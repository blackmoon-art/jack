"""URL 抓取工具：fetch_url。

从 URL 抓取网页并提取纯文本内容，内置 SSRF 防护（含 DNS 重绑定 + 重定向检查）。
"""

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
import urllib.error


# ── SSRF 防护：IP 检查 ────────────────────────────────────

def _is_private_ip(ip_str: str) -> bool:
    """检查 IP 是否为私有/保留地址。"""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_unspecified
    except ValueError:
        return False


def _check_hostname(host: str) -> str | None:
    """检查主机名是否安全。返回 None 表示通过，否则返回错误信息。"""
    if not host:
        return "Error: empty hostname"
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return "Error: Access to localhost is blocked"
    if host.startswith("127."):
        return "Error: Access to loopback is blocked"
    if host.startswith(("192.168.", "10.", "0.")):
        return "Error: Access to internal network is blocked"
    # 172.16.0.0/12
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) >= 2 and parts[1].isdigit():
            second = int(parts[1])
            if 16 <= second <= 31:
                return "Error: Access to internal network is blocked"
    # 整数 IP 绕过
    if host.isdigit():
        try:
            ip_int = int(host)
            octets = [
                (ip_int >> 24) & 0xFF,
                (ip_int >> 16) & 0xFF,
                (ip_int >> 8) & 0xFF,
                ip_int & 0xFF,
            ]
            return _check_hostname(".".join(str(o) for o in octets))
        except (ValueError, OverflowError):
            pass
    return None


# ── SSRF 防护：重定向拦截器 ──────────────────────────────

class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """阻止重定向到内网地址的 HTTPRedirectHandler。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlparse(newurl).hostname or ""
        err = _check_hostname(host)
        if err:
            raise urllib.error.URLError(f"Redirect blocked: {err}")
        # DNS 重绑定检查：解析新 URL 的 IP
        try:
            resolved = socket.getaddrinfo(host, None, socket.AF_UNSPEC,
                                          socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in resolved:
                ip = sockaddr[0]
                if _is_private_ip(ip):
                    raise urllib.error.URLError(
                        f"Redirect blocked: {host} resolves to private IP {ip}")
        except socket.gaierror:
            pass  # DNS 解析失败也继续（后续 urlopen 自会报错）
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
        """抓取网页并提取文本内容。SSRF 防护：阻止内网/本地 + DNS 重绑定 + 重定向检查。"""
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"
        host = urllib.parse.urlparse(url).hostname or ""

        # 1. 主机名检查
        err = _check_hostname(host)
        if err:
            return err

        # 2. DNS 重绑定检查：解析 IP 并验证
        try:
            resolved = socket.getaddrinfo(host, None, socket.AF_UNSPEC,
                                          socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in resolved:
                ip = sockaddr[0]
                if _is_private_ip(ip):
                    return f"Error: {host} resolves to private IP ({ip}) — blocked"
        except socket.gaierror:
            pass

        # 3. 带安全重定向拦截器的 opener
        try:
            opener = urllib.request.build_opener(_SafeRedirectHandler())
            req = urllib.request.Request(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            with opener.open(req, timeout=15) as resp:
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
