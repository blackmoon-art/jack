"""股票图表工具：stock_chart。

生成含成交量子图的股价走势图（PNG），支持 line 和 candle 两种类型。
A 股数据走腾讯 K线 API，美股/港股走 yfinance。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .stock_quote import StockQuote

_PERIOD_DAYS = {'1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '3y': 1095, '5y': 1825}


class StockChart:
    """股票图表 — StockUnified 的内部 helper，不直接暴露工具。"""

    def __init__(self, work_dir: str, charts_dir: str = ""):
        self.work_dir = work_dir
        self._charts_dir = charts_dir
        self._quote = StockQuote(work_dir, charts_dir=charts_dir)

    def stock_chart(self, symbol: str, period: str = "3mo", chart_type: str = "line") -> str:
        """生成股票走势图（含成交量子图）。同股票同日自动缓存。"""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        clean, is_a = self._quote._parse_stock_symbol(symbol)
        days = _PERIOD_DAYS.get(period, 90)

        # 保存到 charts_dir
        if self._charts_dir:
            charts_dir = self._charts_dir
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            charts_dir = str(web_static / "charts")
        os.makedirs(charts_dir, exist_ok=True)
        today = datetime.now().strftime('%Y%m%d')
        filename = f"{clean}_{period}_{chart_type}_{today}.png"
        filepath = os.path.join(charts_dir, filename)
        # 清理旧缓存（同股票同周期不同日期）
        for f in os.listdir(charts_dir):
            if f.startswith(f"{clean}_{period}_{chart_type}_") and f != filename:
                try:
                    os.remove(os.path.join(charts_dir, f))
                except OSError:
                    pass
        if os.path.exists(filepath):
            url = f"/charts/{filename}"
            return f"Chart (cached): {url}\n![{clean}]({url})"

        try:
            if is_a:
                klines = self._quote._tencent_klines(clean, days=days)
                if not klines:
                    return f"Error: No data for A-share '{clean}'"
                closes = [k['close'] for k in klines]
                opens = [k['open'] for k in klines]
                highs = [k['high'] for k in klines]
                lows = [k['low'] for k in klines]
                volumes = [k['volume'] for k in klines]
                date_labels = [k['date'] for k in klines]
            else:
                import yfinance as yf
                hist = yf.Ticker(clean).history(period=period)
                if hist.empty:
                    return f"Error: No data for '{clean}'"
                closes = hist['Close'].tolist()
                opens = hist['Open'].tolist()
                highs = hist['High'].tolist()
                lows = hist['Low'].tolist()
                volumes = hist['Volume'].tolist()
                date_labels = [d.strftime('%Y-%m-%d') for d in hist.index]
        except Exception as e:
            return f"Error: {e}"

        dates = list(range(len(closes)))

        # 双子图：价格 + 成交量
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8),
                                         gridspec_kw={'height_ratios': [3, 1]},
                                         sharex=True)

        if chart_type == "candle":
            from matplotlib.patches import Rectangle
            for i in range(len(dates)):
                color = 'red' if closes[i] >= opens[i] else 'green'
                ax1.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8)
                bottom = min(opens[i], closes[i])
                height = max(abs(closes[i] - opens[i]), 0.001)
                ax1.add_patch(Rectangle((i - 0.3, bottom), 0.6, height,
                                        facecolor=color, edgecolor=color))
        else:
            ax1.plot(dates, closes, color='#2196F3', linewidth=1.5)
            ax1.fill_between(dates, closes, alpha=0.1, color='#2196F3')

        # 成交量柱状图
        colors = ['red' if closes[i] >= opens[i] else 'green' for i in range(len(dates))]
        ax2.bar(dates, volumes, color=colors, alpha=0.7, width=0.8)

        step = max(1, len(dates) // 10)
        ax2.set_xticks(dates[::step])
        ax2.set_xticklabels(date_labels[::step], rotation=45, ha='right')

        ax1.set_title(f"{clean} ({period})", fontsize=14, fontweight='bold')
        ax1.set_ylabel('Price', fontsize=12)
        ax1.grid(True, alpha=0.3)
        ax2.set_ylabel('Volume', fontsize=10)
        ax2.grid(True, alpha=0.3)
        fig.tight_layout()

        fig.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close(fig)
        url = f"/charts/{filename}"
        return f"Chart saved: {url}\n![{clean}]({url})"
