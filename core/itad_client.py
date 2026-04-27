import asyncio
import re

import aiohttp
from astrbot.api import logger

ITAD_BASE = "https://api.isthereanydeal.com"

# 每次 GET/POST 最多显示 8 个用户标签（itad tags 可能多达 20+）
_MAX_TAGS = 8


class ITADClient:
    """
    IsThereAnyDeal HTTP 请求层。
    职责：lookup game ID、拉取 game info（tags）、拉取历史最低价。
    Session 生命周期由外部（main.py initialize/terminate）管理。
    所有方法失败时静默返回 None / 空值，不向上层抛异常。
    """

    def __init__(self, api_key: str, timeout: int = 10, proxy: str | None = None):
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._proxy = proxy
        self._session: aiohttp.ClientSession | None = None

    async def create_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                trust_env=True,
            )
            logger.debug("[itad] session 已创建")

    async def close_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.debug("[itad] session 已关闭")

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def lookup_itad_id(self, appid: int) -> str | None:
        """
        通过 Steam AppID 获取 ITAD 内部 game ID（UUID）。

        优先使用 POST /lookup/id/shop/61/v1（Shop 专属精确查询，Steam shop_id=61）。
        若返回 null，再 fallback 到 GET /games/lookup/v1?appid= 遗留端点。
        找不到或请求失败时返回 None。
        """
        if not self._session or self._session.closed:
            return None

        shop_key = f"app/{appid}"

        # --- 主路径：POST /lookup/id/shop/61/v1（官方 Steam 精确查询，匿名可用）---
        # ITAD 该端点授权为 "None or keySecurity"，不传 key 可绕过 Key 权限问题
        try:
            url = f"{ITAD_BASE}/lookup/id/shop/61/v1"
            async with self._session.post(
                url, json=[shop_key], proxy=self._proxy
            ) as resp:
                if resp.status == 200:
                    data: dict = await resp.json(content_type=None)
                    if isinstance(data, dict):
                        itad_id = data.get(shop_key)
                        if itad_id:
                            logger.debug(f"[itad] shop-lookup appid={appid} -> {itad_id}")
                            return itad_id
                        logger.info(f"[itad] shop-lookup appid={appid} 返回 null（ITAD 未收录该 Steam app），跳过 ITAD")
                        return None
                    else:
                        logger.warning(f"[itad] shop-lookup appid={appid} 响应格式异常: {type(data).__name__}，尝试 fallback")
                else:
                    logger.warning(f"[itad] shop-lookup appid={appid} HTTP {resp.status}，尝试 fallback")
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"[itad] shop-lookup appid={appid} 请求失败: {e}，尝试 fallback")
        except Exception as e:
            logger.warning(f"[itad] shop-lookup appid={appid} 异常: {e}，尝试 fallback")

        # --- Fallback：GET /games/lookup/v1?appid=（遗留端点）---
        try:
            url = f"{ITAD_BASE}/games/lookup/v1"
            params = {"appid": appid, "key": self._api_key}
            async with self._session.get(url, params=params, proxy=self._proxy) as resp:
                if resp.status == 401:
                    logger.warning("[itad] API Key 无效（401），请检查 itad_api_key 配置")
                    return None
                if resp.status != 200:
                    logger.warning(f"[itad] lookup fallback appid={appid} HTTP {resp.status}")
                    return None
                data: dict = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning(f"[itad] lookup fallback appid={appid} 超时")
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"[itad] lookup fallback appid={appid} 网络错误: {e}")
            return None
        except Exception as e:
            logger.warning(f"[itad] lookup fallback appid={appid} 解析响应失败: {e}")
            return None

        if not isinstance(data, dict):
            logger.warning(f"[itad] lookup fallback appid={appid} 响应格式异常: {type(data).__name__} = {str(data)[:200]}")
            return None
        if not data.get("found"):
            logger.info(f"[itad] appid={appid} 在 ITAD 未收录（两种查询均未命中）")
            logger.debug(f"[itad] fallback 原始响应: {str(data)[:500]}")
            return None
        itad_id = (data.get("game") or {}).get("id")
        if not itad_id:
            logger.warning(f"[itad] appid={appid} fallback found=true 但 game.id 为空, 响应: {str(data)[:500]}")
            return None
        logger.debug(f"[itad] lookup fallback appid={appid} -> {itad_id}")
        return itad_id

    async def fetch_game_info(self, itad_id: str) -> dict:
        """
        GET /games/info/v2 — 返回原始 dict（含 tags 等）。
        失败时返回空 dict，调用方按空处理。
        """
        if not self._session or self._session.closed:
            return {}
        url = f"{ITAD_BASE}/games/info/v2"
        params = {"id": itad_id, "key": self._api_key}
        try:
            async with self._session.get(url, params=params, proxy=self._proxy) as resp:
                if resp.status != 200:
                    logger.info(f"[itad] info {itad_id} HTTP {resp.status}")
                    return {}
                return await resp.json(content_type=None)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.info(f"[itad] info {itad_id} 失败: {e}")
            return {}

    async def fetch_steam_low(self, itad_id: str, country: str = "US") -> dict | None:
        """
        POST /games/storelow/v2?shops=61 — Steam 平台专属历史最低价。
        shops=61 为 ITAD 的 Steam 商店 ID。
        失败或无数据时返回 None。
        low 结构：{price: {amount, currency}, cut, shop: {title}, timestamp, ...}
        """
        if not self._session or self._session.closed:
            return None
        url = f"{ITAD_BASE}/games/storelow/v2"
        params = {"key": self._api_key, "country": country.upper(), "shops": 61}
        try:
            async with self._session.post(
                url, params=params, json=[itad_id], proxy=self._proxy
            ) as resp:
                if resp.status != 200:
                    logger.info(f"[itad] storelow {itad_id} HTTP {resp.status}")
                    return None
                data: list = await resp.json(content_type=None)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.info(f"[itad] storelow {itad_id} 失败: {e}")
            return None

        # 返回格式：[{"id": "...", "lows": [...]}]
        if not data:
            return None
        lows = (data[0] or {}).get("lows") or []
        return lows[0] if lows else None

    async def search_games(self, title: str, limit: int = 5) -> list[dict]:
        """
        ITAD /games/search/v1 — 按标题搜索游戏。
        返回 [{"id": str, "title": str, "appid": int|None, "image_url": str}, ...]
        失败或无数据时返回空列表。
        需要有效的 itad_api_key。
        """
        if not self._session or self._session.closed:
            return []
        if not self._api_key:
            return []

        url = f"{ITAD_BASE}/games/search/v1"
        params = {"key": self._api_key, "title": title, "limit": limit}
        logger.debug(f"[itad] 搜索游戏 title={title!r} limit={limit}")

        try:
            async with self._session.get(url, params=params, proxy=self._proxy) as resp:
                if resp.status != 200:
                    logger.info(f"[itad] 搜索 title={title!r} HTTP {resp.status}")
                    return []
                data = await resp.json(content_type=None)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.info(f"[itad] 搜索 title={title!r} 失败: {e}")
            return []
        except Exception as e:
            logger.warning(f"[itad] 搜索 title={title!r} 异常: {e}")
            return []

        if not isinstance(data, list):
            return []

        results: list[dict] = []
        for game in data:
            if not isinstance(game, dict):
                continue
            # 提取封面图 URL（优先 banner145 > boxart > banner300）
            assets = game.get("assets") or {}
            image_url = (
                assets.get("banner145")
                or assets.get("boxart")
                or assets.get("banner300")
                or ""
            )
            # 提取 Steam AppID（从 urls 中匹配 store.steampowered.com/app/{appid}）
            appid = None
            for url_item in (game.get("urls") or []):
                if isinstance(url_item, str) and "store.steampowered.com/app" in url_item:
                    m = re.search(r"/app/(\d+)", url_item)
                    if m:
                        appid = int(m.group(1))
                        break

            results.append({
                "id": game.get("id", ""),
                "title": game.get("title", "未知游戏"),
                "appid": appid,
                "image_url": image_url,
            })

        logger.debug(f"[itad] 搜索 title={title!r} 返回 {len(results)} 条结果")
        return results

    async def fetch_subscriptions(self, itad_id: str, country: str = "US") -> list[str]:
        """
        POST /games/subs/v1 — 返回包含该游戏的订阅服务名称列表（如 Game Pass、EA Play）。
        失败或无数据时返回空列表。
        """
        if not self._session or self._session.closed:
            return []
        url = f"{ITAD_BASE}/games/subs/v1"
        params = {"key": self._api_key, "country": country.upper()}
        try:
            async with self._session.post(
                url, params=params, json=[itad_id], proxy=self._proxy
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[itad] subs {itad_id} HTTP {resp.status}")
                    return []
                data: list = await resp.json(content_type=None)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"[itad] subs {itad_id} 请求失败: {e}")
            return []

        # 返回格式：[{"id": "...", "subs": [{"service": {"title": "..."}, ...}, ...]}]
        logger.info(f"[itad] subs {itad_id} 原始响应: {data}")
        if not data:
            return []
        subs_raw = (data[0] or {}).get("subs") or []
        names: list[str] = []
        for sub in subs_raw:
            # 实际响应结构：{"id": N, "name": "EA Play", "leaving": null}
            title = sub.get("name") or (sub.get("service") or {}).get("title")
            if title:
                names.append(title)
        logger.info(f"[itad] subs {itad_id} 解析结果: {names}")
        return names
