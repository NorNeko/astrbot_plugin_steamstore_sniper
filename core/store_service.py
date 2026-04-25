from astrbot.api import logger
import asyncio

from .steam_client import SteamClient, SteamAPIError
from .itad_client import ITADClient
from ..models.store_models import SteamGameInfo, PriceOverview


class StoreService:
    """
    业务逻辑层。职责：从原始接口数据提炼字段、处理 is_free 兜底、地区优先级。
    不做文本拼接。
    """

    def __init__(self, client: SteamClient, itad_client: ITADClient | None = None):
        self._client = client
        self._itad = itad_client  # None 时禁用 ITAD 功能

    async def get_game_info(
        self,
        appid: int,
        cc: str,
        lang: str,
        review_lang: str = "all",
        enrich: bool = True,
    ) -> SteamGameInfo:
        """
        查询指定 AppID 的游戏信息。
        review_lang: 评测统计筛选的语言区（schinese/tchinese/japanese/english/all）
        enrich: True（默认）时并发拉取评测/在线人数/ITAD；False 时仅请求 appdetails，
                适用于只需截图/成人内容判断的轻量查询（如 /steam_shots）。
        网络或接口失败时不抛异常，返回 SteamGameInfo(error=...) 供 formatter 处理。
        """
        # 频率检查：每次完整查询计一次，超限时直接返回错误而不发起 HTTP 请求
        try:
            await self._client.check_query_rate_limit()
        except Exception as e:
            return SteamGameInfo(error=str(e))

        if enrich:
            # 并发请求 appdetails + appreviews + 当前在线人数
            details_task = self._client.fetch_app_details(appid, cc, lang)
            reviews_task = self._client.fetch_reviews(appid, lang, review_lang)
            players_task = self._client.fetch_current_players(appid)
            results = await asyncio.gather(details_task, reviews_task, players_task, return_exceptions=True)
            details_result, reviews_result, players_result = results
        else:
            # 轻量模式：仅请求 appdetails（截图查询不需要评测/在线人数/ITAD）
            try:
                details_result = await self._client.fetch_app_details(appid, cc, lang)
            except Exception as exc:
                details_result = exc
            reviews_result = None
            players_result = None

        if isinstance(details_result, Exception):
            logger.info(f"[store_service] AppID {appid} cc={cc} 查询失败: {details_result}")
            return SteamGameInfo(error=str(details_result))

        info = self._extract_fields(details_result)

        if enrich:
            # 提取评测摘要（失败时静默忽略）
            if isinstance(reviews_result, Exception):
                logger.debug(f"[store_service] AppID {appid} 评测摘要获取失败（忽略）: {reviews_result}")
            elif reviews_result is not None:
                qs = reviews_result
                info.review_score_desc = qs.get("review_score_desc") or None
                info.review_total_positive = int(qs.get("total_positive") or 0)
                info.review_total_reviews = int(qs.get("total_reviews") or 0)
                info.review_lang = review_lang

            # 提取当前在线人数（失败时静默忽略）
            if isinstance(players_result, Exception):
                logger.debug(f"[store_service] AppID {appid} 在线人数获取失败（忽略）: {players_result}")
            elif players_result is not None:
                info.current_players = players_result

            # ITAD 可选数据：用户标签、历史最低价（需配置 itad_api_key；失败静默忽略）
            if self._itad and info.steam_appid:
                try:
                    await self._enrich_with_itad(info, cc)
                except Exception as e:
                    logger.warning(f"[store_service] ITAD 数据拉取意外异常（AppID {info.steam_appid}）: {e}")

        return info

    async def _enrich_with_itad(self, info: SteamGameInfo, cc: str) -> None:
        """
        通过 ITAD API 补充用户标签、Steam 历史最低价、卡牌信息、订阅服务，直接写入 info 对象。
        两步调用：① lookup → ② 3路并发（info + storelow + subs）。
        任何步骤失败均静默忽略，不影响主流程。
        """
        itad_id = await self._itad.lookup_itad_id(info.steam_appid)
        if not itad_id:
            logger.info(f"[store_service] AppID {info.steam_appid} ITAD 返回空 ID，跳过 ITAD 数据拉取")
            return
        logger.debug(f"[store_service] AppID {info.steam_appid} -> ITAD ID: {itad_id}")

        info_result, low_result, subs_result = await asyncio.gather(
            self._itad.fetch_game_info(itad_id),
            self._itad.fetch_steam_low(itad_id, cc.upper()),
            # 订阅服务（Game Pass / EA Play 等）固定用 US 查询：
            # ITAD subs 按地区过滤可用性，CN/HK 区订阅库不完整，会漏掉全球性服务
            self._itad.fetch_subscriptions(itad_id, "US"),
            return_exceptions=True,
        )

        # 提取用户标签 + 卡牌信息
        if isinstance(info_result, Exception):
            logger.info(f"[store_service] ITAD info 获取异常: {info_result}")
        elif isinstance(info_result, dict) and info_result:
            raw_tags = info_result.get("tags") or []
            info.itad_tags = [str(t) for t in raw_tags]
            trading_cards = info_result.get("tradingCards")
            if trading_cards is not None:
                info.has_trading_cards = bool(trading_cards)

        # 提取 Steam 历史最低价
        if isinstance(low_result, Exception):
            logger.info(f"[store_service] ITAD storelow 获取异常: {low_result}")
        elif isinstance(low_result, dict) and low_result:
            price_obj = low_result.get("price") or {}
            info.history_low_price = price_obj.get("amount")
            info.history_low_currency = price_obj.get("currency")
            info.history_low_cut = low_result.get("cut")
            ts = low_result.get("timestamp") or ""
            info.history_low_date = ts[:10] if ts else None
            shop_obj = low_result.get("shop") or {}
            info.history_low_shop = shop_obj.get("title")

        # 提取订阅服务
        if isinstance(subs_result, Exception):
            logger.warning(f"[store_service] ITAD subs 获取异常: {subs_result}")
        elif isinstance(subs_result, list):
            info.subscription_services = subs_result

    # ------------------------------------------------------------------
    # 私有方法：字段提炼（只读 data，不做网络调用）
    # ------------------------------------------------------------------

    def _extract_fields(self, data: dict) -> SteamGameInfo:
        info = SteamGameInfo()

        info.steam_appid = data.get("steam_appid")
        info.name = data.get("name")
        info.type = data.get("type")
        info.is_free = bool(data.get("is_free", False))
        info.short_description = data.get("short_description")
        info.header_image = data.get("header_image")

        # categories → 功能特性标签（单人/多人/Steam成就等，图2，不对外展示）
        categories_raw = data.get("categories") or []
        info.categories = [
            g.get("description", "")
            for g in categories_raw
            if g.get("description")
        ]

        # genres → 开发商指定的游戏类型标签（图3，如 独立/策略/抢先体验）
        genres_raw = data.get("genres") or []
        info.genres = [
            g.get("description", "")
            for g in genres_raw
            if g.get("description")
        ]

        # 开发商 / 发行商（接口已为字符串列表）
        info.developers = list(data.get("developers") or [])
        info.publishers = list(data.get("publishers") or [])

        # price_overview（免费游戏通常缺失，以 is_free 兜底）
        price_raw = data.get("price_overview")
        if price_raw:
            info.price_overview = PriceOverview(
                currency=price_raw.get("currency"),
                initial_formatted=price_raw.get("initial_formatted"),
                final_formatted=price_raw.get("final_formatted"),
                discount_percent=int(price_raw.get("discount_percent") or 0),
            )

        # release_date
        rd = data.get("release_date") or {}
        info.coming_soon = bool(rd.get("coming_soon", False))
        info.release_date_str = rd.get("date") or None

        # dlc（只记数量，P0 不展开列表）
        info.dlc_count = len(data.get("dlc") or [])

        # screenshots（F2 截图列表）
        info.screenshots = data.get("screenshots") or []

        # required_age（F3 成人内容防护）；接口有时返回字符串 "0"，强转为 int
        info.required_age = int(data.get("required_age") or 0)

        # content_descriptors.ids（F3 成人内容辅助判断：ID 1/3/4 = 性内容）
        cd = data.get("content_descriptors") or {}
        info.content_descriptor_ids = [int(i) for i in (cd.get("ids") or [])]

        return info
