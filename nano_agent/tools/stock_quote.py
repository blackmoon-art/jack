"""股票行情工具：stock_info, stock_history, stock_indicators。

数据源（国内可达，无需 Key）：
  - 实时行情: 腾讯行情 API (qt.gtimg.cn)
  - K线数据: 腾讯 K线 API (web.ifzq.gtimg.cn)
  - 美股/港股: Yahoo Finance → 腾讯行情 fallback

技术指标委托 stock_indicators.StockIndicators。
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from .stock_indicators import StockIndicators

_PERIOD_DAYS = {'1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '3y': 1095, '5y': 1825}


class StockQuote:
    # 工具注册声明
    TOOLS = [
        ("stock_info", "Get real-time stock quote. A-shares via Tencent API (600519, 000001), US/HK via Yahoo Finance with Tencent fallback (AAPL, 0700.HK).", "stock_info",
         {"symbol": {"type": "string", "description": "Stock symbol (e.g. 600519, AAPL, 0700.HK)"}},
         ["symbol"]),
        ("stock_history", "Get historical stock prices. A-shares via Tencent K-line API, US/HK via yfinance. Period: 1mo/3mo/6mo/1y/3y/5y.", "stock_history",
         {"symbol": {"type": "string", "description": "Stock symbol"},
          "period": {"type": "string", "description": "Time period: 1mo, 3mo, 6mo, 1y, 3y, 5y (default: 1mo)"}},
         ["symbol"]),
        ("stock_indicators", "Calculate technical indicators: MA (5/10/20/60), RSI (14), MACD (12/26/9), Bollinger Bands (20,2).", "stock_indicators",
         {"symbol": {"type": "string", "description": "Stock symbol"},
          "period": {"type": "string", "description": "Time period: 1mo, 3mo, 6mo, 1y, 3y, 5y (default: 6mo)"}},
         ["symbol"]),
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
                data = json.loads(resp.read().decode("utf-8"))

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
            data = json.loads(resp.read().decode('utf-8'))

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


