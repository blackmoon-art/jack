"""
航班查询工具 — 搜索航班信息。

API 后端：Amadeus Self-Service（免费注册，测试额度充足）
注册地址: https://developers.amadeus.com/register
获取 API Key + API Secret 后填入 .env:
  AMADEUS_API_KEY=xxx
  AMADEUS_API_SECRET=xxx

若未配置 Amadeus，则用 web_search 兜底。
"""

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from typing import Optional

from .observation import Observation

logger = logging.getLogger("nano_agent.tools.flight")

# Amadeus token 缓存
_token_cache: dict = {"token": "", "expires_at": 0}
_TOKEN_LOCK = threading.Lock()


def _get_amadeus_token(api_key: str, api_secret: str) -> str:
    """获取 Amadeus OAuth token，带缓存。"""
    now = time.time()
    with _TOKEN_LOCK:
        if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
            return _token_cache["token"]

    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": api_secret,
    }).encode()

    req = urllib.request.Request(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            token = result["access_token"]
            expires_in = result.get("expires_in", 1799)
            with _TOKEN_LOCK:
                _token_cache["token"] = token
                _token_cache["expires_at"] = now + expires_in
            return token
    except Exception as e:
        logger.warning(f"Amadeus auth failed: {e}")
        return ""


def _format_city(code: str) -> str:
    """常见机场代码 → 中文名映射（精简版）。"""
    _CITIES = {
        "PEK": "北京首都", "PKX": "北京大兴", "SHA": "上海虹桥", "PVG": "上海浦东",
        "CAN": "广州白云", "SZX": "深圳宝安", "CTU": "成都双流", "TFU": "成都天府",
        "HGH": "杭州萧山", "NNG": "南宁吴圩", "KMG": "昆明长水", "XIY": "西安咸阳",
        "CKG": "重庆江北", "WUH": "武汉天河", "CSX": "长沙黄花", "NKG": "南京禄口",
        "TAO": "青岛流亭", "XMN": "厦门高崎", "HAK": "海口美兰", "SYX": "三亚凤凰",
        "HND": "东京羽田", "NRT": "东京成田", "ICN": "首尔仁川", "BKK": "曼谷素万那普",
        "SIN": "新加坡樟宜", "HKG": "香港国际", "KIX": "大阪关西", "LHR": "伦敦希思罗",
        "CDG": "巴黎戴高乐", "JFK": "纽约肯尼迪", "LAX": "洛杉矶", "SFO": "旧金山",
    }
    # 支持城市名 → 代码的模糊匹配
    _NAME_TO_CODE = {
        "北京": "PEK", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
        "成都": "CTU", "杭州": "HGH", "南宁": "NNG", "昆明": "KMG",
        "西安": "XIY", "重庆": "CKG", "武汉": "WUH", "长沙": "CSX",
        "南京": "NKG", "东京": "NRT", "首尔": "ICN", "曼谷": "BKK",
        "新加坡": "SIN", "香港": "HKG", "伦敦": "LHR", "巴黎": "CDG",
        "纽约": "JFK", "洛杉矶": "LAX", "旧金山": "SFO",
    }

    code_upper = code.strip().upper()
    if code_upper in _CITIES:
        return _CITIES[code_upper]
    # 中文名 → 代码
    if code in _NAME_TO_CODE:
        return _NAME_TO_CODE[code]
    return code_upper


def _resolve_airport(city: str) -> str:
    """将城市名解析为机场代码。"""
    _NAME_TO_CODE = {
        "北京": "PEK", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
        "成都": "CTU", "杭州": "HGH", "南宁": "NNG", "昆明": "KMG",
        "西安": "XIY", "重庆": "CKG", "武汉": "WUH", "长沙": "CSX",
        "南京": "NKG", "青岛": "TAO", "厦门": "XMN", "海口": "HAK",
        "三亚": "SYX", "大连": "DLC", "沈阳": "SHE", "郑州": "CGO",
        "东京": "NRT", "首尔": "ICN", "曼谷": "BKK", "新加坡": "SIN",
        "香港": "HKG", "大阪": "KIX", "伦敦": "LHR", "巴黎": "CDG",
        "纽约": "JFK", "洛杉矶": "LAX", "旧金山": "SFO",
    }
    upper = city.strip().upper()
    # 如果已经是标准机场代码
    if len(upper) == 3 and upper.isalpha():
        return upper
    return _NAME_TO_CODE.get(city.strip(), upper)


class Flight:
    TOOLS = [
        ("search_flights",
         "Search for flights between two cities on a given date. "
         "Returns flight number, departure/arrival times, duration, and price. "
         "Supports both airport codes (PEK, SHA) and city names (北京, 上海).",
         "search_flights",
         {"origin": {"type": "string",
                     "description": "Departure city or airport code (e.g. '北京', 'PEK', 'Shanghai')"},
          "destination": {"type": "string",
                          "description": "Arrival city or airport code (e.g. '上海', 'SHA')"},
          "date": {"type": "string",
                   "description": "Flight date in YYYY-MM-DD format (e.g. '2026-07-01')"},
          "passengers": {"type": "integer", "description": "Number of passengers (default: 1)"}},
         ["origin", "destination", "date"]),
    ]

    def __init__(self, amadeus_api_key: str = "", amadeus_api_secret: str = "",
                 work_dir: str = ""):
        self.api_key = amadeus_api_key or os.getenv("AMADEUS_API_KEY", "")
        self.api_secret = amadeus_api_secret or os.getenv("AMADEUS_API_SECRET", "")
        self.work_dir = work_dir

    def search_flights(self, origin: str, destination: str, date: str,
                       passengers: int = 1) -> Observation:
        """
        搜索航班。优先使用 Amadeus API，未配置则返回引导信息。
        """
        origin_code = _resolve_airport(origin)
        dest_code = _resolve_airport(destination)

        # 校验日期格式
        try:
            flight_date = datetime.strptime(date.strip(), "%Y-%m-%d")
            if flight_date.date() < datetime.now().date():
                return Observation(
                    tool_name="search_flights",
                    result=f"日期 {date} 已过期，请选择今天或之后的日期。",
                    success=False,
                    args={"origin": origin, "destination": destination, "date": date},
                )
        except ValueError:
            return Observation(
                tool_name="search_flights",
                result=f"日期格式错误: '{date}'，请使用 YYYY-MM-DD 格式（如 2026-07-01）。",
                success=False,
                args={"origin": origin, "destination": destination, "date": date},
            )

        if self.api_key and self.api_secret:
            return self._search_amadeus(origin_code, dest_code, date, passengers)
        else:
            return self._search_web_fallback(origin_code, dest_code, date)

    # ── Amadeus API ──────────────────────────────────────

    def _search_amadeus(self, origin: str, destination: str, date: str,
                        passengers: int) -> Observation:
        """通过 Amadeus Self-Service API 搜索航班。"""
        token = _get_amadeus_token(self.api_key, self.api_secret)
        if not token:
            return Observation(
                tool_name="search_flights",
                result="Amadeus API 认证失败，请检查 AMADEUS_API_KEY 和 AMADEUS_API_SECRET。",
                success=False,
                args={"origin": origin, "destination": destination, "date": date},
            )

        params = urllib.parse.urlencode({
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": date,
            "adults": str(max(1, min(passengers, 9))),
            "max": "10",
            "currencyCode": "CNY",
        })

        req = urllib.request.Request(
            f"https://test.api.amadeus.com/v2/shopping/flight-offers?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                return self._format_amadeus_result(data, origin, destination, date)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="ignore")[:500]
            logger.warning(f"Amadeus API error {e.code}: {err_body}")
            return Observation(
                tool_name="search_flights",
                result=f"航班查询失败 (HTTP {e.code})。可能原因：无可用航班、机场代码无效、或日期超出范围。",
                success=False,
                args={"origin": origin, "destination": destination, "date": date},
            )
        except Exception as e:
            logger.warning(f"Amadeus request failed: {e}")
            return Observation(
                tool_name="search_flights",
                result=f"航班查询网络错误: {e}",
                success=False,
                args={"origin": origin, "destination": destination, "date": date},
            )

    def _format_amadeus_result(self, data: dict, origin: str,
                               destination: str, date: str) -> Observation:
        """格式化 Amadeus 返回的航班数据。"""
        offers = data.get("data", [])
        if not offers:
            return Observation(
                tool_name="search_flights",
                result=f"未找到 {origin} → {destination} 在 {date} 的直飞/转机航班。",
                success=True,
                args={"origin": origin, "destination": destination, "date": date},
            )

        origin_name = _format_city(origin)
        dest_name = _format_city(destination)

        lines = [f"✈️ **{origin_name}({origin}) → {dest_name}({destination})** — {date}"]
        lines.append(f"共找到 {len(offers)} 个航班:\n")

        for i, offer in enumerate(offers[:10], 1):
            itinerary = offer.get("itineraries", [{}])[0]
            segments = itinerary.get("segments", [{}])
            first_seg = segments[0]
            last_seg = segments[-1]

            departure = first_seg.get("departure", {})
            arrival = last_seg.get("arrival", {})

            dep_time = departure.get("at", "N/A")
            arr_time = arrival.get("at", "N/A")
            airline = first_seg.get("carrierCode", "??")
            flight_no = f"{airline}{first_seg.get('number', '')}"

            # 格式化时间
            dep_display = dep_time.replace("T", " ")[:16] if dep_time != "N/A" else "N/A"
            arr_display = arr_time.replace("T", " ")[:16] if arr_time != "N/A" else "N/A"

            # 价格
            price_info = offer.get("price", {})
            total = price_info.get("grandTotal", "N/A")
            currency = price_info.get("currency", "CNY")

            # 中转信息
            stops = len(segments) - 1
            stop_info = "直飞" if stops == 0 else f"经停 {stops} 站"

            lines.append(
                f"**{i}. {flight_no}** | {stop_info}\n"
                f"   🛫 出发: {dep_display}\n"
                f"   🛬 到达: {arr_display}\n"
                f"   💰 价格: ¥{total} {currency}\n"
            )

        lines.append(f"---\n💡 价格仅供参考，实际以航司官网为准。")
        return Observation(
            tool_name="search_flights",
            result="\n".join(lines),
            success=True,
            args={"origin": origin, "destination": destination, "date": date},
            metadata={"count": len(offers)},
        )

    # ── Web 搜索兜底 ────────────────────────────────────

    def _search_web_fallback(self, origin: str, destination: str,
                             date: str) -> Observation:
        """未配置 API 时通过网页搜索获取航班信息。"""
        origin_name = _format_city(origin)
        dest_name = _format_city(destination)

        # 直接搜航班信息
        query = f"{origin_name}到{dest_name}机票 {date} 航班时刻 价格"
        search_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"

        return Observation(
            tool_name="search_flights",
            result=(
                f"✈️ **{origin_name}({origin}) → {dest_name}({destination})** — {date}\n\n"
                f"🔍 帮你搜航班信息：\n"
                f"[在必应搜索航班]({search_url})\n\n"
                f"💡 **建议查询方式：**\n"
                f"- 携程: https://flights.ctrip.com\n"
                f"- 去哪儿: https://flight.qunar.com\n"
                f"- 飞猪: https://www.fliggy.com\n\n"
                f"📌 你也可以直接告诉我想查的**城市、日期**，我用网页搜索帮你查实时结果。"
            ),
            success=True,
            args={"origin": origin, "destination": destination, "date": date},
        )
