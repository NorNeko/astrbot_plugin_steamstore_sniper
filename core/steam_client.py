import asyncio
import html as _html
import re
import time
from collections import deque

import aiohttp
from astrbot.api import logger

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
APPREVIEWS_URL = "https://store.steampowered.com/appreviews"
PLAYERS_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
SEARCH_SUGGEST_URL = "https://store.steampowered.com/search/suggest"
SEARCH_RESULTS_URL = "https://store.steampowered.com/search/results/"

# /search/suggest 返回 HTML 片段的正则提取模式
_RE_APPID = re.compile(r'data-ds-appid="(\d+)"')
_RE_MATCH_NAME = re.compile(r'class="match_name">(.*?)</div>')
_RE_MATCH_PRICE = re.compile(r'class="match_price">(.*?)</div>')

# /search/results/ 返回 HTML 片段的正则提取模式
_RE_RESULTS_APPID = re.compile(r'data-ds-appid="(\d+)"')
_RE_RESULTS_TITLE = re.compile(r'<span class="title">(.*?)</span>')
_RE_RESULTS_PRICE = re.compile(r'<div class="discount_final_price[^"]*">(.*?)</div>')
_RE_RESULTS_IMG = re.compile(r'<img src="(https://[^"]*capsule[^"]*)"')


class SteamAPIError(Exception):
    """steam_client 向上层抛出的统一异常，屏蔽底层网络细节。"""
    pass


class SteamClient:
    """
    HTTP 请求层。职责：发请求、处理网络异常、返回原始 data 字典。
    不做任何业务判断（is_free、字段提炼等均不在此处理）。
    session 生命周期由插件 initialize / terminate 管理。
    """

    def __init__(self, timeout: int = 10, proxy: str | None = None, rate_limit: int = 4):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        # 显式代理 URL（如 http://127.0.0.1:7897）；None 则依赖环境变量（trust_env）
        self._proxy: str | None = proxy
        # 全局频率限制：每分钟最多 rate_limit 次完整商店页面查询；0 = 不限制
        self._rate_limit: int = rate_limit
        self._query_times: deque[float] = deque()
        self._rate_lock = asyncio.Lock()

    async def check_query_rate_limit(self) -> None:
        """
        全局查询频率检查（滑动窗口，窗口 = 60 秒）。
        以"完整商店页面查询次数"为单位（非底层 HTTP 请求次数），全局对所有会话生效。
        超限时抛出 SteamAPIError，不发起实际 HTTP 请求，避免触发 Steam 临时封禁。
        rate_limit == 0 时完全跳过检查。
        """
        if self._rate_limit <= 0:
            return
        async with self._rate_lock:
            now = time.monotonic()
            # 移除 60 秒窗口外的旧时间戳
            while self._query_times and now - self._query_times[0] > 60.0:
                self._query_times.popleft()
            if len(self._query_times) >= self._rate_limit:
                raise SteamAPIError(
                    f"查询过于频繁，已达每分钟上限（{self._rate_limit} 次），请稍后再试"
                )
            self._query_times.append(now)

    async def create_session(self) -> None:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                trust_env=True,  # 允许读取 HTTPS_PROXY 等环境变量，作为显式代理未配置时的回退
            )
            logger.debug(f"[steam_client] aiohttp session 已创建，代理={'[trust_env]' if self._proxy is None else self._proxy}")

    async def close_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.debug("[steam_client] aiohttp session 已关闭")

    async def fetch_app_details(self, appid: int, cc: str, lang: str) -> dict:
        """
        请求 appdetails 接口，返回原始 data 字典（即 response[appid]["data"]）。
        失败时统一抛出 SteamAPIError，不向外暴露 aiohttp / asyncio 异常类型。
        """
        if self._session is None or self._session.closed:
            raise SteamAPIError("HTTP session 未初始化，请检查插件 initialize 是否正常执行")

        params = {"appids": appid, "cc": cc, "l": lang}
        logger.debug(f"[steam_client] 请求 appdetails appid={appid} cc={cc} l={lang}")

        try:
            async with self._session.get(APPDETAILS_URL, params=params, proxy=self._proxy) as resp:
                if resp.status != 200:
                    raise SteamAPIError(f"HTTP {resp.status}，接口请求失败")
                raw: dict = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            raise SteamAPIError("请求超时，请稍后重试")
        except aiohttp.ClientError as e:
            raise SteamAPIError(f"网络错误：{e}")

        key = str(appid)
        if key not in raw:
            raise SteamAPIError(f"接口未返回 AppID {appid} 的数据")
        if not raw[key].get("success"):
            raise SteamAPIError(f"AppID {appid} 不存在或在当前地区不可见")

        return raw[key]["data"]

    async def download_bytes(self, url: str) -> bytes:
        """
        下载任意 URL 的二进制内容（用于截图下载）。
        复用已有 session，失败时统一抛出 SteamAPIError。
        """
        if self._session is None or self._session.closed:
            raise SteamAPIError("HTTP session 未初始化，请检查插件 initialize 是否正常执行")

        try:
            async with self._session.get(url, proxy=self._proxy) as resp:
                if resp.status != 200:
                    raise SteamAPIError(f"下载失败 HTTP {resp.status}: {url}")
                return await resp.read()
        except asyncio.TimeoutError:
            raise SteamAPIError(f"下载超时: {url}")
        except aiohttp.ClientError as e:
            raise SteamAPIError(f"下载网络错误：{e}")

    async def fetch_reviews(self, appid: int, display_lang: str, review_lang: str = "all") -> dict:
        """
        请求 appreviews 接口，返回评测摘要字典（query_summary）。
        display_lang: 响应文本语言（review_score_desc 标签的显示语言，如 schinese）
        review_lang:  统计筛选的语言区（如 schinese/tchinese/japanese/english/all）
        失败时抛出 SteamAPIError（由调用方截获，不影响主流程）。
        """
        if self._session is None or self._session.closed:
            raise SteamAPIError("HTTP session 未初始化")

        url = f"{APPREVIEWS_URL}/{appid}"
        params = {"json": "1", "language": review_lang, "filter": "all", "l": display_lang}

        try:
            async with self._session.get(url, params=params, proxy=self._proxy) as resp:
                if resp.status != 200:
                    raise SteamAPIError(f"HTTP {resp.status}")
                raw: dict = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            raise SteamAPIError("请求超时")
        except aiohttp.ClientError as e:
            raise SteamAPIError(f"网络错误：{e}")

        if not raw.get("success"):
            raise SteamAPIError("评测接口返回失败")

        return raw.get("query_summary") or {}

    async def fetch_current_players(self, appid: int) -> int:
        """
        查询指定 AppID 的当前在线玩家数（Steam 上已连接的玩家）。
        来源：ISteamUserStats/GetNumberOfCurrentPlayers，公开接口，无需 API Key。
        失败时抛出 SteamAPIError（调用方应捕获，不影响主流程）。
        """
        if self._session is None or self._session.closed:
            raise SteamAPIError("HTTP session 未初始化")

        params = {"appid": appid}
        logger.debug(f"[steam_client] 请求在线人数 appid={appid}")

        try:
            async with self._session.get(PLAYERS_URL, params=params, proxy=self._proxy) as resp:
                if resp.status != 200:
                    raise SteamAPIError(f"HTTP {resp.status}")
                raw: dict = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            raise SteamAPIError("请求超时")
        except aiohttp.ClientError as e:
            raise SteamAPIError(f"网络错误：{e}")

        response = raw.get("response") or {}
        if response.get("result") != 1:
            raise SteamAPIError("在线人数接口返回失败")

        return int(response.get("player_count") or 0)

    # ------------------------------------------------------------------
    # 搜索接口（方案 B：Steam 官方端点）
    # ------------------------------------------------------------------

    async def search_suggest(self, keyword: str, cc: str, lang: str) -> list[dict]:
        """
        Steam /search/suggest 端点搜索游戏。
        返回 [{"appid": int, "name": str, "price": str, "image_url": str}, ...]
        HTML 正则解析，失败时抛出 SteamAPIError。
        """
        if self._session is None or self._session.closed:
            raise SteamAPIError("HTTP session 未初始化，请检查插件 initialize 是否正常执行")

        await self.check_query_rate_limit()

        params = {"term": keyword, "f": "games", "cc": cc, "realm": "1", "l": lang}
        logger.debug(f"[steam_client] 搜索 suggest keyword={keyword!r} cc={cc} l={lang}")

        try:
            async with self._session.get(SEARCH_SUGGEST_URL, params=params, proxy=self._proxy) as resp:
                if resp.status != 200:
                    raise SteamAPIError(f"HTTP {resp.status}，搜索请求失败")
                html: str = await resp.text()
        except asyncio.TimeoutError:
            raise SteamAPIError("搜索请求超时，请稍后重试")
        except aiohttp.ClientError as e:
            raise SteamAPIError(f"搜索网络错误：{e}")

        # 解析 HTML 片段：每个 <a class="match ..."> 块包含 data-ds-appid / match_name / match_price
        appids = _RE_APPID.findall(html)
        names = _RE_MATCH_NAME.findall(html)
        prices = _RE_MATCH_PRICE.findall(html)

        results: list[dict] = []
        for i, appid_str in enumerate(appids):
            name = names[i] if i < len(names) else "未知游戏"
            price = prices[i] if i < len(prices) else ""
            results.append({
                "appid": int(appid_str),
                "name": _unescape_html(name),
                "price": _unescape_html(price),
                "image_url": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid_str}/capsule_231x87.jpg",
            })

        logger.debug(f"[steam_client] 搜索 suggest 返回 {len(results)} 条结果")
        return results

    async def search_results_fallback(self, keyword: str, cc: str, lang: str, count: int = 5) -> list[dict]:
        """
        Steam /search/results/ 备选端点（suggest 无结果时回退）。
        返回格式同 search_suggest。
        """
        if self._session is None or self._session.closed:
            raise SteamAPIError("HTTP session 未初始化")

        await self.check_query_rate_limit()

        params = {
            "query": keyword,
            "start": "0",
            "count": str(count),
            "dynamic_data": "",
            "sort_by": "_ASC",
            "snr": "1_7_7_230_7",
            "infinite": "1",
        }
        logger.debug(f"[steam_client] 搜索 results fallback keyword={keyword!r} cc={cc} l={lang}")

        try:
            async with self._session.get(SEARCH_RESULTS_URL, params=params, proxy=self._proxy) as resp:
                if resp.status != 200:
                    raise SteamAPIError(f"HTTP {resp.status}，搜索请求失败")
                data: dict = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            raise SteamAPIError("搜索请求超时")
        except aiohttp.ClientError as e:
            raise SteamAPIError(f"搜索网络错误：{e}")

        html = data.get("results_html") or ""
        if not html:
            return []

        appids = _RE_RESULTS_APPID.findall(html)
        titles = _RE_RESULTS_TITLE.findall(html)
        prices = _RE_RESULTS_PRICE.findall(html)
        images = _RE_RESULTS_IMG.findall(html)

        results: list[dict] = []
        for i, appid_str in enumerate(appids):
            name = titles[i] if i < len(titles) else "未知游戏"
            price = prices[i] if i < len(prices) else ""
            image_url = images[i] if i < len(images) else ""
            results.append({
                "appid": int(appid_str),
                "name": _unescape_html(name),
                "price": _unescape_html(price),
                "image_url": image_url,
            })

        logger.debug(f"[steam_client] 搜索 results fallback 返回 {len(results)} 条结果")
        return results


def _unescape_html(text: str) -> str:
    """反转义常见的 HTML 实体（使用 Python 标准库 html.unescape）。"""
    return _html.unescape(text)
