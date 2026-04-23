import asyncio
import aiohttp
from astrbot.api import logger

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
APPREVIEWS_URL = "https://store.steampowered.com/appreviews"


class SteamAPIError(Exception):
    """steam_client 向上层抛出的统一异常，屏蔽底层网络细节。"""
    pass


class SteamClient:
    """
    HTTP 请求层。职责：发请求、处理网络异常、返回原始 data 字典。
    不做任何业务判断（is_free、字段提炼等均不在此处理）。
    session 生命周期由插件 initialize / terminate 管理。
    """

    def __init__(self, timeout: int = 10, proxy: str | None = None):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        # 显式代理 URL（如 http://127.0.0.1:7897）；None 则依赖环境变量（trust_env）
        self._proxy: str | None = proxy

    async def create_session(self) -> None:
        if self._session is None or self._session.closed:
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
