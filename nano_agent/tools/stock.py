"""股票工具：stock_info, stock_history, stock_chart。

数据来源：
  - A 股: akshare (前复权日线)
  - 美股/港股: yfinance
  - 实时行情: Yahoo Finance v8 chart API
"""

import json as _json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

_PERIOD_DAYS = {'1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '3y': 1095, '5y': 1825}


class Stock:
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    # ── 实时行情 ──────────────────────────────────────

    def stock_info(self, symbol: str) -> str:
        """获取股票实时行情。数据来源 Yahoo Finance，免费无需 Key。"""
        symbol = symbol.upper().strip()
        try:
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
                "?interval=1d&range=1mo"
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return f"Error: Stock symbol not found — '{symbol}' (HTTP {e.code})"
        except urllib.error.URLError as e:
            return f"Error: Cannot reach Yahoo Finance — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        result = data.get("chart", {}).get("result", [])
        if not result:
            return f"Error: No data for symbol '{symbol}'"

        meta = result[0].get("meta", {})

        price = meta.get("regularMarketPrice", "N/A")
        prev_close = meta.get("previousClose", "N/A")
        change = round(price - prev_close, 2) if isinstance(price, (int, float)) and isinstance(prev_close, (int, float)) else "N/A"
        change_pct = round(change / prev_close * 100, 2) if isinstance(change, (int, float)) and prev_close else "N/A"
        high = meta.get("regularMarketDayHigh", "N/A")
        low = meta.get("regularMarketDayLow", "N/A")
        volume = meta.get("regularMarketVolume", "N/A")
        name = meta.get("longName") or meta.get("shortName") or symbol
        currency = meta.get("currency", "USD")
        exchange = meta.get("exchangeName", "")

        sign = "+" if isinstance(change, (int, float)) and change >= 0 else ""
        arrow = "📈" if isinstance(change, (int, float)) and change >= 0 else "📉"

        return (
            f"{arrow} {name} ({symbol}) — {exchange}\n"
            f"💵 价格: {price} {currency}\n"
            f"📊 涨跌: {sign}{change} ({sign}{change_pct}%)\n"
            f"📈 最高: {high} | 📉 最低: {low}\n"
            f"📦 成交量: {volume}\n"
            f"🕐 昨收: {prev_close} {currency}"
        )

    # ── 历史行情 ──────────────────────────────────────

    @staticmethod
    def _parse_stock_symbol(symbol: str) -> tuple[str, bool]:
        """解析股票代码。返回 (clean_symbol, is_a_share)。"""
        symbol = symbol.strip()
        is_a = (symbol.isdigit() and len(symbol) == 6) or symbol.endswith(('.SS', '.SZ'))
        if symbol.endswith(('.SS', '.SZ')):
            symbol = symbol.split('.')[0]
        return symbol, is_a

    @staticmethod
    def _format_history(df, symbol: str, period: str, is_a: bool, max_rows: int = 16) -> str:
        """格式化历史数据。>max_rows 时只显示头尾 + 统计。"""
        if is_a:
            header = f"{'日期':<12} {'开盘':>10} {'收盘':>10} {'最高':>10} {'最低':>10} {'涨跌幅':>8} {'成交额':>14}"
        else:
            header = f"{'Date':<12} {'Open':>10} {'Close':>10} {'High':>10} {'Low':>10} {'Volume':>12}"

        total = len(df)
        if total <= max_rows:
            rows = df
            truncated = False
        else:
            rows = df.head(max_rows // 2)._append(df.tail(max_rows // 2))
            truncated = True

        lines = [f"📊 {symbol} 最近 {period} 历史行情 ({'A股前复权' if is_a else 'US/HK'})\n", header]
        for _, row in rows.iterrows():
            if is_a:
                date = str(row.get('日期', ''))[:10]
                pct = row.get('涨跌幅', '')
                amt = row.get('成交额', '')
                pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else str(pct)
                amt_str = f"{amt/1e8:.2f}亿" if isinstance(amt, (int, float)) and amt > 0 else str(amt)
                lines.append(f"{date:<12} {row.get('开盘',''):>10} {row.get('收盘',''):>10} {row.get('最高',''):>10} {row.get('最低',''):>10} {pct_str:>8} {amt_str:>14}")
            else:
                lines.append(f"{row.name.strftime('%Y-%m-%d'):<12} {row['Open']:>10.2f} {row['Close']:>10.2f} {row['High']:>10.2f} {row['Low']:>10.2f} {int(row['Volume']):>12,}")

        if truncated:
            lines.append(f"\n... ({total - max_rows} rows omitted) ...")
            if is_a:
                lines.append(f"Summary: mean close {df['收盘'].mean():.2f}, high {df['最高'].max()}, low {df['最低'].min()}, total rows {total}")
            else:
                lines.append(f"Summary: mean close {df['Close'].mean():.2f}, high {df['High'].max()}, low {df['Low'].min()}, total rows {total}")

        return '\n'.join(lines)

    def stock_history(self, symbol: str, period: str = "1mo") -> str:
        """获取股票历史行情。支持 A 股（akshare）和美股/港股（yfinance）。"""
        symbol, is_a = self._parse_stock_symbol(symbol)
        days = _PERIOD_DAYS.get(period, 30)

        try:
            if is_a:
                import akshare as ak
                start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
                end = datetime.now().strftime('%Y%m%d')
                df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                         start_date=start, end_date=end, adjust="qfq")
                if df is None or df.empty:
                    return f"Error: No data for A-share '{symbol}'"
                return self._format_history(df, symbol, period, is_a=True)
            else:
                import yfinance as yf
                hist = yf.Ticker(symbol).history(period=period)
                if hist.empty:
                    return f"Error: No data for '{symbol}'"
                return self._format_history(hist, symbol, period, is_a=False)
        except Exception as e:
            return f"Error: {e}"

    def stock_chart(self, symbol: str, period: str = "3mo", chart_type: str = "line") -> str:
        """生成股票走势图并保存为图片。同股票同周期自动缓存。"""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        symbol, is_a = self._parse_stock_symbol(symbol)
        days = _PERIOD_DAYS.get(period, 90)

        charts_dir = os.path.join(self.work_dir, "charts")
        os.makedirs(charts_dir, exist_ok=True)
        filename = f"{symbol}_{period}_{chart_type}.png"
        filepath = os.path.join(charts_dir, filename)
        if os.path.exists(filepath):
            return f"Chart (cached): {filepath}"

        try:
            if is_a:
                import akshare as ak
                start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
                end = datetime.now().strftime('%Y%m%d')
                df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                         start_date=start, end_date=end, adjust="qfq")
                if df is None or df.empty:
                    return f"Error: No data for A-share '{symbol}'"
                closes, opens = df['收盘'].tolist(), df['开盘'].tolist()
                highs, lows = df['最高'].tolist(), df['最低'].tolist()
                date_labels = [str(d)[:10] for d in df['日期'].tolist()]
            else:
                import yfinance as yf
                hist = yf.Ticker(symbol).history(period=period)
                if hist.empty:
                    return f"Error: No data for '{symbol}'"
                closes, opens = hist['Close'].tolist(), hist['Open'].tolist()
                highs, lows = hist['High'].tolist(), hist['Low'].tolist()
                date_labels = [d.strftime('%Y-%m-%d') for d in hist.index]
        except Exception as e:
            return f"Error: {e}"

        dates = list(range(len(closes)))
        fig, ax = plt.subplots(figsize=(12, 6))

        if chart_type == "candle":
            from matplotlib.patches import Rectangle
            for i in range(len(dates)):
                color = 'red' if closes[i] >= opens[i] else 'green'
                ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8)
                bottom = min(opens[i], closes[i])
                height = max(abs(closes[i] - opens[i]), 0.001)
                ax.add_patch(Rectangle((i - 0.3, bottom), 0.6, height,
                                       facecolor=color, edgecolor=color))
        else:
            ax.plot(dates, closes, color='#2196F3', linewidth=1.5)
            ax.fill_between(dates, closes, alpha=0.15, color='#2196F3')

        step = max(1, len(dates) // 10)
        ax.set_xticks(dates[::step])
        ax.set_xticklabels(date_labels[::step], rotation=45, ha='right')
        ax.set_title(f"{symbol} ({period})", fontsize=14, fontweight='bold')
        ax.set_ylabel('Price', fontsize=12)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        fig.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close(fig)
        return f"Chart saved: {filepath}"
