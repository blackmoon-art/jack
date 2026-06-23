"""股票技术指标计算：MA, RSI, MACD, Bollinger Bands。

纯数学计算，不依赖外部 API。数据由 Stock 类提供。
"""

import math


class StockIndicators:
    """技术指标计算器 — 无状态，所有方法为 @staticmethod。"""

    @staticmethod
    def sma(data: list, period: int) -> list:
        """简单移动平均。前 period-1 个为 NaN。"""
        result = [float('nan')] * len(data)
        for i in range(period - 1, len(data)):
            result[i] = sum(data[i - period + 1:i + 1]) / period
        return result

    @staticmethod
    def rsi(data: list, period: int = 14) -> list:
        """RSI 指标。"""
        result = [float('nan')] * len(data)
        if len(data) < period + 1:
            return result

        gains, losses = [], []
        for i in range(1, period + 1):
            diff = data[i] - data[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            result[period] = 100.0
        else:
            result[period] = 100.0 - (100.0 / (1 + avg_gain / avg_loss))

        for i in range(period + 1, len(data)):
            diff = data[i] - data[i - 1]
            gain = max(diff, 0)
            loss = max(-diff, 0)
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            if avg_loss == 0:
                result[i] = 100.0
            else:
                result[i] = 100.0 - (100.0 / (1 + avg_gain / avg_loss))

        return result

    @staticmethod
    def macd(data: list, fast: int = 12, slow: int = 26, signal: int = 9):
        """MACD 指标。返回 (macd_line, signal_line, histogram)。"""
        def ema(vals, period):
            result = [float('nan')] * len(vals)
            if len(vals) < period:
                return result
            result[period - 1] = sum(vals[:period]) / period
            k = 2 / (period + 1)
            for i in range(period, len(vals)):
                result[i] = vals[i] * k + result[i - 1] * (1 - k)
            return result

        ema_fast = ema(data, fast)
        ema_slow = ema(data, slow)

        dif = [float('nan')] * len(data)
        for i in range(len(data)):
            if not math.isnan(ema_fast[i]) and not math.isnan(ema_slow[i]):
                dif[i] = ema_fast[i] - ema_slow[i]

        valid_dif = [v for v in dif if not math.isnan(v)]
        dea = ema(valid_dif, signal) if len(valid_dif) >= signal else [float('nan')] * len(data)

        dea_aligned = [float('nan')] * len(data)
        j = 0
        for i in range(len(data)):
            if not math.isnan(dif[i]):
                if j < len(dea):
                    dea_aligned[i] = dea[j]
                    j += 1

        hist = [float('nan')] * len(data)
        for i in range(len(data)):
            if not math.isnan(dif[i]) and not math.isnan(dea_aligned[i]):
                hist[i] = 2 * (dif[i] - dea_aligned[i])

        return dif, dea_aligned, hist

    @staticmethod
    def boll(data: list, period: int = 20, std_dev: int = 2):
        """布林带。返回 (upper, mid, lower)。"""
        upper = [float('nan')] * len(data)
        mid = [float('nan')] * len(data)
        lower = [float('nan')] * len(data)

        for i in range(period - 1, len(data)):
            window = data[i - period + 1:i + 1]
            avg = sum(window) / period
            variance = sum((x - avg) ** 2 for x in window) / period
            std = math.sqrt(variance)
            mid[i] = avg
            upper[i] = avg + std_dev * std
            lower[i] = avg - std_dev * std

        return upper, mid, lower

    @staticmethod
    def format_report(symbol: str, period: str, closes: list) -> str:
        """生成完整的技术指标报告。"""
        if len(closes) < 26:
            return f"Error: Not enough data ({len(closes)} bars) for indicators (need ≥26)"

        ma5 = StockIndicators.sma(closes, 5)
        ma10 = StockIndicators.sma(closes, 10)
        ma20 = StockIndicators.sma(closes, 20)
        ma60 = StockIndicators.sma(closes, 60)
        rsi14 = StockIndicators.rsi(closes, 14)
        macd_line, signal_line, histogram = StockIndicators.macd(closes)
        boll_upper, boll_mid, boll_lower = StockIndicators.boll(closes)

        last = len(closes) - 1
        price = closes[last]

        lines = [f"📊 {symbol} 技术指标 ({period})\n"]
        lines.append(f"💰 当前价: {price:.2f}\n")

        # MA
        lines.append("📈 移动平均线 (MA)")
        lines.append(f"  MA5:  {ma5[last]:.2f}  {'↑ 多头' if price > ma5[last] else '↓ 空头'}")
        lines.append(f"  MA10: {ma10[last]:.2f}  {'↑ 多头' if price > ma10[last] else '↓ 空头'}")
        lines.append(f"  MA20: {ma20[last]:.2f}  {'↑ 多头' if price > ma20[last] else '↓ 空头'}")
        if ma60[last] is not None and not math.isnan(ma60[last]):
            lines.append(f"  MA60: {ma60[last]:.2f}  {'↑ 多头' if price > ma60[last] else '↓ 空头'}")
        else:
            lines.append(f"  MA60: N/A (数据不足)")

        above = sum(1 for m in [ma5[last], ma10[last], ma20[last]] if price > m)
        trend = "多头排列 🐂" if above >= 2 else "空头排列 🐻"
        lines.append(f"  → 趋势: {trend}")

        # RSI
        lines.append("\n📉 RSI (14)")
        rsi_val = rsi14[last]
        if rsi_val is not None and not math.isnan(rsi_val):
            if rsi_val > 70:
                status = "⚠️ 超买"
            elif rsi_val < 30:
                status = "⚠️ 超卖"
            else:
                status = "正常"
            lines.append(f"  RSI14: {rsi_val:.1f}  {status}")
        else:
            lines.append(f"  RSI14: N/A")

        # MACD
        lines.append("\n📊 MACD (12/26/9)")
        if macd_line[last] is not None and not math.isnan(macd_line[last]):
            m = macd_line[last]
            s = signal_line[last]
            h = histogram[last]
            cross = "金叉 ↑" if m > s else "死叉 ↓"
            lines.append(f"  DIF:   {m:.3f}")
            lines.append(f"  DEA:   {s:.3f}")
            lines.append(f"  MACD:  {h:.3f}")
            lines.append(f"  → 信号: {cross}")
        else:
            lines.append(f"  MACD: N/A (数据不足)")

        # BOLL
        lines.append("\n📐 布林带 (20,2)")
        if boll_upper[last] is not None and not math.isnan(boll_upper[last]):
            u = boll_upper[last]
            m_val = boll_mid[last]
            l = boll_lower[last]
            lines.append(f"  上轨: {u:.2f}")
            lines.append(f"  中轨: {m_val:.2f}")
            lines.append(f"  下轨: {l:.2f}")
            if price >= u:
                pos = "触及上轨 ⚠️ 超买"
            elif price <= l:
                pos = "触及下轨 ⚠️ 超卖"
            else:
                pct_pos = (price - l) / (u - l) * 100 if u != l else 50
                pos = f"带内 ({pct_pos:.0f}% 位置)"
            lines.append(f"  → 位置: {pos}")
        else:
            lines.append(f"  BOLL: N/A (数据不足)")

        return '\n'.join(lines)
