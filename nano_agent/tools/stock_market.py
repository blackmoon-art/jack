"""股票大盘工具：stock_market, stock_market_us。

数据源（国内可达，无需 Key）：
  - 大盘指数: 腾讯行情 API (qt.gtimg.cn)
  - 行业板块: 新浪行业板块 API
"""

import json
import re
import urllib.request

_PERIOD_DAYS = {'1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '3y': 1095, '5y': 1825}


class StockMarket:
    # 工具注册声明
    TOOLS = [
        ("stock_market", "Get A-share market overview: major indices + sector rankings (top/bottom 5).", "stock_market",
         {}, []),
        ("stock_market_us", "Get US stock market overview: S&P 500, Dow Jones, Nasdaq indices + major movers.", "stock_market_us",
         {}, []),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        self.work_dir = work_dir
        self._charts_dir = charts_dir

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
        data = json.loads(m.group(1))

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
