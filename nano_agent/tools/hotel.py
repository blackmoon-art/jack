"""
酒店查询工具 — 搜索酒店房源。

API 后端：Amadeus Self-Service（免费注册，与机票共用同一套 Key）
注册地址: https://developers.amadeus.com/register
已在 .env 配置机票 Key 后，酒店自动可用：
  AMADEUS_API_KEY=xxx
  AMADEUS_API_SECRET=xxx
"""

import json
import logging
import os
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from typing import Optional

from .observation import Observation
from .flight import _get_amadeus_token  # 复用机票的 token 逻辑

logger = logging.getLogger("nano_agent.tools.hotel")

# 城市名 → IATA 城市代码（Amadeus 酒店搜索用城市代码，非机场代码）
_CITY_CODE_MAP = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
    "成都": "CTU", "杭州": "HGH", "南宁": "NNG", "昆明": "KMG",
    "西安": "XIY", "重庆": "CKG", "武汉": "WUH", "长沙": "CSX",
    "南京": "NKG", "青岛": "TAO", "厦门": "XMN", "海口": "HAK",
    "三亚": "SYX", "大连": "DLC", "沈阳": "SHE", "郑州": "CGO",
    "苏州": "SZV", "天津": "TSN", "哈尔滨": "HRB", "贵阳": "KWE",
    "东京": "TYO", "首尔": "SEL", "曼谷": "BKK", "新加坡": "SIN",
    "香港": "HKG", "大阪": "OSA", "伦敦": "LON", "巴黎": "PAR",
    "纽约": "NYC", "洛杉矶": "LAX", "旧金山": "SFO", "悉尼": "SYD",
}

_CITY_NAMES = {v: k for k, v in _CITY_CODE_MAP.items()}


def _resolve_city(city: str) -> str:
    """将城市名解析为 IATA 城市代码。"""
    upper = city.strip().upper()
    if len(upper) == 3 and upper.isalpha():
        return upper
    return _CITY_CODE_MAP.get(city.strip(), upper)


class Hotel:
    TOOLS = [
        ("search_hotels",
         "Search for hotels in a city for given check-in/check-out dates. "
         "Returns hotel name, rating, price per night, and address. "
         "Supports both city codes (BJS) and Chinese/English city names (北京, Tokyo).",
         "search_hotels",
         {"city": {"type": "string",
                   "description": "City name or code (e.g. '北京', 'Tokyo', 'BJS')"},
          "check_in": {"type": "string",
                       "description": "Check-in date in YYYY-MM-DD format (e.g. '2026-07-01')"},
          "check_out": {"type": "string",
                        "description": "Check-out date in YYYY-MM-DD format (e.g. '2026-07-03')"},
          "adults": {"type": "integer", "description": "Number of adults (default: 1)"},
          "max_results": {"type": "integer", "description": "Max results to return (default: 5, max: 10)"}},
         ["city", "check_in", "check_out"]),
    ]

    def __init__(self, amadeus_api_key: str = "", amadeus_api_secret: str = "",
                 work_dir: str = ""):
        self.api_key = amadeus_api_key or os.getenv("AMADEUS_API_KEY", "")
        self.api_secret = amadeus_api_secret or os.getenv("AMADEUS_API_SECRET", "")

    def search_hotels(self, city: str, check_in: str, check_out: str,
                      adults: int = 1, max_results: int = 5) -> Observation:
        """搜索酒店。优先 Amadeus API，未配置则返回引导。"""
        city_code = _resolve_city(city)
        city_name = _CITY_NAMES.get(city_code, city_code)

        # 校验日期
        for label, d in [("入住", check_in), ("退房", check_out)]:
            try:
                parsed = datetime.strptime(d.strip(), "%Y-%m-%d")
                if parsed.date() < datetime.now().date():
                    return Observation(
                        tool_name="search_hotels",
                        result=f"{label}日期 {d} 已过期，请选择今天或之后的日期。",
                        success=False,
                        args={"city": city, "check_in": check_in, "check_out": check_out},
                    )
            except ValueError:
                return Observation(
                    tool_name="search_hotels",
                    result=f"{label}日期格式错误: '{d}'，请使用 YYYY-MM-DD 格式。",
                    success=False,
                    args={"city": city, "check_in": check_in, "check_out": check_out},
                )

        if datetime.strptime(check_out.strip(), "%Y-%m-%d") <= datetime.strptime(check_in.strip(), "%Y-%m-%d"):
            return Observation(
                tool_name="search_hotels",
                result="退房日期必须晚于入住日期。",
                success=False,
                args={"city": city, "check_in": check_in, "check_out": check_out},
            )

        if self.api_key and self.api_secret:
            return self._search_amadeus(city_code, check_in, check_out, adults, max_results)
        else:
            return self._search_web_fallback(city_code, city_name, check_in, check_out)

    # ── Amadeus API ──────────────────────────────────────

    def _search_amadeus(self, city_code: str, check_in: str, check_out: str,
                        adults: int, max_results: int) -> Observation:
        """通过 Amadeus Hotel List API 搜索酒店。"""
        token = _get_amadeus_token(self.api_key, self.api_secret)
        if not token:
            return Observation(
                tool_name="search_hotels",
                result="Amadeus API 认证失败，请检查 AMADEUS_API_KEY。",
                success=False,
                args={"city": city_code, "check_in": check_in, "check_out": check_out},
            )

        params = urllib.parse.urlencode({
            "cityCode": city_code,
            "radius": "20",
            "radiusUnit": "KM",
            "ratings": "3,4,5",
        })

        # Step 1: 获取酒店列表
        req = urllib.request.Request(
            f"https://test.api.amadeus.com/v1/reference-data/locations/hotels/by-city?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                hotels_data = json.loads(resp.read())
        except Exception as e:
            return self._search_web_fallback(city_code, "", check_in, check_out)

        hotels = hotels_data.get("data", [])
        if not hotels:
            return Observation(
                tool_name="search_hotels",
                result=f"未找到 {_CITY_NAMES.get(city_code, city_code)} 的酒店。",
                success=True,
                args={"city": city_code, "check_in": check_in, "check_out": check_out},
            )

        max_results = min(max(max_results, 1), 10)
        hotels = hotels[:max_results]

        # Step 2: 对每个酒店查报价
        hotel_ids = [h.get("hotelId") for h in hotels if h.get("hotelId")]
        offers = self._get_hotel_offers(token, hotel_ids, check_in, check_out, adults)
        offer_map = {}
        if offers:
            for o in offers.get("data", []):
                hid = o.get("hotel", {}).get("hotelId", "")
                if hid:
                    offer_map[hid] = o

        city_name = _CITY_NAMES.get(city_code, city_code)
        lines = [f"🏨 **{city_name}** — {check_in} 至 {check_out}，{adults}人"]
        lines.append(f"共找到 {len(hotels)} 家酒店:\n")

        for i, h in enumerate(hotels, 1):
            name = h.get("name", "未知")
            geo = h.get("geoCode", {})
            dist = h.get("distance", {})

            dist_text = f"{dist.get('value', '?')}km 距市中心" if dist.get("value") else ""

            rating = "暂无评分"
            offer = offer_map.get(h.get("hotelId"), {})
            price_text = "暂无报价"
            currency = "CNY"
            if offer:
                o = offer.get("offers", [{}])[0] if offer.get("offers") else {}
                price = o.get("price", {})
                if price:
                    price_text = f"¥{price.get('total', '?')}"
                    currency = price.get("currency", "CNY")

            lines.append(
                f"**{i}. {name}** ⭐{rating} | {dist_text}\n"
                f"   📍 坐标: {geo.get('latitude', '?')}, {geo.get('longitude', '?')}\n"
                f"   💰 {price_text} {currency}/晚\n"
            )

        lines.append(f"---\n💡 价格仅供参考，实际以酒店/携程官网为准。")
        return Observation(
            tool_name="search_hotels",
            result="\n".join(lines),
            success=True,
            args={"city": city_code, "check_in": check_in, "check_out": check_out},
            metadata={"count": len(hotels)},
        )

    def _get_hotel_offers(self, token: str, hotel_ids: list[str],
                          check_in: str, check_out: str, adults: int) -> dict | None:
        """批量获取酒店报价。"""
        if not hotel_ids:
            return None

        # Amadeus Hotel Offers API — 一次最多传 10 个 hotelId
        params = {
            "hotelIds": ",".join(hotel_ids[:10]),
            "adults": str(max(1, min(adults, 9))),
            "checkInDate": check_in,
            "checkOutDate": check_out,
            "currency": "CNY",
        }
        query_str = urllib.parse.urlencode(params, doseq=True)

        req = urllib.request.Request(
            f"https://test.api.amadeus.com/v3/shopping/hotel-offers?{query_str}",
            headers={"Authorization": f"Bearer {token}"},
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"Hotel offers fetch failed: {e}")
            return None

    # ── Web 搜索兜底 ────────────────────────────────────

    def _search_web_fallback(self, city_code: str, city_name: str,
                             check_in: str, check_out: str) -> Observation:
        """未配置 API 时的引导说明。"""
        if not city_name:
            city_name = _CITY_NAMES.get(city_code, city_code)

        return Observation(
            tool_name="search_hotels",
            result=(
                f"🏨 **{city_name}** — {check_in} 至 {check_out}\n\n"
                f"⚠️ 未配置 Amadeus API，无法获取实时酒店数据。\n\n"
                f"**配置方法（与机票共用）：**\n"
                f"1. 注册 Amadeus: https://developers.amadeus.com/register\n"
                f"2. 在 .env 中添加:\n"
                f"   AMADEUS_API_KEY=你的key\n"
                f"   AMADEUS_API_SECRET=你的secret\n"
                f"3. 重启服务 → 机票和酒店同时可用\n\n"
                f"**临时替代：** 去携程/美团/飞猪查 {city_name} 酒店 {check_in}-{check_out}"
            ),
            success=True,
            args={"city": city_code, "check_in": check_in, "check_out": check_out},
        )
