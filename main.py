import re

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Node, Image

from .core.steam_client import SteamClient, SteamAPIError
from .core.store_service import StoreService
from .core.security_acl import SecurityACL
from .core.image_utils import compress_image
from .core import formatter

# 从 Steam 商店链接中提取 AppID（定版规则：开发圣经 §5.2）
_URL_APPID_RE = re.compile(r"store\.steampowered\.com/app/(\d+)")

# 评测语言区配置：支持的语言代码及其显示名
_REVIEW_LANG_NAMES: dict[str, str] = {
    "schinese": "简体中文区",
    "tchinese": "繁体中文区",
    "japanese": "日语区",
    "english": "英语区",
    "all": "全部语言",
}


def _parse_appid(raw: str) -> int | None:
    """
    解析用户输入，提取 AppID。
    支持纯数字和 store.steampowered.com/app/{appid} 形式的链接。
    无法识别时返回 None。
    """
    raw = raw.strip()
    if re.match(r"^\d+$", raw):
        return int(raw)
    m = _URL_APPID_RE.search(raw)
    if m:
        return int(m.group(1))
    return None


class SteamStoreSniperPlugin(Star):

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        timeout = int(config.get("request_timeout", 10))

        # 代理：直接读取完整 URL，空字符串则不使用代理（回退至 trust_env 环境变量）
        proxy_raw = str(config.get("proxy", "")).strip()
        proxy = proxy_raw if proxy_raw else None

        self._client = SteamClient(timeout=timeout, proxy=proxy)
        self._service = StoreService(self._client)
        self._acl = SecurityACL(
            acl_mode=config.get("acl_mode", "Off"),
            allowed_list=config.get("allowed_list", []),
            banned_list=config.get("banned_list", []),
        )
        # 会话级评测语言区覆盖：键为 unified_msg_origin，值为语言代码
        self._session_review_lang: dict[str, str] = {}

    async def initialize(self):
        await self._client.create_session()
        logger.info("[steam] 插件已初始化")

    async def terminate(self):
        await self._client.close_session()
        logger.info("[steam] 插件已卸载")

    # ------------------------------------------------------------------
    # 配置读取（统一从 config 读取，不硬编码默认值在逻辑层）
    # ------------------------------------------------------------------

    def _cc(self) -> str:
        return str(self.config.get("default_cc", "hk")).lower()

    def _lang(self) -> str:
        return str(self.config.get("default_lang", "schinese"))

    def _max_desc(self) -> int:
        return int(self.config.get("max_description_length", 200))

    def _review_lang(self, session_id: str = "") -> str:
        """Returns the effective review language for the given session.
        Session-level override (set by /steam_rlang) takes priority over WebUI config.
        """
        if session_id and session_id in self._session_review_lang:
            return self._session_review_lang[session_id]
        return str(self.config.get("review_lang", "schinese"))

    # ------------------------------------------------------------------
    # 指令：/steam
    # ------------------------------------------------------------------

    @filter.command("steam")
    async def cmd_steam(self, event: AstrMessageEvent):
        """查询 Steam 游戏详情。用法：/steam {appid 或商店链接}"""
        arg = re.sub(r"^/?steam\s*", "", event.message_str.strip(), flags=re.IGNORECASE).strip()

        if not await self._acl.check_access(event.unified_msg_origin):
            yield event.plain_result("权限不足：您所在的群组或账户未被授权使用此功能。")
            return

        if not arg or arg.lower() == "help":
            yield event.plain_result(
                "用法：\n"
                "  /steam {appid}                        — 通过 AppID 查询游戏详情\n"
                "  /steam {商店链接}                    — 通过商店链接查询游戏详情\n"
                "  /steam {appid} {rlang=语言代码}       — 指定本次查询的评测语言区\n"
                "  /steam_price {appid} {地区代码}     — 指定地区查询价格\n"
                "  /steam help                         — 显示此帮助\n"
                "评测语言区可选值：schinese、tchinese、japanese、english、all"
            )
            return

        # 解析可选末尾语言区参数：/steam {appid} {rlang} 或 /steam {appid}
        parts = arg.split()
        inline_rlang: str | None = None
        if len(parts) >= 2 and parts[-1].lower() in _REVIEW_LANG_NAMES:
            inline_rlang = parts[-1].lower()
            appid_str = " ".join(parts[:-1])
        else:
            appid_str = arg

        appid = _parse_appid(appid_str)
        if appid is None:
            yield event.plain_result("请输入 Steam AppID（纯数字）或商店链接")
            return

        cc = self._cc()
        lang = self._lang()
        # 优先级：内联参数 > 会话覆盖 > WebUI 全局默认
        rlang = inline_rlang if inline_rlang else self._review_lang(event.unified_msg_origin)
        game = await self._service.get_game_info(appid, cc, lang, review_lang=rlang)

        # 截断简介
        if game.short_description:
            max_len = self._max_desc()
            if len(game.short_description) > max_len:
                game.short_description = game.short_description[:max_len] + "..."

        text, image_url = formatter.format_game_info(game, cc)
        result = event.make_result().message(text)
        if image_url:
            result = result.url_image(image_url)
        yield result

    # ------------------------------------------------------------------
    # 指令：/steam_price
    # ------------------------------------------------------------------

    @filter.command("steam_price")
    async def cmd_steam_price(self, event: AstrMessageEvent):
        """指定地区查询价格。用法：/steam_price {appid} {地区代码}"""
        if not await self._acl.check_access(event.unified_msg_origin):
            yield event.plain_result("权限不足：您所在的群组或账户未被授权使用此功能。")
            return

        arg = re.sub(r"^/?steam_price\s*", "", event.message_str.strip(), flags=re.IGNORECASE).strip()
        parts = arg.split()

        if len(parts) < 2:
            yield event.plain_result("用法：/steam_price {appid 或商店链接} {地区代码}")
            return

        appid = _parse_appid(parts[0])
        if appid is None:
            yield event.plain_result("请输入有效的 Steam AppID（纯数字）或商店链接")
            return

        cc = parts[1].lower()
        game = await self._service.get_game_info(appid, cc, self._lang())

        if game.error:
            yield event.plain_result(f"❌ 查询失败：{game.error}")
            return

        price_line = formatter.format_price_only(game, cc)
        yield event.plain_result(f"🎮 {game.name}\n💰 {price_line}")

    # ------------------------------------------------------------------
    # 自动解析：检测消息中的 Steam 商店链接（auto_parse_sessions 白名单）
    # ------------------------------------------------------------------

    @filter.regex(r"store\.steampowered\.com/app/\d+")
    async def auto_parse_url(self, event: AstrMessageEvent):
        """当消息包含 Steam 商店链接，且 auto_parse_enabled=True 且会话通过 ACL 时自动解析。"""
        if not self.config.get("auto_parse_enabled", False):
            return
        if not await self._acl.check_access(event.unified_msg_origin):
            return

        appid = _parse_appid(event.message_str)
        if appid is None:
            return

        cc = self._cc()
        lang = self._lang()
        game = await self._service.get_game_info(appid, cc, lang, review_lang=self._review_lang(event.unified_msg_origin))

        if game.error:
            logger.debug(f"[steam] 自动解析 AppID {appid} 失败: {game.error}")
            return

        if game.short_description:
            max_len = self._max_desc()
            if len(game.short_description) > max_len:
                game.short_description = game.short_description[:max_len] + "..."

        text, image_url = formatter.format_game_info(game, cc)
        result = event.make_result().message(text)
        if image_url:
            result = result.url_image(image_url)
        yield result

    # ------------------------------------------------------------------
    # 正则指令：/steam_rlang {语言代码}（会话级评测语言区切换）
    # ------------------------------------------------------------------

    @filter.regex(r"^/?steam_rlang(?:\s+\S+)?")
    async def cmd_set_review_lang(self, event: AstrMessageEvent):
        """切换当前会话的评测数据语言区。用法：/steam_rlang {语言代码}"""
        if not await self._acl.check_access(event.unified_msg_origin):
            yield event.plain_result("权限不足：您所在的群组或账户未被授权使用此功能。")
            return

        m = re.search(r"^/?steam_rlang\s+(\S+)", event.message_str.strip(), re.IGNORECASE)
        if not m:
            # 无参数时显示当前设置与帮助
            current = self._review_lang(event.unified_msg_origin)
            current_label = _REVIEW_LANG_NAMES.get(current, current)
            options = "、".join(f"{k}（{v}）" for k, v in _REVIEW_LANG_NAMES.items())
            yield event.plain_result(
                f"当前评测数据语言区：{current_label}（{current}）\n"
                f"可选值：{options}\n"
                f"用法：/steam_rlang {{语言代码}}"
            )
            return

        lang_code = m.group(1).lower()
        if lang_code not in _REVIEW_LANG_NAMES:
            options = "、".join(_REVIEW_LANG_NAMES.keys())
            yield event.plain_result(f"不支持的语言代码：{lang_code}\n可选值：{options}")
            return

        self._session_review_lang[event.unified_msg_origin] = lang_code
        label = _REVIEW_LANG_NAMES[lang_code]
        yield event.plain_result(f"已将当前会话的评测语言区切换为：{label}（{lang_code}）")

    # ------------------------------------------------------------------
    # 指令：/steam_shots（F2 截图预览 + F3 成人内容防护）
    # ------------------------------------------------------------------

    @filter.command("steam_shots")
    async def cmd_steam_shots(self, event: AstrMessageEvent):
        """查询 Steam 游戏截图。用法：/steam_shots {appid 或商店链接}"""
        if not await self._acl.check_access(event.unified_msg_origin):
            yield event.plain_result("权限不足：您所在的群组或账户未被授权使用此功能。")
            return

        arg = re.sub(r"^/?steam_shots\s*", "", event.message_str.strip(), flags=re.IGNORECASE).strip()
        if not arg:
            yield event.plain_result("用法：/steam_shots {appid 或商店链接}")
            return

        appid = _parse_appid(arg)
        if appid is None:
            yield event.plain_result("请输入 Steam AppID（纯数字）或商店链接")
            return

        game = await self._service.get_game_info(appid, self._cc(), self._lang())

        if game.error:
            yield event.plain_result(f"查询失败：{game.error}")
            return

        # F3：成人内容防护
        # required_age >= 18 是明确年龄门控；content_descriptor_ids 含 1/3/4 是 Steam
        # 对性内容的描述符，两者取并集以覆盖 required_age=0 但实为成人内容的游戏
        _ADULT_DESCRIPTOR_IDS = {1, 3, 4}
        is_adult = (
            game.required_age >= 18
            or bool(_ADULT_DESCRIPTOR_IDS & set(game.content_descriptor_ids))
        )
        if self.config.get("block_adult_screenshots", True) and is_adult:
            yield event.plain_result(
                f"【{game.name}】被标记为成人内容（年龄限制 {game.required_age}+），已屏蔽截图发送。\n"
                "如需关闭此限制，请管理员在插件配置中禁用「屏蔽成人截图」。"
            )
            return

        if not game.screenshots:
            yield event.plain_result(f"【{game.name}】暂无截图数据")
            return

        max_count = max(1, min(9, int(self.config.get("max_screenshots", 6))))
        thumb_urls = [
            s["path_thumbnail"]
            for s in game.screenshots[:max_count]
            if s.get("path_thumbnail")
        ]
        if not thumb_urls:
            yield event.plain_result(f"【{game.name}】无可用截图 URL")
            return

        # 下载并压缩截图
        # uin 使用机器人自身 QQ 号（官方文档示例：uin=905617992，整数或字符串均可）
        self_id = event.get_self_id()
        nodes: list[Node] = []
        failed = 0
        for url in thumb_urls:
            try:
                img_data = await self._client.download_bytes(url)
                compressed = await compress_image(img_data, quality=85)
                node = Node(
                    uin=self_id,
                    name="Steam 截图",
                    content=[Image.fromBytes(compressed)],
                )
                nodes.append(node)
            except SteamAPIError as e:
                logger.warning(f"[steam] 截图下载失败: {e}")
                failed += 1
            except Exception as e:
                logger.warning(f"[steam] 截图处理异常: {type(e).__name__}: {e}")
                failed += 1

        if not nodes:
            yield event.plain_result(
                f"【{game.name}】所有截图下载失败（共 {failed} 张），请检查网络或代理配置后重试"
            )
            return

        # 合并转发：多个 Node 直接放入列表，通过 yield event.chain_result 发送
        # 来源：https://docs.astrbot.app/dev/star/guides/send-message.html
        yield event.chain_result(nodes)

