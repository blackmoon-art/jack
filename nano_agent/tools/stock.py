"""股票工具：stock_info, stock_history, stock_chart, stock_indicators, stock_market。

数据源（国内可达，无需 Key）：
  - 实时行情: 腾讯行情 API (qt.gtimg.cn)
  - K线数据: 腾讯 K线 API (web.ifzq.gtimg.cn)
  - 大盘/板块: 腾讯行情 + 新浪行业板块
  - 美股/港股: Yahoo Finance → 腾讯行情 fallback

技术指标（纯计算，无额外数据源）：
  - MA (5/10/20/60)
  - RSI (14)
  - MACD (12/26/9)
  - BOLL (20,2)
"""

import json as _json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from .stock_indicators import StockIndicators
from pathlib import Path

_PERIOD_DAYS = {'1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '3y': 1095, '5y': 1825}


class Stock:
    # 工具注册声明
    TOOLS = [
        ("stock_info", "Get real-time stock quote. A-shares via Tencent API (600519, 000001), US/HK via Yahoo Finance with Tencent fallback (AAPL, 0700.HK).", "stock_info",
         {"symbol": {"type": "string", "description": "Stock symbol (e.g. 600519, AAPL, 0700.HK)"}},
         ["symbol"]),
        ("stock_history", "Get historical stock prices. A-shares via Tencent K-line API, US/HK via yfinance. Period: 1mo/3mo/6mo/1y/3y/5y.", "stock_history",
         {"symbol": {"type": "string", "description": "Stock symbol"},
          "period": {"type": "string", "description": "Time period: 1mo, 3mo, 6mo, 1y, 3y, 5y (default: 1mo)"}},
         ["symbol"]),
        ("stock_chart", "Generate stock price chart (PNG) with volume subplot. A-shares via Tencent API, US/HK via yfinance. Supports line and candle charts.", "stock_chart",
         {"symbol": {"type": "string", "description": "Stock symbol"},
          "period": {"type": "string", "description": "Time period: 1mo, 3mo, 6mo, 1y, 3y, 5y (default: 3mo)"},
          "chart_type": {"type": "string", "description": "Chart type: line or candle (default: line)"}},
         ["symbol"]),
        ("stock_indicators", "Calculate technical indicators: MA (5/10/20/60), RSI (14), MACD (12/26/9), Bollinger Bands (20,2).", "stock_indicators",
         {"symbol": {"type": "string", "description": "Stock symbol"},
          "period": {"type": "string", "description": "Time period: 1mo, 3mo, 6mo, 1y, 3y, 5y (default: 6mo)"}},
         ["symbol"]),
        ("stock_market", "Get A-share market overview: major indices + sector rankings (top/bottom 5).", "stock_market",
         {}, []),
        ("stock_market_us", "Get US stock market overview: S&P 500, Dow Jones, Nasdaq indices + major movers.", "stock_market_us",
         {}, []),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        self.work_dir = work_dir
        self._charts_dir = charts_dir

    # ════════════════════════════════════════════════════
    #  实时行情
    # ════════════════════════════════════════════════════

    def stock_info(self, symbol: str) -> str:
        """获取股票实时行情。A 股走腾讯行情，美股/港股走 Yahoo → 腾讯 fallback。"""
        raw = symbol.upper().strip()
        clean, is_a = self._parse_stock_symbol(raw)

        if is_a:
            return self._stock_info_tencent_a(clean)
        return self._stock_info_yahoo(raw)

    def _stock_info_tencent_a(self, symbol: str) -> str:
        """A 股实时行情 — 腾讯行情 API。"""
        try:
            prefix = 'sh' if symbol.startswith(('6', '9')) else 'sz'
            code = f"{prefix}{symbol}"
            data = self._tencent_quote(code)
            if not data:
                return f"Error: A-share symbol not found — '{symbol}'"

            name = data.get('name', symbol)
            price = data.get('price', 0)
            prev_close = data.get('prev_close', 0)
            change = data.get('change', 0)
            change_pct = data.get('change_pct', 0)
            high = data.get('high', 0)
            low = data.get('low', 0)
            volume = data.get('volume', 0)
            turnover = data.get('turnover', 0)

            sign = "+" if change >= 0 else ""
            arrow = "📈" if change >= 0 else "📉"
            vol_str = f"{volume/1e4:.0f}万手" if volume > 1e4 else f"{volume:.0f}手"
            turnover_str = f"{turnover/1e4:.1f}亿" if turnover > 1e4 else f"{turnover:.0f}万"

            return (
                f"{arrow} {name} ({symbol}) — A股\n"
                f"💵 价格: {price:.2f} CNY\n"
                f"📊 涨跌: {sign}{change:.2f} ({sign}{change_pct:.2f}%)\n"
                f"📈 最高: {high:.2f} | 📉 最低: {low:.2f}\n"
                f"📦 成交量: {vol_str} | 💰 成交额: {turnover_str}\n"
                f"🕐 昨收: {prev_close:.2f} CNY"
            )
        except Exception as e:
            return f"Error: A-share lookup failed for '{symbol}' — {e}"

    def _stock_info_yahoo(self, symbol: str) -> str:
        """美股/港股实时行情 — Yahoo Finance → 腾讯行情 fallback。"""
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

            result = data.get("chart", {}).get("result", [])
            if not result:
                raise ValueError("No data")

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
        except Exception:
            pass

        # Fallback: 腾讯行情
        try:
            if '.' in symbol.upper():
                base, mkt = symbol.upper().split('.', 1)
                if mkt == 'HK':
                    qt_code = f'hk{base}'
                else:
                    qt_code = f'us{base}'
            else:
                qt_code = f'us{symbol.upper()}'

            data = self._tencent_quote(qt_code, is_foreign=True)
            if not data:
                return f"Error: Stock symbol not found — '{symbol}'"

            name = data.get('name', symbol)
            price = data.get('price', 0)
            prev_close = data.get('prev_close', 0)
            change = data.get('change', 0)
            change_pct = data.get('change_pct', 0)
            high = data.get('high', 0)
            low = data.get('low', 0)
            volume = data.get('volume', 0)
            currency = data.get('currency', 'USD')

            sign = "+" if change >= 0 else ""
            arrow = "📈" if change >= 0 else "📉"

            return (
                f"{arrow} {name} ({symbol})\n"
                f"💵 价格: {price:.2f} {currency}\n"
                f"📊 涨跌: {sign}{change:.2f} ({sign}{change_pct:.2f}%)\n"
                f"📈 最高: {high:.2f} | 📉 最低: {low:.2f}\n"
                f"📦 成交量: {volume:,.0f}\n"
                f"🕐 昨收: {prev_close:.2f} {currency}"
            )
        except Exception as e:
            return f"Error: Stock lookup failed for '{symbol}' — {e}"

    # ════════════════════════════════════════════════════
    #  历史行情
    # ════════════════════════════════════════════════════

    def stock_history(self, symbol: str, period: str = "1mo") -> str:
        """获取股票历史行情。A 股走腾讯 K线 API，美股/港股走 yfinance。"""
        clean, is_a = self._parse_stock_symbol(symbol)
        days = _PERIOD_DAYS.get(period, 30)

        try:
            if is_a:
                klines = self._tencent_klines(clean, days=days)
                if not klines:
                    return f"Error: No data for A-share '{clean}'"
                return self._format_tencent_history(klines, clean, period)
            else:
                import yfinance as yf
                hist = yf.Ticker(clean).history(period=period)
                if hist.empty:
                    return f"Error: No data for '{clean}'"
                return self._format_yfinance_history(hist, clean, period)
        except Exception as e:
            return f"Error: {e}"

    def _format_tencent_history(self, klines: list, symbol: str, period: str,
                                 max_rows: int = 16) -> str:
        """格式化腾讯 K线数据。"""
        header = f"{'日期':<12} {'开盘':>10} {'收盘':>10} {'最高':>10} {'最低':>10} {'涨跌幅':>8} {'成交额':>14}"

        total = len(klines)
        if total <= max_rows:
            show = klines
            truncated = False
        else:
            show = klines[:max_rows // 2] + klines[-(max_rows // 2):]
            truncated = True

        lines = [f"📊 {symbol} 最近 {period} 历史行情 (A股前复权)\n", header]
        for kl in show:
            date = kl['date']
            open_p = kl['open']
            close_p = kl['close']
            high_p = kl['high']
            low_p = kl['low']
            vol = kl['volume']
            # 计算涨跌幅
            if open_p > 0:
                pct = (close_p - open_p) / open_p * 100
                pct_str = f"{pct:+.2f}%"
            else:
                pct_str = "N/A"
            vol_str = f"{vol/1e8:.2f}亿" if vol > 1e8 else f"{vol/1e4:.0f}万"
            lines.append(f"{date:<12} {open_p:>10.2f} {close_p:>10.2f} {high_p:>10.2f} {low_p:>10.2f} {pct_str:>8} {vol_str:>14}")

        if truncated:
            closes = [k['close'] for k in klines]
            highs = [k['high'] for k in klines]
            lows = [k['low'] for k in klines]
            lines.append(f"\n... ({total - max_rows} rows omitted) ...")
            lines.append(f"Summary: mean close {sum(closes)/len(closes):.2f}, "
                         f"high {max(highs):.2f}, low {min(lows):.2f}, total rows {total}")

        return '\n'.join(lines)

    @staticmethod
    def _format_yfinance_history(df, symbol: str, period: str, max_rows: int = 16) -> str:
        """格式化 yfinance 历史数据。"""
        header = f"{'Date':<12} {'Open':>10} {'Close':>10} {'High':>10} {'Low':>10} {'Volume':>12}"

        total = len(df)
        if total <= max_rows:
            rows = df
            truncated = False
        else:
            rows = df.head(max_rows // 2)._append(df.tail(max_rows // 2))
            truncated = True

        lines = [f"📊 {symbol} 最近 {period} 历史行情 (US/HK)\n", header]
        for _, row in rows.iterrows():
            lines.append(f"{row.name.strftime('%Y-%m-%d'):<12} {row['Open']:>10.2f} {row['Close']:>10.2f} {row['High']:>10.2f} {row['Low']:>10.2f} {int(row['Volume']):>12,}")

        if truncated:
            lines.append(f"\n... ({total - max_rows} rows omitted) ...")
            lines.append(f"Summary: mean close {df['Close'].mean():.2f}, high {df['High'].max():.2f}, low {df['Low'].min():.2f}, total rows {total}")

        return '\n'.join(lines)

    # ════════════════════════════════════════════════════
    #  技术指标
    # ════════════════════════════════════════════════════

    def stock_indicators(self, symbol: str, period: str = "6mo") -> str:
        """计算技术指标：MA/RSI/MACD/BOLL。
        A 股走腾讯 K线，美股/港股走 yfinance。
        """
        clean, is_a = self._parse_stock_symbol(symbol)
        days = _PERIOD_DAYS.get(period, 180)

        try:
            if is_a:
                klines = self._tencent_klines(clean, days=days)
                if not klines:
                    return f"Error: No data for A-share '{clean}'"
                closes = [k['close'] for k in klines]
            else:
                import yfinance as yf
                hist = yf.Ticker(clean).history(period=period)
                if hist.empty:
                    return f"Error: No data for '{clean}'"
                closes = hist['Close'].tolist()
        except Exception as e:
            return f"Error: {e}"

        return StockIndicators.format_report(clean, period, closes)

    def stock_market(self) -> str:
        """获取 A 股大盘指数 + 行业板块涨跌排行。"""
        lines = []

        # ── 大盘指数 ──
        indices = {
            'sh000001': '上证指数',
            'sz399001': '深证成指',
            'sz399006': '创业板指',
        }
        codes = ','.join(indices.keys())
        try:
            url = f"http://qt.gtimg.cn/q={codes}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode('gbk', errors='replace')

            lines.append("🏛️ 大盘指数")
            lines.append(f"{'指数':<10} {'现价':>12} {'涨跌':>10} {'涨幅':>10}")
            lines.append("─" * 45)

            for line in raw.strip().split(';'):
                line = line.strip()
                if not line or '="' not in line:
                    continue
                _, val = line.split('="', 1)
                val = val.rstrip('";')
                fields = val.split('~')
                if len(fields) > 32 and fields[1]:
                    name = fields[1]
                    price = fields[3]
                    try:
                        change = float(fields[31])
                        pct = float(fields[32])
                    except (ValueError, IndexError):
                        change = 0
                        pct = 0
                    arrow = "📈" if pct >= 0 else "📉"
                    lines.append(f"{arrow} {name:<8} {price:>12} {change:>+10.2f} {pct:>+9.2f}%")
        except Exception as e:
            lines.append(f"大盘数据获取失败: {e}")

        # ── 行业板块 ──
        lines.append("\n🏭 行业板块涨跌")
        lines.append(f"{'板块':<10} {'涨幅':>8}")
        lines.append("─" * 22)
        try:
            sectors = self._sina_sectors()
            # 涨幅前5
            lines.append("📈 涨幅前5:")
            for name, pct in sectors[:5]:
                lines.append(f"  {name:<10} {pct:>+7.2f}%")
            # 跌幅前5
            lines.append("📉 跌幅前5:")
            for name, pct in sectors[-5:]:
                lines.append(f"  {name:<10} {pct:>+7.2f}%")
        except Exception as e:
            lines.append(f"板块数据获取失败: {e}")

        return '\n'.join(lines)

    def stock_market_us(self) -> str:
        """获取美股大盘指数 + 涨幅最大的个股。"""
        lines = []

        # ── 三大指数（用腾讯行情 API）──
        indices = {
            'usDJI': '道琼斯',
            'usIXIC': '纳斯达克',
            'usINX': 'S&P 500',
        }
        codes = ','.join(indices.keys())
        lines.append("🇺🇸 美股大盘指数")
        lines.append(f"{'指数':<12} {'现价':>12} {'涨跌':>10} {'涨幅':>10}")
        lines.append("─" * 48)

        try:
            url = f"http://qt.gtimg.cn/q={codes}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode('gbk', errors='replace')

            for line in raw.strip().split(';'):
                line = line.strip()
                if not line or '="' not in line:
                    continue
                _, val = line.split('="', 1)
                val = val.rstrip('";')
                fields = val.split('~')
                if len(fields) > 32 and fields[1]:
                    name = fields[1]
                    price = fields[3]
                    try:
                        pct = float(fields[32])
                        price_val = float(price.replace(',', ''))
                        change = price_val * pct / (100 + pct) if (100 + pct) != 0 else 0
                    except (ValueError, IndexError):
                        pct = 0
                        change = 0
                    arrow = "📈" if pct >= 0 else "📉"
                    lines.append(f"{arrow} {name:<10} {price:>12} {change:>+10.2f} {pct:>+9.2f}%")
        except Exception as e:
            lines.append(f"大盘指数获取失败: {e}")

        # ── 热门个股（用腾讯行情 API 查美股）──
        hot_stocks = {
            'usAAPL': 'Apple',
            'usMSFT': 'Microsoft',
            'usNVDA': 'NVIDIA',
            'usTSLA': 'Tesla',
            'usAMZN': 'Amazon',
            'usGOOG': 'Alphabet',
            'usMETA': 'Meta',
        }
        lines.append("\n🔥 热门美股")
        lines.append(f"{'股票':<14} {'现价':>10} {'涨幅':>10}")
        lines.append("─" * 38)

        codes = ','.join(hot_stocks.keys())
        try:
            url = f"http://qt.gtimg.cn/q={codes}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode('gbk', errors='replace')

            for line in raw.strip().split(';'):
                line = line.strip()
                if not line or '="' not in line:
                    continue
                _, val = line.split('="', 1)
                val = val.rstrip('";')
                fields = val.split('~')
                if len(fields) > 32 and fields[1]:
                    name = fields[1]
                    price = fields[3]
                    try:
                        pct = float(fields[32])
                    except (ValueError, IndexError):
                        pct = 0
                    arrow = "📈" if pct >= 0 else "📉"
                    lines.append(f"{arrow} {name:<12} {price:>10} {pct:>+9.2f}%")
        except Exception as e:
            lines.append(f"热门美股数据获取失败: {e}")

        return '\n'.join(lines)

    def _sina_sectors(self) -> list[tuple[str, float]]:
        """从新浪获取行业板块涨跌排行。返回 [(name, change_pct)] 按涨幅降序。"""
        url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('gbk', errors='replace')

        m = re.search(r'=\s*(\{.*\})', raw, re.DOTALL)
        if not m:
            return []
        data = _json.loads(m.group(1))

        sectors = []
        for k, v in data.items():
            parts = v.split(',')
            if len(parts) > 5:
                name = parts[1]
                try:
                    chg_pct = float(parts[5])
                    sectors.append((name, chg_pct))
                except (ValueError, IndexError):
                    continue

        sectors.sort(key=lambda x: x[1], reverse=True)
        return sectors

    # ════════════════════════════════════════════════════
    #  K线图
    # ════════════════════════════════════════════════════

    def stock_chart(self, symbol: str, period: str = "3mo", chart_type: str = "line") -> str:
        """生成股票走势图（含成交量子图）。同股票同日自动缓存。"""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        clean, is_a = self._parse_stock_symbol(symbol)
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
                klines = self._tencent_klines(clean, days=days)
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

    # ════════════════════════════════════════════════════
    #  数据源：腾讯行情 API
    # ════════════════════════════════════════════════════

    def _tencent_quote(self, code: str, is_foreign: bool = False) -> dict:
        """
        腾讯实时行情。
        code: sh600519 / sz000001 / usAAPL / hk0700
        """
        url = f"http://qt.gtimg.cn/q={code}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('gbk', errors='replace')

        if f'v_{code}="' not in raw:
            return {}

        val = raw.split('="', 1)[1].rstrip('";')
        fields = val.split('~')

        if len(fields) < 35:
            return {}

        result = {
            'name': fields[1] if len(fields) > 1 else '',
            'price': float(fields[3]) if fields[3] else 0,
            'prev_close': float(fields[4]) if fields[4] else 0,
            'change': float(fields[31]) if len(fields) > 31 and fields[31] else 0,
            'change_pct': float(fields[32]) if len(fields) > 32 and fields[32] else 0,
            'high': float(fields[33]) if len(fields) > 33 and fields[33] else 0,
            'low': float(fields[34]) if len(fields) > 34 and fields[34] else 0,
            'volume': float(fields[6]) if len(fields) > 6 and fields[6] else 0,
            'turnover': float(fields[37]) if len(fields) > 37 and fields[37] else 0,
        }

        if is_foreign and len(fields) > 82 and fields[82]:
            result['currency'] = fields[82]
        elif not is_foreign:
            result['currency'] = 'CNY'
        else:
            result['currency'] = 'USD'

        return result

    def _tencent_klines(self, symbol: str, days: int = 90) -> list[dict]:
        """
        腾讯 K线 API（前复权）。

        Args:
            symbol: 纯数字代码如 '000001', '600519'
            days: 需要的天数

        Returns:
            [{"date", "open", "close", "high", "low", "volume"}]
        """
        prefix = 'sh' if symbol.startswith(('6', '9')) else 'sz'
        code = f"{prefix}{symbol}"

        start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        end = datetime.now().strftime('%Y-%m-%d')
        # 请求更多K线以确保足够
        count = min(days + 30, 500)

        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={code},day,{start},{end},{count},qfq"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode('utf-8'))

        code_data = data.get('data', {}).get(code, {})
        raw_klines = code_data.get('qfqday', code_data.get('day', []))

        result = []
        for kl in raw_klines:
            # 格式: [date, open, close, high, low, volume]
            if len(kl) < 6:
                continue
            result.append({
                'date': kl[0],
                'open': float(kl[1]),
                'close': float(kl[2]),
                'high': float(kl[3]),
                'low': float(kl[4]),
                'volume': float(kl[5]),
            })
        return result

    # ════════════════════════════════════════════════════
    #  通用工具
    # ════════════════════════════════════════════════════

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
        """格式化历史数据（yfinance DataFrame 兼容）。"""
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
                lines.append(f"Summary: mean close {df['Close'].mean():.2f}, high {df['High'].max():.2f}, low {df['Low'].min():.2f}, total rows {total}")

        return '\n'.join(lines)
