"""搜索工具：web_search, fetch_url, search_and_fetch。

五级降级：Brave → DuckDuckGo → Bing → SearXNG → Wikipedia
"""

import json
import logging
import re
import urllib.parse
import urllib.request

logger = logging.getLogger("nano_agent.tools.search")


class Search:
    def __init__(self, brave_api_key: str = ""):
        self.brave_api_key = brave_api_key

    # ── 公开接口 ──────────────────────────────────────

    def web_search(self, query: str, max_results: int = 5) -> str:
        """搜索网页：Bing → DuckDuckGo → Brave → SearXNG → Wikipedia 五级降级。"""
        max_results = min(max(max_results, 1), 10)

        # 国内环境：Bing 最稳定，优先
        results = self._search_bing(query, max_results)
        if results:
            return f"Search results for '{query}' (Bing):\n\n" + "\n\n".join(results)

        results = self._search_duckduckgo(query, max_results)
        if results:
            return f"Search results for '{query}':\n\n" + "\n\n".join(results)

        if self.brave_api_key:
            results = self._search_brave(query, max_results)
            if results:
                return f"Search results for '{query}' (Brave):\n\n" + "\n\n".join(results)

        results = self._search_searxng(query, max_results)
        if results:
            return f"Search results for '{query}' (SearXNG):\n\n" + "\n\n".join(results)

        results = self._search_wikipedia(query, max_results)
        if results:
            return f"Search results for '{query}' (Wikipedia):\n\n" + "\n\n".join(results)

        return f"No search results found for '{query}'."

    def fetch_url(self, url: str) -> str:
        """抓取网页并提取文本内容。"""
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"
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
        return text[:8000] if text else "(页面无文本内容)"

    def search_and_fetch(self, query: str) -> str:
        """搜索 + 自动抓取第一个结果的内容。"""
        search_result = self.web_search(query, max_results=3)
        if "No search results" in search_result or "Error" in search_result:
            return search_result

        urls = re.findall(r'(https?://[^\s\n]+)', search_result)
        if not urls:
            return search_result + "\n\n(Could not extract URL for auto-fetch)"

        first_url = urls[0]
        content = self.fetch_url(first_url)
        return (
            f"{search_result}\n\n"
            f"─── Auto-fetched: {first_url} ───\n"
            f"{content[:5000]}"
        )

    # ── 内部搜索引擎 ──────────────────────────────────

    def _search_brave(self, query: str, max_results: int) -> list[str]:
        try:
            req = urllib.request.Request(
                f"https://api.search.brave.com/res/v1/web/search?"
                f"{urllib.parse.urlencode({'q': query, 'count': str(max_results)})}",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.brave_api_key,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.debug(f"Brave search failed: {e}")
            return []
            title = item.get("title", "").strip()
            url = item.get("url", "")
            desc = item.get("description", "").strip()
            if title and url:
                s = f" — {desc[:200]}" if desc else ""
                results.append(f"{len(results)+1}. {title}\n   {url}{s}")
        return results

    def _search_duckduckgo(self, query: str, max_results: int) -> list[str]:
        data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
        url = "https://lite.duckduckgo.com/lite/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://lite.duckduckgo.com/",
        }
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            logger.debug(f"DuckDuckGo search failed: {e}")
            return []
        if "anomaly" in html.lower():
            return []

        results = []
        blocks = re.findall(
            r'<tr[^>]*class="result-snippet"[^>]*>.*?</tr>',
            html, re.DOTALL,
        )
        if not blocks:
            blocks = re.findall(
                r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                html, re.DOTALL,
            )
            seen = set()
            for href, title in blocks:
                if len(results) >= max_results:
                    break
                title_clean = self._clean_html(title)
                if not title_clean or len(title_clean) < 3 or href in seen:
                    continue
                if "duckduckgo" in href:
                    continue
                seen.add(href)
                results.append(f"{len(results)+1}. {title_clean}\n   {href}")
        else:
            seen = set()
            for block in blocks:
                if len(results) >= max_results:
                    break
                link_m = re.search(
                    r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL
                )
                snippet_m = re.search(
                    r'<td class="result-snippet"[^>]*>(.*?)</td>', block, re.DOTALL
                )
                if link_m:
                    href = link_m.group(1)
                    title = self._clean_html(link_m.group(2))
                    if not title or len(title) < 3 or href in seen:
                        continue
                    if "duckduckgo" in href:
                        continue
                    seen.add(href)
                    snippet = ""
                    if snippet_m:
                        snippet = " — " + self._clean_html(snippet_m.group(1))[:200]
                    results.append(f"{len(results)+1}. {title}\n   {href}{snippet}")
        return results

    def _clean_html(self, text: str) -> str:
        return (re.sub(r'<[^>]+>', '', text).strip()
                .replace("&#x27;", "'").replace("&amp;", "&")
                .replace("&quot;", '"').replace("&lt;", "<")
                .replace("&gt;", ">").replace("&#039;", "'"))

    def _search_bing(self, query: str, max_results: int) -> list[str]:
        sources = [
            ("https://cn.bing.com/search", {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }),
            ("https://www.bing.com/search", {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }),
        ]
        params = f"q={urllib.parse.quote_plus(query)}&count={max_results}"

        html = ""
        for base_url, headers in sources:
            try:
                req = urllib.request.Request(f"{base_url}?{params}", headers=headers)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                if html and len(html) > 1000:
                    break
            except Exception as e:
                logger.debug(f"Bing search failed ({base_url}): {e}")
                continue

        if not html or len(html) < 500:
            return []

        blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)
        results = []
        seen = set()
        for block in blocks:
            if len(results) >= max_results:
                break
            title_m = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h2>', block, re.DOTALL)
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if not snippet_m:
                snippet_m = re.search(r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.DOTALL)

            if title_m:
                href = title_m.group(1)
                title = re.sub(r'<[^>]+>', '', title_m.group(2)).strip().replace("&#x27;", "'").replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
                if not title or len(title) < 3 or href in seen:
                    continue
                seen.add(href)
                snippet = ""
                if snippet_m:
                    snippet = " — " + re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip()[:200]
                results.append(f"{len(results)+1}. {title}\n   {href}{snippet}")
        return results

    def _search_searxng(self, query: str, max_results: int) -> list[str]:
        instances = [
            "https://searx.be",
            "https://search.sapti.me",
        ]
        params = urllib.parse.urlencode({
            "q": query, "format": "json", "categories": "general",
        })
        for base_url in instances:
            try:
                req = urllib.request.Request(
                    f"{base_url}/search?{params}",
                    headers={"User-Agent": "nano_agent_plus/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                results = []
                for item in data.get("results", [])[:max_results]:
                    title = item.get("title", "").strip()
                    url = item.get("url", "")
                    snippet = item.get("content", "") or item.get("snippet", "")
                    if title and url:
                        s = f" — {self._clean_html(snippet)[:200]}" if snippet else ""
                        results.append(f"{len(results)+1}. {title}\n   {url}{s}")
                return results
            except Exception as e:
                logger.debug(f"SearXNG search failed ({base_url}): {e}")
                continue
        return []

    def _search_wikipedia(self, query: str, max_results: int) -> list[str]:
        has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in query)
        lang = "zh" if has_cjk else "en"
        api_url = f"https://{lang}.wikipedia.org/w/api.php"
        params = urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": str(max_results),
        })
        try:
            req = urllib.request.Request(
                f"{api_url}?{params}",
                headers={"User-Agent": "nano_agent_plus/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"Wikipedia search failed: {e}")
            return []

        search_items = data.get("query", {}).get("search", [])
        results = []
        for item in search_items:
            title = item["title"]
            snippet = re.sub(r'<[^>]+>', '', item.get("snippet", ""))
            page_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            results.append(f"{len(results)+1}. {title}\n   {page_url}\n   {snippet[:200]}")
        return results
