from astrbot.api import logger
import asyncio

from .steam_client import SteamClient, SteamAPIError
from ..models.store_models import SteamGameInfo, PriceOverview


class StoreService:
    """
    业务逻辑层。职责：从原始接口数据提炼字段、处理 is_free 兜底、地区优先级。
    不做 HTTP 调用，不做文本拼接。
    """

    def __init__(self, client: SteamClient):
        self._client = client

    async def get_game_info(self, appid: int, cc: str, lang: str, review_lang: str = "all") -> SteamGameInfo:
        """
        查询指定 AppID 的游戏信息，并发拉取评测摘要。
        review_lang: 评测统计筛选的语言区（schinese/tchinese/japanese/english/all）
        网络或接口失败时不抛异常，返回 SteamGameInfo(error=...) 供 formatter 处理。
        评测摘要获取失败时静默忽略，不影响主流程。
        """
        # 并发请求 appdetails + appreviews
        details_task = self._client.fetch_app_details(appid, cc, lang)
        reviews_task = self._client.fetch_reviews(appid, lang, review_lang)
        results = await asyncio.gather(details_task, reviews_task, return_exceptions=True)

        details_result, reviews_result = results

        if isinstance(details_result, Exception):
            logger.warning(f"[store_service] AppID {appid} 查询失败: {details_result}")
            return SteamGameInfo(error=str(details_result))

        info = self._extract_fields(details_result)

        # 提取评测摘要（失败时静默忽略）
        if isinstance(reviews_result, Exception):
            logger.debug(f"[store_service] AppID {appid} 评测摘要获取失败（忽略）: {reviews_result}")
        else:
            qs = reviews_result
            info.review_score_desc = qs.get("review_score_desc") or None
            info.review_total_positive = int(qs.get("total_positive") or 0)
            info.review_total_reviews = int(qs.get("total_reviews") or 0)
            info.review_lang = review_lang

        return info

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
