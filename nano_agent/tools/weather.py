"""天气工具：get_weather。数据来源 Open-Meteo，免费无需 API Key。

使用 subprocess + curl 获取数据，绕过 Python urllib 在某些 macOS 环境下的出站连接问题。
"""

import json
import subprocess
import urllib.parse

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

_GEO_BASE = "http://geocoding-api.open-meteo.com/v1/search"
_WEATHER_BASE = "http://api.open-meteo.com/v1/forecast"


def _curl_get(url: str, timeout: int = 8) -> dict | None:
    """用 curl 子进程获取 JSON，绕过 Python urllib 出站问题。"""
    try:
        r = subprocess.run(
            ["curl", "-sS", "--connect-timeout", str(timeout),
             "--max-time", str(timeout + 5), url],
            capture_output=True, text=True, timeout=timeout + 8,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


class Weather:
    # 工具注册声明
    TOOLS = [
        ("get_weather", "Get current weather for a city. Data from Open-Meteo (free, no API key).", "get_weather",
         {"city": {"type": "string", "description": "City name (Chinese or English)"}},
         ["city"]),
    ]

    def get_weather(self, city: str) -> str:
        """获取城市实时天气。"""
        # Step 1: 地理编码 (city → lat/lon)
        geo_url = (
            f"{_GEO_BASE}"
            f"?name={urllib.parse.quote(city)}&count=1&language=zh&format=json"
        )
        geo_data = _curl_get(geo_url)
        if not geo_data:
            return "Error: Cannot reach weather service (geocoding timeout)"

        results = geo_data.get("results")
        if not results or not isinstance(results, list) or len(results) == 0:
            return f"Error: City not found — '{city}'"

        r = results[0]
        lat, lon = r["latitude"], r["longitude"]
        location = f"{r.get('admin1', '')} {r['name']}".strip()
        country = r.get("country", "")

        # Step 2: 获取天气
        weather_url = (
            f"{_WEATHER_BASE}"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m,pressure_msl"
            "&timezone=auto"
        )
        w_data = _curl_get(weather_url)
        if not w_data:
            return f"Error: Cannot fetch weather for {location} (timeout)"

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
