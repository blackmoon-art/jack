"""URL 抓取工具：fetch_url。

从 URL 抓取网页并提取纯文本内容，内置 SSRF 防护。
"""

import re
import urllib.parse
import urllib.request
import urllib.error


class Fetch:
    # 工具注册声明
    TOOLS = [
        ("fetch_url", "Fetch and extract text content from a URL.", "fetch_url",
         {"url": {"type": "string", "description": "URL to fetch"}},
         ["url"]),
    ]

    def __init__(self, work_dir: str = "", max_chars: int = 8000):
        self.max_chars = max_chars

    # ── 公开接口 ──────────────────────────────────────

    def fetch_url(self, url: str) -> str:
        """抓取网页并提取文本内容。阻止内网/本地地址防止 SSRF。"""
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"
        # SSRF 防护：检查目标主机
        host = urllib.parse.urlparse(url).hostname or ""

        # 内网主机名检查
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", ""):
            return "Error: Access to localhost is blocked"
        # IPv6 loopback 各种形式
        if host.startswith("[::1") or host.startswith("[::ffff:"):
            return "Error: Access to localhost is blocked"
        # 127.x.x.x 整个 loopback 段
        if host.startswith("127."):
            return "Error: Access to loopback is blocked"
        # 内网 IP 段
        if host.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.",
                           "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                           "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                           "172.29.", "172.30.", "172.31.")):
            return "Error: Access to internal network is blocked"
        # 0.0.0.0/8 段（含 0x7f000001 等十六进制绕过变体）
        if host.startswith("0") and host != "0":
            return "Error: Access to reserved network is blocked"
        # 整数 IP 绕过（2130706433 → 127.0.0.1）
        if host.isdigit():
            try:
                ip_int = int(host)
                # 检查是否是私有/保留 IP 段
                if (ip_int == 0 or                          # 0.0.0.0
                    (0x7F000000 <= ip_int <= 0x7FFFFFFF) or # 127.0.0.0/8
                    (0x0A000000 <= ip_int <= 0x0AFFFFFF) or # 10.0.0.0/8
                    (0xC0A80000 <= ip_int <= 0xC0A8FFFF) or # 192.168.0.0/16
                    (0xAC100000 <= ip_int <= 0xAC1FFFFF)):  # 172.16.0.0/12
                    return "Error: Access to private network is blocked"
            except (ValueError, OverflowError):
                pass
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "html" not in content_type and "text" not in content_type:
                    return f"Error: Unsupported content type — {content_type}"
                raw = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            return f"Error: HTTP {e.code} — {e.reason}"
        except urllib.error.URLError as e:
            return f"Error: Cannot reach URL — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:self.max_chars] if text else "(页面无文本内容)"
