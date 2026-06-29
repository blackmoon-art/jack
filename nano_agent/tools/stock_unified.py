"""统一股票工具 — 将 stock_info / stock_history / stock_indicators /
stock_chart / stock_market / stock_market_us 合并为单个 `stock` 工具。

通过 `action` 参数路由到具体子工具，减少 tool schema 数量（6→1），
每次 LLM 调用节省 ~800 tokens。
"""

from .stock_quote import StockQuote
from .stock_chart import StockChart
from .stock_market import StockMarket


class StockUnified:
    """统一股票工具入口 — action 参数路由到子工具。"""

    TOOLS = [
        ("stock", "Stock market tool. action='info' for real-time quote, 'history' for historical prices, "
         "'indicators' for technical indicators (MA/RSI/MACD/BOLL), 'chart' for price chart (PNG), "
         "'market' for A-share market overview, 'market_us' for US market overview.",
         "stock",
         {"action": {"type": "string", "description": "One of: info, history, indicators, chart, market, market_us"},
          "symbol": {"type": "string", "description": "Stock symbol (e.g. 600519, AAPL, 0700.HK). Required for info/history/indicators/chart."},
          "period": {"type": "string", "description": "Time period for history/chart: 1mo, 3mo, 6mo, 1y, 3y, 5y (default: 1mo for history, 3mo for chart)"},
          "chart_type": {"type": "string", "description": "Chart type for action=chart: line or candle (default: line)"}},
         ["action"]),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        self._quote = StockQuote(work_dir, charts_dir=charts_dir)
        self._chart = StockChart(work_dir, charts_dir=charts_dir)
        self._market = StockMarket(work_dir, charts_dir=charts_dir)

    def stock(self, action: str = "info", symbol: str = "", period: str = "",
              chart_type: str = "", **kwargs) -> str:
        action = (action or "").strip().lower()

        if action == "info":
            if not symbol:
                return "Error: symbol is required for action='info'"
            return self._quote.stock_info(symbol)

        elif action == "history":
            if not symbol:
                return "Error: symbol is required for action='history'"
            return self._quote.stock_history(symbol, period or "1mo")

        elif action == "indicators":
            if not symbol:
                return "Error: symbol is required for action='indicators'"
            return self._quote.stock_indicators(symbol, period or "3mo")

        elif action == "chart":
            if not symbol:
                return "Error: symbol is required for action='chart'"
            return self._chart.stock_chart(
                symbol, period or "3mo", chart_type or "line")

        elif action == "market":
            return self._market.stock_market()

        elif action == "market_us":
            return self._market.stock_market_us()

        else:
            return (f"Error: Unknown action '{action}'. "
                    f"Supported: info, history, indicators, chart, market, market_us")
