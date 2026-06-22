"""天气工具：get_weather。数据来源 Open-Meteo，免费无需 API Key。"""

import json as _json
import urllib.parse
import urllib.request

_WMO_CODES: dict = {
    0: "晴天 ☀️", 1: "大部晴朗 🌤️", 2: "多云 ⛅", 3: "阴天 ☁️",
    45: "有雾 🌫️", 48: "雾凇 🌫️",
    51: "毛毛雨 🌧️", 53: "毛毛雨 🌧️", 55: "大毛毛雨 🌧️",
    61: "小雨 🌧️", 63: "中雨 🌧️", 65: "大雨 🌧️",
    71: "小雪 ❄️", 73: "中雪 ❄️", 75: "大雪 ❄️", 77: "雪粒 ❄️",
    80: "阵雨 ⛈️", 81: "中阵雨 ⛈️", 82: "大阵雨 ⛈️",
    85: "小阵雪 🌨️", 86: "大阵雪 🌨️",
    95: "雷暴 ⚡", 96: "冰雹雷暴 ⚡", 99: "强冰雹雷暴 ⚡",
}


class Weather:
    def get_weather(self, city: str) -> str:
        """获取城市实时天气。"""
        # Step 1: 地理编码 (city → lat/lon)
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search"
            f"?name={urllib.parse.quote(city)}&count=1&language=zh&format=json"
        )
        try:
            req = urllib.request.Request(geo_url, headers={
                "User-Agent": "nano_agent_plus/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                geo_data = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            return f"Error: Cannot reach weather service — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        results = geo_data.get("results")
        if not results or not isinstance(results, list) or len(results) == 0:
            return f"Error: City not found — '{city}'"

        r = results[0]
        lat, lon = r["latitude"], r["longitude"]
        location = f"{r.get('admin1', '')} {r['name']}".strip()
        country = r.get("country", "")

        # Step 2: 获取天气
        weather_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m,pressure_msl"
            "&timezone=auto"
        )
        try:
            req = urllib.request.Request(weather_url, headers={
                "User-Agent": "nano_agent_plus/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                w_data = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            return f"Error: Cannot fetch weather — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        c = w_data.get("current", {})
        if not c:
            return f"Error: Weather data unavailable for {location}"

        weather = _WMO_CODES.get(c.get("weather_code", -1), "未知")
        return (
            f"📍 {location} ({country}) 实时天气\n"
            f"🌡️  温度: {c['temperature_2m']}°C\n"
            f"🤔 体感温度: {c['apparent_temperature']}°C\n"
            f"💧 湿度: {c['relative_humidity_2m']}%\n"
            f"🌬️  风速: {c['wind_speed_10m']} km/h\n"
            f"🌀 气压: {c['pressure_msl']} hPa\n"
            f"☁️  天气: {weather}"
        )
