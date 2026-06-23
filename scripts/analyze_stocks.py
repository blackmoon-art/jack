import json, sys, os, math, urllib.request
from datetime import datetime, timedelta

def get_kline_data(symbol, period='1y'):
    period_map = {'1mo': 'M', '3mo': 'Q', '6mo': '2Q', '1y': 'Y', '3y': '3Y', '5y': '5Y'}
    p = period_map.get(period, 'Y')
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},{p},,,,qfq"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('utf-8'))
        return data
    except Exception as e:
        print(f"Error: {e}")
        return None

def extract_kline(data):
    if not data or 'data' not in data:
        return None
    d = data['data']
    for key in d:
        if isinstance(d[key], dict):
            for k2, v2 in d[key].items():
                if isinstance(v2, list) and len(v2) > 0 and isinstance(v2[0], list):
                    return v2
    return None

print("=" * 70)
print("📊 茅台(600519) vs 上证指数(000001) — 完整数据分析")
print("=" * 70)

results = {}
for symbol, name in [('600519', '茅台'), ('000001', '上证指数')]:
    print(f"\n{'='*60}")
    print(f"📈 {name} ({symbol})")
    print(f"{'='*60}")
    
    data = get_kline_data(symbol, '1y')
    kline = extract_kline(data)
    
    if kline:
        print(f"  ✅ 获取到 {len(kline)} 条日线数据")
        dates = [row[0] for row in kline]
        print(f"  日期范围: {dates[0]} ~ {dates[-1]}")
        print(f"  首条: {kline[0]}")
        print(f"  末条: {kline[-1]}")
        
        gaps = []
        for i in range(1, len(dates)):
            d1 = datetime.strptime(dates[i-1], '%Y-%m-%d')
            d2 = datetime.strptime(dates[i], '%Y-%m-%d')
            diff = (d2 - d1).days
            if diff > 5:
                gaps.append((dates[i-1], dates[i], diff))
        
        print(f"  📍 数据断层(>5天): {len(gaps)} 处")
        for g in gaps:
            print(f"     ⚠️ {g[0]} → {g[1]} ({g[2]}天)")
        
        closes = [float(row[2]) for row in kline]
        returns = []
        valid_dates = []
        for i in range(1, len(closes)):
            r = (closes[i] - closes[i-1]) / closes[i-1] * 100
            returns.append(r)
            valid_dates.append(dates[i])
        
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return)**2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        positive_days = sum(1 for r in returns if r > 0)
        negative_days = sum(1 for r in returns if r < 0)
        
        ann_return = mean_return * 252
        ann_vol = std_dev * math.sqrt(252)
        sharpe = ann_return / ann_vol if ann_vol > 0 else 0
        
        peak = closes[0]
        max_dd = 0
        max_dd_start = dates[0]
        max_dd_end = dates[0]
        temp_start = dates[0]
        for i in range(1, len(closes)):
            if closes[i] > peak:
                peak = closes[i]
                temp_start = dates[i]
            dd = (closes[i] - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd
                max_dd_start = temp_start
                max_dd_end = dates[i]
        
        results[name] = {
            'mean_return': mean_return,
            'std_dev': std_dev,
            'ann_return': ann_return,
            'ann_vol': ann_vol,
            'sharpe': sharpe,
            'max_dd': max_dd,
            'max_dd_start': max_dd_start,
            'max_dd_end': max_dd_end,
            'positive_days': positive_days,
            'negative_days': negative_days,
            'total_days': len(returns),
            'start_price': closes[0],
            'end_price': closes[-1],
            'total_return': (closes[-1] - closes[0]) / closes[0] * 100,
            'gaps': gaps,
            'dates_range': (dates[0], dates[-1])
        }
        
        print(f"\n  📊 基本统计:")
        print(f"     起始价格: {closes[0]:.2f}")
        print(f"     结束价格: {closes[-1]:.2f}")
        print(f"     期间涨跌幅: {(closes[-1]-closes[0])/closes[0]*100:+.2f}%")
        print(f"     日均收益率: {mean_return:+.4f}%")
        print(f"     日收益率标准差: {std_dev:.4f}%")
        print(f"     年化收益率: {ann_return:+.2f}%")
        print(f"     年化波动率: {ann_vol:.2f}%")
        print(f"     夏普比率: {sharpe:.3f}")
        print(f"     最大回撤: {max_dd:.2f}% ({max_dd_start} ~ {max_dd_end})")
        print(f"     上涨天数: {positive_days}/{len(returns)} ({positive_days/len(returns)*100:.1f}%)")
        print(f"     下跌天数: {negative_days}/{len(returns)} ({negative_days/len(returns)*100:.1f}%)")
        
        if len(gaps) > 0:
            total_gap_days = sum(g[2] for g in gaps)
            print(f"\n  ⚠️ 数据缺失说明:")
            print(f"     共缺失约 {total_gap_days} 天数据")
        
        # Try weekly data
        print(f"\n  🔄 尝试获取周线数据...")
        data_w = get_kline_data(symbol, '3mo')
        kline_w = extract_kline(data_w)
        if kline_w and len(kline_w) > 10:
            print(f"     周线数据: {len(kline_w)} 条")
            print(f"     范围: {kline_w[0][0]} ~ {kline_w[-1][0]}")
    else:
        print(f"  ❌ 获取数据失败")

print("\n" + "=" * 70)
print("📋 茅台 vs 上证指数 对比总结")
print("=" * 70)

if '茅台' in results and '上证指数' in results:
    m = results['茅台']
    s = results['上证指数']
    
    print(f"\n{'指标':<20} {'茅台':>15} {'上证指数':>15}")
    print(f"{'─'*50}")
    print(f"{'起始价':<20} {m['start_price']:>12.2f}元 {s['start_price']:>12.2f}元")
    print(f"{'结束价':<20} {m['end_price']:>12.2f}元 {s['end_price']:>12.2f}元")
    print(f"{'总涨跌幅':<20} {m['total_return']:>+12.2f}% {s['total_return']:>+12.2f}%")
    print(f"{'年化收益率':<20} {m['ann_return']:>+12.2f}% {s['ann_return']:>+12.2f}%")
    print(f"{'年化波动率':<20} {m['ann_vol']:>12.2f}% {s['ann_vol']:>12.2f}%")
    print(f"{'夏普比率':<20} {m['sharpe']:>12.3f} {s['sharpe']:>12.3f}")
    print(f"{'最大回撤':<20} {m['max_dd']:>12.2f}% {s['max_dd']:>12.2f}%")
    print(f"{'上涨占比':<20} {m['positive_days']/m['total_days']*100:>11.1f}% {s['positive_days']/s['total_days']*100:>11.1f}%")
    print(f"{'有效天数':<20} {m['total_days']:>12}天 {s['total_days']:>12}天")
    
    print(f"\n📌 风险调整后分析:")
    if m['sharpe'] > s['sharpe']:
        print(f"   🏆 茅台夏普({m['sharpe']:.3f}) > 大盘({s['sharpe']:.3f})，风险调整收益更优")
    else:
        print(f"   🏆 大盘夏普({s['sharpe']:.3f}) > 茅台({m['sharpe']:.3f})，风险调整收益更优")
    
    if abs(m['max_dd']) < abs(s['max_dd']):
        print(f"   🛡️ 茅台回撤({m['max_dd']:.2f}%) < 大盘({s['max_dd']:.2f}%)，防御性更强")
    else:
        print(f"   🛡️ 大盘回撤({s['max_dd']:.2f}%) < 茅台({m['max_dd']:.2f}%)，防御性更强")
    
    print(f"\n📌 缺失数据标注:")
    print(f"   茅台: {len(m['gaps'])} 处断层")
    print(f"   大盘: {len(s['gaps'])} 处断层")
    print(f"   以上统计使用可用数据计算，缺失时段已跳过")
