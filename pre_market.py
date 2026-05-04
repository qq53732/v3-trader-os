#!/usr/bin/env python3
"""
V3+开盘前决策系统
三板块(创业板/科创板/北交所) 大盘情绪 → 板块热度 → 股票筛选 → V3信号
用法: python3 pre_market.py [--output obsidian|terminal]
"""
import urllib.request, json, sys, os
from datetime import datetime

# ========== 配置 ==========
OBSIDIAN_PATH = "/Volumes/T/每日简报"
PANORAMA_FILE = "/Users/apple/.cola/outputs/全景监控/三板块250MA全景_latest.csv"
EASTMONEY_HEADERS = {'Referer': 'https://quote.eastmoney.com'}

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers=EASTMONEY_HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

# ========== 第一级: 大盘情绪 ==========
def market_sentiment():
    """拉三板块指数数据，判断涨跌比和趋势"""
    indices = {
        '创业板': '399006',   # 创业板指
        '科创板': '000688',   # 科创50
        '北证50': '899050',   # 北证50
    }
    
    results = {}
    total_up, total_down = 0, 0
    
    # 创业板涨跌比 (从板块成分)
    for board, code in indices.items():
        try:
            # 指数本身
            url = f'http://push2his.eastmoney.com/api/qt/stock/get?secid=0.{code}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f170'
            data = fetch(url)
            d = data['data']
            price = d.get('f43', 0) / 100 if d.get('f43') else 0
            change_pct = d.get('f170', 0) / 100 if d.get('f170') else 0
            high = d.get('f44', 0) / 100 if d.get('f44') else 0
            low = d.get('f45', 0) / 100 if d.get('f45') else 0
            volume = d.get('f47', 0)
            
            results[board] = {
                'price': price,
                'change_pct': change_pct,
                'volume': volume,
            }
        except Exception as e:
            results[board] = {'error': str(e)}
    
    # 全局涨跌比: 分别拉三个板块的成分
    for board_label, market_code in [
        ('创业板涨跌', 'm:0+t:80'),      # 创业板
        ('科创板涨跌', 'm:1+t:23'),      # 科创板
    ]:
        try:
            url = f'http://80.push2his.eastmoney.com/api/qt/clist/get?pn=1&pz=5000&po=1&np=1&fltt=2&invt=2&fid=f3&fs={market_code}&fields=f3,f12,f14'
            data = fetch(url, timeout=12)
            for item in data['data']['diff']:
                chg = item.get('f3', 0)
                if chg > 0: total_up += 1
                elif chg < 0: total_down += 1
        except: pass
    
    # BSE
    try:
        url = f'http://80.push2his.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:81+s:2048&fields=f3,f12'
        data = fetch(url, timeout=12)
        for item in data['data']['diff']:
            chg = item.get('f3', 0)
            if chg > 0: total_up += 1
            elif chg < 0: total_down += 1
    except: pass
    
    ad_ratio = total_up / max(1, total_down)
    
    # 判断情绪
    cyb_price = results.get('创业板', {}).get('price', 0)
    cyb_chg = results.get('创业板', {}).get('change_pct', 0)
    
    if ad_ratio > 1.2 and cyb_chg > 0:
        sentiment = '积极'
        signal = '🟢 只买不卖'
        position = '满仓'
    elif ad_ratio < 0.8 or (cyb_chg < -1 and ad_ratio < 0.9):
        sentiment = '低迷'
        signal = '🔴 只卖不买'
        position = '≤30%'
    else:
        sentiment = '中性'
        signal = '🟡 正常交易'
        position = '正常'
    
    return {
        'sentiment': sentiment,
        'signal': signal,
        'position': position,
        'ad_ratio': round(ad_ratio, 2),
        'total_up': total_up,
        'total_down': total_down,
        'indices': results,
    }


# ========== 第二级: 板块热度 ==========
def sector_heatmap():
    """拉申万二级行业/概念板块涨幅排名"""
    sectors = []
    
    # 概念板块
    try:
        url = 'http://80.push2his.eastmoney.com/api/qt/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3&fields=f2,f3,f4,f12,f14'
        data = fetch(url, timeout=10)
        for item in data['data']['diff']:
            sectors.append({
                'name': item['f14'],
                'code': item['f12'],
                'change_pct': item.get('f3', 0),
                'type': '概念'
            })
    except: pass
    
    # 行业板块  
    try:
        url = 'http://80.push2his.eastmoney.com/api/qt/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f2,f3,f4,f12,f14'
        data = fetch(url, timeout=10)
        for item in data['data']['diff']:
            sectors.append({
                'name': item['f14'],
                'code': item['f12'],
                'change_pct': item.get('f3', 0),
                'type': '行业'
            })
    except: pass
    
    sectors.sort(key=lambda x: -x['change_pct'])
    return sectors[:20]


# ========== 第三级+第四级: 股票筛选 ==========
def stock_screening(top_sectors, sentiment):
    """
    从全景数据中筛选:
    - Tier1: 站上250MA, 偏离<50%, 在Top5板块内 → 主升浪回踩候选
    - Tier2: 250MA±10%, 在Top5板块内 → 低洼启动候选
    """
    if not os.path.exists(PANORAMA_FILE):
        return None, "全景数据文件未就绪"
    
    tier1 = []  # 主升浪中(0-50%偏离), 回踩候选
    tier2 = []  # 低洼(-10%~+10%), 启动前
    
    # 取Top5板块名(简化为关键词匹配)
    top5_keywords = set()
    for s in top_sectors[:5]:
        name = s['name']
        # 简化: 取前两个字或常见关键词
        for kw in [name[:2], name[:3], name[:4]]:
            if len(kw) >= 2:
                top5_keywords.add(kw)
    
    try:
        with open(PANORAMA_FILE) as f:
            header = f.readline().strip().split(',')
            for line in f:
                if not line.strip(): continue
                parts = line.strip().split(',')
                if len(parts) < 7: continue
                
                code = parts[0]
                name = parts[1]
                board = parts[2]
                close = float(parts[3])
                ma250 = float(parts[4])
                dev_pct = float(parts[5])
                
                # 板块匹配 (简单: 看行业分类)
                sector = parts[6] if len(parts) > 6 else ''
                
                # 不匹配热门板块就跳过
                # matched = any(kw in sector or kw in name for kw in top5_keywords)
                # 暂时全量筛选, 后续细化
                
                # Tier1: 主升浪中但不过热 (0% < dev < 50%)
                if 0 < dev_pct < 50:
                    tier1.append((code, name, board, close, ma250, dev_pct, sector))
                
                # Tier2: 低洼 (-5% < dev < 5%)
                elif -5 < dev_pct <= 5:
                    tier2.append((code, name, board, close, ma250, dev_pct, sector))
    
    except Exception as e:
        return None, f"读取全景文件失败: {e}"
    
    # 排序: Tier1按偏离度升序(越接近MA250越好,回踩买入), Tier2也一样
    tier1.sort(key=lambda x: x[5])
    tier2.sort(key=lambda x: abs(x[5]))
    
    return {
        'tier1': tier1[:30],
        'tier2': tier2[:30],
        'tier1_count': len(tier1),
        'tier2_count': len(tier2),
    }, None


# ========== V3信号检查(快速版) ==========
def v3_check(code, name, close, ma250, dev_pct):
    """简化版V3: 检查SAR和MACD方向(需要K线数据暂不拉取,占位)"""
    return {
        'code': code,
        'name': name,
        'close': close,
        'ma250': ma250,
        'dev_pct': dev_pct,
        'v3_signal': '待验证',  # 需要K线数据
    }


# ========== 主流程 ==========
def main():
    now = datetime.now()
    print(f"\n{'='*60}")
    print(f"  V3+ 开盘前决策  {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    # 第一级: 大盘情绪
    print("\n[1/4] 大盘情绪...")
    sentiment = market_sentiment()
    print(f"  涨跌比: {sentiment['ad_ratio']} (↑{sentiment['total_up']} ↓{sentiment['total_down']})")
    for board, info in sentiment['indices'].items():
        if 'error' not in info:
            print(f"  {board}: {info['price']:.1f} ({info['change_pct']:+.2f}%)")
    print(f"\n  ╔══════════════════════╗")
    print(f"  ║  情绪: {sentiment['sentiment']:<10} ║")
    print(f"  ║  动作: {sentiment['signal']:<12} ║")
    print(f"  ║  仓位: {sentiment['position']:<12} ║")
    print(f"  ╚══════════════════════╝")
    
    # 第二级: 板块热度
    print("\n[2/4] 板块热度 Top10...")
    sectors = sector_heatmap()
    for i, s in enumerate(sectors[:10]):
        print(f"  {i+1:>2}. {s['name']:<10} {s['change_pct']:>+6.2f}% ({s['type']})")
    
    # 第三级+第四级: 股票筛选
    print("\n[3/4] 股票筛选...")
    stocks, error = stock_screening(sectors, sentiment)
    
    if error:
        print(f"  ⚠️ {error}")
    elif stocks:
        print(f"  Tier1(主升浪回踩): {stocks['tier1_count']}只, 展示Top15:")
        for i, (code, name, board, close, ma250, dev, sector) in enumerate(stocks['tier1'][:15]):
            board_label = '创' if board == 'cyb' else ('科' if board == 'kcb' else '北')
            print(f"  {i+1:>2}. [{board_label}] {name:<6} {code} C={close:.1f} MA250={ma250:.1f} 偏离{dev:+.1f}%")
        
        print(f"\n  Tier2(低洼启动): {stocks['tier2_count']}只, 展示Top15:")
        for i, (code, name, board, close, ma250, dev, sector) in enumerate(stocks['tier2'][:15]):
            board_label = '创' if board == 'cyb' else ('科' if board == 'kcb' else '北')
            print(f"  {i+1:>2}. [{board_label}] {name:<6} {code} C={close:.1f} MA250={ma250:.1f} 偏离{dev:+.1f}%")
    
    # 第四级: V3信号 (暂占位)
    print("\n[4/4] V3信号: 需K线数据,当前全景文件满足后自动接入")
    
    # 输出
    print(f"\n{'='*60}")
    print(f"  总结: 大盘{sentiment['sentiment']} | 仓位{sentiment['position']} | V3信号待接入")
    print(f"{'='*60}\n")
    
    # 生成网站HTML
    generate_dashboard_html(sentiment, sectors, stocks)
    
    return sentiment, sectors, stocks


def generate_dashboard_html(sentiment, sectors, stocks):
    """注入数据到仪表盘HTML模板"""
    template_path = '/Users/apple/.cola/outputs/V3交易系统/dashboard_template.html'
    output_path = '/Users/apple/.cola/outputs/V3交易系统/index.html'
    
    with open(template_path) as f:
        html = f.read()
    
    # 构建数据JSON
    stocks_data = None
    if stocks:
        stocks_data = {
            'tier1': [[s[0], s[1], s[2], s[3], s[4], s[5], s[6]] for s in (stocks['tier1'][:15] if stocks else [])],
            'tier2': [[s[0], s[1], s[2], s[3], s[4], s[5], s[6]] for s in (stocks['tier2'][:15] if stocks else [])],
            'tier1_count': stocks.get('tier1_count', 0),
            'tier2_count': stocks.get('tier2_count', 0),
        }
    
    data = {
        'sentiment': {
            'sentiment': sentiment['sentiment'],
            'signal': sentiment['signal'],
            'position': sentiment['position'],
            'ad_ratio': sentiment['ad_ratio'],
            'total_up': sentiment['total_up'],
            'total_down': sentiment['total_down'],
            'indices': sentiment['indices'],
        },
        'sectors': [
            {'name': s['name'], 'change_pct': s['change_pct'], 'type': s['type']}
            for s in (sectors[:12] if sectors else [])
        ],
        'stocks': stocks_data,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    
    html = html.replace('__DATA_PLACEHOLDER__', json.dumps(data, ensure_ascii=False))
    
    with open(output_path, 'w') as f:
        f.write(html)
    
    print(f"  🌐 仪表盘已生成: {output_path}")


if __name__ == '__main__':
    main()
