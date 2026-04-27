import asyncio
import base64
import json as _json
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

from .core.steam_client import SteamClient, SteamAPIError
from .core.store_service import StoreService
from .core.itad_client import ITADClient
from .core.security_acl import SecurityACL
from .core.image_utils import stitch_images_vertical
from .core import formatter

if TYPE_CHECKING:
    from .models.store_models import SteamGameInfo

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
        try:
            timeout = int(config.get("request_timeout", 10))
        except (TypeError, ValueError):
            timeout = 10

        try:
            rate_limit = max(0, int(config.get("rate_limit_per_minute", 4)))
        except (TypeError, ValueError):
            rate_limit = 4

        # 代理：直接读取完整 URL，空字符串则不使用代理（回退至 trust_env 环境变量）
        proxy_raw = str(config.get("proxy", "")).strip()
        proxy = proxy_raw if proxy_raw else None

        self._client = SteamClient(timeout=timeout, proxy=proxy, rate_limit=rate_limit)

        # ITAD：API Key 非空时启用，否则禁用（标签回退 genres，不显示史低）
        itad_key = str(config.get("itad_api_key", "")).strip()
        if not itad_key:
            logger.warning("[steam] itad_api_key 未配置或为空，ITAD 功能已禁用（标签/史低/订阅均不显示）。请在插件配置页填写 API Key 后重载插件。")
        self._itad_client: ITADClient | None = (
            ITADClient(api_key=itad_key, timeout=timeout, proxy=proxy)
            if itad_key else None
        )

        self._service = StoreService(self._client, itad_client=self._itad_client)
        self._acl = SecurityACL(
            acl_mode=config.get("acl_mode", "Off"),
            allowed_list=config.get("allowed_list", []),
            banned_list=config.get("banned_list", []),
        )
        # 会话级评测语言区覆盖：键为 unified_msg_origin，值为语言代码
        self._session_review_lang: dict[str, str] = {}

    async def initialize(self):
        await self._client.create_session()
        if self._itad_client:
            await self._itad_client.create_session()
            logger.info("[steam] ITAD 客户端已初始化")
        logger.info("[steam] 插件已初始化")

    async def terminate(self):
        await self._client.close_session()
        if self._itad_client:
            await self._itad_client.close_session()
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

    def _screenshot_width(self) -> int:
        try:
            return max(400, min(1200, int(self.config.get("screenshot_width", 600))))
        except (TypeError, ValueError):
            return 600

    def _stitch_target_kb(self) -> int:
        try:
            return max(0, int(self.config.get("screenshot_stitch_max_kb", 800)))
        except (TypeError, ValueError):
            return 800

    def _fallback_ccs(self, exclude: str = "") -> list[str]:
        """解析 cc_fallback_order 配置项，返回去重后的地区代码列表（已排除 exclude）。"""
        raw = str(self.config.get("cc_fallback_order", "hk;jp;us")).strip()
        if not raw:
            return []
        codes = [c.strip().lower() for c in raw.split(";") if c.strip()]
        if exclude:
            codes = [c for c in codes if c != exclude.lower()]
        return codes

    async def _query_with_fallback(
        self,
        appid: int,
        cc: str,
        lang: str,
        review_lang: str = "all",
        enrich: bool = True,
    ) -> "tuple[SteamGameInfo, str]":
        """
        查询游戏信息，若主地区不可见则按 cc_fallback_order 顺序自动重试。
        返回 (game, effective_cc)，effective_cc 为实际成功的地区代码。
        enrich=False 时跳过评测/在线人数/ITAD，适用于截图等轻量查询场景。
        """
        game = await self._service.get_game_info(appid, cc, lang, review_lang=review_lang, enrich=enrich)
        if not game.error:
            return game, cc
        # 仅对「地区不可见」触发回退，其他错误（超时/AppID不存在）直接返回
        if "不可见" not in game.error:
            return game, cc
        for fallback_cc in self._fallback_ccs(exclude=cc):
            logger.debug(f"[steam] AppID {appid} 地区 {cc} 不可见，尝试回退至 {fallback_cc}")
            fallback_game = await self._service.get_game_info(appid, fallback_cc, lang, review_lang=review_lang, enrich=enrich)
            if not fallback_game.error:
                return fallback_game, fallback_cc
        # 所有回退均失败，返回原始错误
        return game, cc

    def _review_lang(self, session_id: str = "") -> str:
        """Returns the effective review language for the given session.
        Session-level override (set by /steam_rlang) takes priority over WebUI config.
        """
        if session_id and session_id in self._session_review_lang:
            return self._session_review_lang.pop(session_id)
        return str(self.config.get("review_lang", "schinese"))

    # ------------------------------------------------------------------
    # 成人内容屏蔽名单（UMO 列表）：默认全局不屏蔽，仅名单内会话拒绝 R18 截图
    # ------------------------------------------------------------------

    def _adult_block_list(self) -> list[str]:
        raw = self.config.get("adult_screenshots_block_list", []) or []
        return [str(x).strip() for x in raw if str(x).strip()]

    def _is_adult_blocked(self, umo: str) -> bool:
        if not umo:
            return False
        return umo in self._adult_block_list()

    def _persist_adult_block_list(self, items: list[str]) -> bool:
        """更新屏蔽名单到 self.config 并尝试持久化。返回是否成功落盘。"""
        # 去重保序
        seen: set[str] = set()
        deduped: list[str] = []
        for it in items:
            it = str(it).strip()
            if it and it not in seen:
                seen.add(it)
                deduped.append(it)
        self.config["adult_screenshots_block_list"] = deduped
        save_fn = getattr(self.config, "save_config", None)
        if callable(save_fn):
            try:
                save_fn()
                return True
            except Exception as e:
                logger.warning(f"[steam] adult_screenshots_block_list 持久化失败: {e}")
                return False
        logger.warning("[steam] self.config 不支持 save_config()，屏蔽名单仅在内存中生效")
        return False

    def _get_aiocqhttp_send_target(self, event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        group_id = event.get_group_id()
        sender_id = event.get_sender_id()

        if bot is None:
            return None
        if group_id and str(group_id).isdigit():
            return bot.send_group_msg, "group_id", int(group_id)
        if sender_id and str(sender_id).isdigit():
            return bot.send_private_msg, "user_id", int(sender_id)
        return None

    async def _send_aiocqhttp_local_image(
        self,
        event: AstrMessageEvent,
        file_path: str,
    ) -> bool | None:
        """
        aiocqhttp 专用快速路径：直接调用 OneBot 发送本地长图文件，
        避开 AstrBot 对 Image 组件统一 base64 化导致的 NapCat 超时问题。
        """
        send_target = self._get_aiocqhttp_send_target(event)
        if send_target is None:
            return None
        send_method, send_kwargs_key, target_id = send_target
        file_uri = Path(file_path).resolve().as_uri()

        try:
            # 3 秒超时限制：若 NapCat/QQ 服务端未在 3s 内回调 sendMsg（通常意味着
            # 图片被黑名单/审核队列扛住），立即报错回退为链接，避免原本 12s 的无效等待。
            await asyncio.wait_for(
                send_method(
                    **{
                        send_kwargs_key: target_id,
                        "message": [{"type": "image", "data": {"file": file_uri}}],
                    }
                ),
                timeout=3.0,
            )
            logger.info(f"[steam] 截图长图直发成功: {file_uri}")
            return True
        except asyncio.TimeoutError:
            logger.warning(f"[steam] 截图长图直发超时（3s），可能命中 QQ 服务端图片黑名单，回退为链接 | file={file_uri}")
            return False
        except Exception as e:
            logger.warning(f"[steam] 截图长图直发失败，回退为链接: {type(e).__name__}: {e} | file={file_uri}")
            return False

    # ------------------------------------------------------------------
    # 指令：/steam
    # ------------------------------------------------------------------

    @filter.command("steam")
    async def cmd_steam(self, event: AstrMessageEvent):
        """查询 Steam 游戏详情。用法：/steam {appid}"""
        arg = re.sub(r"^/?steam\s*", "", event.message_str.strip(), flags=re.IGNORECASE).strip()

        if not await self._acl.check_access(event.unified_msg_origin):
            yield event.plain_result("权限不足：您所在的群组或账户未被授权使用此功能。")
            return

        if not arg or arg.lower() == "help":
            yield event.plain_result(
                "🎮 Steam 商店速查指令列表\n\n"
                "【基础查询】\n"
                "  /steam {appid}                         查询游戏详情\n"
                "  /steam {appid} {语言代码}               指定评测语言区查询\n"
                "  /steam_price {appid} {地区}             指定地区查询价格\n"
                "  /steam_shots {appid}                   查询游戏截图\n"
                "  发送 Steam 商店链接                     开启自动解析时自动查询\n"
                "  /steam help                           显示此帮助\n\n"
                "【配置管理】\n"
                "  /steam_rlang [语言代码]                设置评测语言区（无参查看帮助）\n"
                "  /steam_adult status [UMO]             查看 R18 截图屏蔽名单\n"
                "  /steam_adult on [UMO]                 加入屏蔽名单\n"
                "  /steam_adult off [UMO]                移出屏蔽名单\n\n"
                "【快速提示】\n"
                "  • 评测语言：schinese | tchinese | japanese | english | all\n"
                "  • 支持 AppID 和商店链接作为参数\n"
                "  • 获取会话 UMO 标识：向机器人发送 /sid\n"
                "  • /steam_rlang 仅对下一次查询生效（一次性）\n"
                "  • /steam_adult 无参数时默认查看当前会话，附加 UMO 可远程管理其他群聊"
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

        # 仅接受纯数字 AppID，URL 链接由自动解析处理器负责
        if not re.match(r"^\d+$", appid_str):
            yield event.plain_result("请输入 Steam AppID（纯数字）\n如需通过商店链接查询，请直接发送链接（需开启自动解析）")
            return
        appid = int(appid_str)

        cc = self._cc()
        lang = self._lang()
        # 优先级：内联参数 > 会话覆盖 > WebUI 全局默认
        rlang = inline_rlang if inline_rlang else self._review_lang(event.unified_msg_origin)
        game, cc = await self._query_with_fallback(appid, cc, lang, review_lang=rlang)

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
        game, cc = await self._query_with_fallback(appid, cc, lang, review_lang=self._review_lang(event.unified_msg_origin))

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
    # 正则指令：/steam_adult on|off|status [UMO]（成人内容屏蔽名单切换）
    # ------------------------------------------------------------------

    @filter.regex(r"^/?steam_adult(?:\s+\S+){0,2}")
    async def cmd_toggle_adult(self, event: AstrMessageEvent):
        """切换 R18 截图屏蔽名单。用法：/steam_adult on|off|status [UMO]
        可在任意会话中通过附加 UMO 参数远程管理任意群聊；省略 UMO 时默认对当前会话操作。
        """
        if not await self._acl.check_access(event.unified_msg_origin):
            yield event.plain_result("权限不足：您所在的群组或账户未被授权使用此功能。")
            return

        current_umo = event.unified_msg_origin or ""
        m = re.search(
            r"^/?steam_adult(?:\s+(\S+))?(?:\s+(\S+))?",
            event.message_str.strip(),
            re.IGNORECASE,
        )
        action = (m.group(1).lower() if m and m.group(1) else "status")
        target_umo = (m.group(2) if m and m.group(2) else current_umo).strip()

        current_list = self._adult_block_list()
        in_list = target_umo in current_list

        if action == "status":
            count = len(current_list)
            if target_umo:
                state = "已屏蔽 R18 截图" if in_list else "默认放行（不屏蔽 R18 截图）"
                target_line = f"目标 UMO：{target_umo}\n目标状态：{state}\n"
            else:
                target_line = "目标 UMO：(未指定，且当前会话 UMO 不可用)\n"
            preview = "、".join(current_list[:5]) if current_list else "(空)"
            more = f"… 等共 {count} 条" if count > 5 else ""
            yield event.plain_result(
                f"R18 截图屏蔽名单状态：\n"
                f"{target_line}"
                f"屏蔽名单容量：{count}\n"
                f"前若干条：{preview}{more}\n"
                f"用法：/steam_adult on|off|status [UMO]\n"
                f"  - 省略 UMO 时对当前会话操作\n"
                f"  - 附加 UMO 可远程管理任意会话，例如：/steam_adult on aiocqhttp:GroupMessage:123456"
            )
            return

        if action not in {"on", "off"}:
            yield event.plain_result("参数无效。用法：/steam_adult on|off|status [UMO]")
            return

        if not target_umo:
            yield event.plain_result(
                "未指定目标 UMO，且当前会话 UMO 不可用。\n"
                "用法：/steam_adult on|off {UMO}，例如 /steam_adult on aiocqhttp:GroupMessage:123456"
            )
            return

        # 简单格式校验：UMO 通常形如 platform:MessageType:id
        if ":" not in target_umo:
            yield event.plain_result(
                f"目标 UMO 格式可疑（缺少冒号分隔）：{target_umo}\n"
                "完整 UMO 形如 aiocqhttp:GroupMessage:123456，可向目标会话发送 /sid 获取。"
            )
            return

        # on  = 启用屏蔽 = 加入屏蔽名单
        # off = 关闭屏蔽 = 从屏蔽名单移除（默认全局放行）
        if action == "on":
            if in_list:
                yield event.plain_result(f"目标 UMO 已在屏蔽名单中，无需修改：{target_umo}")
                return
            new_list = current_list + [target_umo]
            persisted = self._persist_adult_block_list(new_list)
            tail = "（已持久化到配置）" if persisted else "（仅内存生效，重启后恢复）"
            yield event.plain_result(
                f"已将 UMO 加入 R18 截图屏蔽名单：{target_umo}\n后续该会话的 /steam_shots 将拒绝发送成人内容截图{tail}。"
            )
            return

        # action == "off"
        if not in_list:
            yield event.plain_result(f"目标 UMO 未在屏蔽名单中（默认放行），无需修改：{target_umo}")
            return
        new_list = [x for x in current_list if x != target_umo]
        persisted = self._persist_adult_block_list(new_list)
        tail = "（已持久化到配置）" if persisted else "（仅内存生效，重启后恢复）"
        yield event.plain_result(f"已将 UMO 从 R18 截图屏蔽名单中移除：{target_umo}{tail}")

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

        game, _cc = await self._query_with_fallback(appid, self._cc(), self._lang(), enrich=False)

        if game.error:
            yield event.plain_result(f"查询失败：{game.error}")
            return

        # F3：成人内容防护（默认全局屏蔽，仅豁免名单内的会话允许 R18）
        # required_age >= 18 是明确年龄门控；content_descriptor_ids 含 1/3/4 是 Steam
        # 对性内容的描述符，两者取并集以覆盖 required_age=0 但实为成人内容的游戏
        _ADULT_DESCRIPTOR_IDS = {1, 3, 4}
        is_adult = (
            game.required_age >= 18
            or bool(_ADULT_DESCRIPTOR_IDS & set(game.content_descriptor_ids))
        )
        if is_adult and self._is_adult_blocked(event.unified_msg_origin):
            yield event.plain_result(
                f"【{game.name}】被标记为成人内容（年龄限制 {game.required_age}+），已在当前会话屏蔽截图发送。\n"
                "如需在当前会话放行，请管理员发送 /steam_adult off 将本会话从屏蔽名单中移除。"
            )
            return

        if not game.screenshots:
            yield event.plain_result(f"【{game.name}】暂无截图数据")
            return

        try:
            max_count = max(1, min(15, int(self.config.get("max_screenshots", 6))))
        except (TypeError, ValueError):
            max_count = 6
        # 先过滤全部有效 URL，再取前 max_count 张（避免先切片后因缺少 URL 导致实际数量不足）
        thumb_urls = [
            s["path_thumbnail"]
            for s in game.screenshots
            if s.get("path_thumbnail")
        ][:max_count]
        if not thumb_urls:
            yield event.plain_result(f"【{game.name}】无可用截图 URL")
            return

        # 下载截图原始字节（拼图前不单张压缩，统一交给 stitch_images_vertical 处理）
        images_raw: list[bytes] = []
        failed = 0
        for url in thumb_urls:
            try:
                img_data = await self._client.download_bytes(url)
                images_raw.append(img_data)
            except SteamAPIError as e:
                logger.warning(f"[steam] 截图下载失败: {e}")
                failed += 1
            except Exception as e:
                logger.warning(f"[steam] 截图下载异常: {type(e).__name__}: {e}")
                failed += 1

        if not images_raw:
            yield event.plain_result(
                f"【{game.name}】所有截图下载失败（共 {failed} 张），请检查网络或代理配置后重试"
            )
            return

        # 垂直拼接为长图，以普通图片消息发送（避免合并转发的 QQ 兼容性问题）
        try:
            stitched = await stitch_images_vertical(
                images_raw,
                target_width=self._screenshot_width(),
                target_kb=self._stitch_target_kb(),
                quality=72,
            )
        except Exception as e:
            logger.warning(f"[steam] 截图拼接失败: {type(e).__name__}: {e}")
            yield event.plain_result(f"【{game.name}】截图拼接失败，请稍后重试")
            return

        if self._get_aiocqhttp_send_target(event) is not None:
            # 重试最多 3 次：每次重新拼接，借助 image_utils 中 1-4px 随机白边产出不同字节，
            # 尝试绕过 QQ 服务端可能的图像哈希黑名单。每次发送 3s 超时立即判败。
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                if attempt == 1:
                    payload = stitched
                else:
                    try:
                        payload = await stitch_images_vertical(
                            images_raw,
                            target_width=self._screenshot_width(),
                            target_kb=self._stitch_target_kb(),
                            quality=72,
                        )
                    except Exception as e:
                        logger.warning(f"[steam] 截图重试拼接失败({attempt}/{max_attempts}): {type(e).__name__}: {e}")
                        continue

                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp.write(payload)
                    tmp_path = tmp.name
                event.track_temporary_local_file(tmp_path)

                if await self._send_aiocqhttp_local_image(event, tmp_path):
                    # 直接通过 OneBot 发送图片成功后，必须显式终止事件链路，
                    # 否则 AstrBot pipeline 会因为没有 yield 任何 result 而判定事件未处理，
                    # 继续把原始 "/steam_shots <appid>" 文本喂给 LLM agent，触发 web_fetch 等工具调用。
                    event.stop_event()
                    return
                logger.info(f"[steam] 截图长图直发第 {attempt}/{max_attempts} 次失败")

            # 全部重试失败 → 判定为 QQ 风控/审核拦截，不再返回链接（链接也可能再次触发审核）
            yield event.plain_result(
                f"【{game.name}】截图发送失败：经多次尝试仍被 QQ 平台风控拦截，本次无法获取该游戏截图。"
            )
            return

        # 非 aiocqhttp 平台保留原有通用方案：拼成长图后由 AstrBot 常规图片通道发送。
        yield event.make_result().base64_image(base64.b64encode(stitched).decode())

    # ------------------------------------------------------------------
    # 搜索辅助方法
    # ------------------------------------------------------------------

    def _enhanced_search_enabled(self) -> bool:
        """检查增强搜索是否启用（enhanced_search=true 且 itad_api_key 非空）。"""
        if not self.config.get("enhanced_search", False):
            return False
        itad_key = str(self.config.get("itad_api_key", "")).strip()
        return bool(itad_key) and self._itad_client is not None

    def _search_max_results(self) -> int:
        try:
            return max(1, min(10, int(self.config.get("search_max_results", 5))))
        except (TypeError, ValueError):
            return 5

    async def _llm_validate_search(
        self, keyword: str, results: list[dict]
    ) -> dict:
        """
        调用 LLM 评估搜索结果与用户意图的匹配度。
        返回 {"match_level": "high"|"low", "matched_indices": [int], "is_single_precise": bool}
        LLM 不可用或解析失败时返回默认高匹配（不阻断流程）。
        """
        default_result = {
            "match_level": "high",
            "matched_indices": list(range(len(results))),
            "is_single_precise": len(results) == 1,
        }

        try:
            provider = self.context.get_using_provider()
        except Exception:
            logger.debug("[steam] LLM Provider 不可用，跳过搜索校验")
            return default_result

        # 构建结果列表文本
        items_text = "\n".join(
            f"  {i}. {r.get('name', '未知')} (AppID {r.get('appid', '?')}) — {r.get('price', '未知')}"
            for i, r in enumerate(results)
        )

        prompt = (
            f"你是一个 Steam 游戏搜索结果评估器。你的唯一任务是评估下方列表中的结果与用户搜索意图的匹配度。\n"
            f"你绝对不能自行搜索、联想、推荐或补充任何不在列表中的游戏。\n\n"
            f"用户搜索了「{keyword}」。\n\n"
            f"以下是 Steam 商店搜索返回的结果列表（仅限这些结果，不要引入其他游戏）：\n{items_text}\n\n"
            f"评估规则：\n"
            f"1. 仅评估列表中已有的结果，不要自行补充或推荐任何不在列表中的游戏。\n"
            f"2. 如果用户搜索的是一个游戏系列的通用名（如\"Dark Souls\"、\"Hollow Knight\"），"
            f"该系列的所有续作/衍生作都应视为高匹配度。\n"
            f"3. 如果用户搜索中包含了明确的编号或副标题（如\"Dark Souls 3\"、\"Hollow Knight Silksong\"），"
            f"则只有对应的那一作是高匹配度。\n"
            f"4. DLC、原声带、粉丝扩展等衍生内容应视为低匹配度，除非用户明确搜索了它们。\n"
            f"5. 列表中的游戏名与用户搜索意图完全不相关时为低匹配度。\n\n"
            f"返回 JSON（只返回 JSON，不要返回其他内容）：\n"
            f'{{"match_level": "high" 或 "low", "matched_indices": [匹配的结果序号，从0开始], '
            f'"is_single_precise": true 或 false, "reason": "简短说明"}}'
        )

        try:
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    image_urls=[],
                    func_tool=None,
                    system_prompt=(
                        "你是一个 Steam 游戏搜索结果评估器。你的唯一职责是评估给定列表中游戏与用户搜索意图的匹配度。"
                        "严禁自行搜索、联想、推荐或补充任何不在列表中的游戏。只返回 JSON 格式的结果。"
                    ),
                ),
                timeout=15.0,
            )
            text = response.completion_text.strip()
            # 尝试提取 JSON（可能被 markdown 代码块包裹）
            json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
            if json_match:
                parsed = _json.loads(json_match.group())
                match_level = parsed.get("match_level", "high")
                matched_indices = parsed.get("matched_indices", [])
                is_single_precise = parsed.get("is_single_precise", False)
                # 校验 matched_indices 范围
                matched_indices = [i for i in matched_indices if 0 <= i < len(results)]
                if not matched_indices:
                    matched_indices = list(range(len(results)))
                logger.info(
                    f"[steam] LLM 搜索校验: level={match_level} "
                    f"indices={matched_indices} single={is_single_precise} "
                    f"reason={parsed.get('reason', '')}"
                )
                return {
                    "match_level": match_level,
                    "matched_indices": matched_indices,
                    "is_single_precise": bool(is_single_precise),
                }
        except asyncio.TimeoutError:
            logger.warning("[steam] LLM 搜索校验超时（15s），回退为默认。请检查 LLM Provider 网络连接或代理配置")
        except Exception as e:
            logger.warning(f"[steam] LLM 搜索校验失败（回退为默认）: {type(e).__name__}: {e}。请检查 LLM Provider 配置")

        return default_result

    async def _translate_to_english(self, chinese_text: str) -> str:
        """调用 LLM 将中文翻译为 Steam 英文游戏名。失败时返回原文。"""
        try:
            provider = self.context.get_using_provider()
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=(
                        f"请将以下游戏名翻译为 Steam 商店页面上的英文官方名称。\n"
                        f"规则：仅输出英文名，不要输出其他任何内容。如果该游戏不在 Steam 平台上，"
                        f"请原样输出输入的游戏名，不要编造或联想其他游戏。\n"
                        f"游戏名：{chinese_text}"
                    ),
                    contexts=[],
                    image_urls=[],
                    func_tool=None,
                    system_prompt="你是一个游戏名称翻译器。仅将中文游戏名翻译为 Steam 商店的英文官方名称。不要编造、联想或推荐任何游戏。仅输出英文名。",
                ),
                timeout=15.0,
            )
            translated = response.completion_text.strip()
            if translated:
                logger.info(f"[steam] LLM 翻译: {chinese_text!r} -> {translated!r}")
                return translated
        except asyncio.TimeoutError:
            logger.warning("[steam] LLM 翻译超时（15s），使用原始关键词。请检查 LLM Provider 网络连接或代理配置")
        except Exception as e:
            logger.warning(f"[steam] LLM 翻译失败: {type(e).__name__}: {e}。请检查 LLM Provider 配置")
        return chinese_text

    async def _download_image_bytes(self, url: str) -> bytes | None:
        """下载图片字节，失败时返回 None。"""
        if not url:
            return None
        try:
            return await self._client.download_bytes(url)
        except Exception as e:
            logger.debug(f"[steam] 搜索封面图下载失败: {type(e).__name__}: {e}")
            return None

    async def _send_search_results(
        self,
        event: AstrMessageEvent,
        results: list[dict],
        keyword: str,
    ):
        """
        逐条发送搜索结果：封面缩略图 + 文字描述。
        每条结果作为一个独立消息发送（图片+文字），实现"文字夹带封面缩略图"效果。
        图片发送失败时跳过该条图片，仅发送文字。
        """
        # 先发送标题行
        yield event.plain_result(
            f"🔍 搜索「{keyword}」找到 {len(results)} 个相关结果："
        )

        # 逐条发送结果
        for i, item in enumerate(results):
            name = item.get("name", "未知游戏")
            appid = item.get("appid", "")
            price = item.get("price", "")
            image_url = item.get("image_url", "")

            # 构建文字描述
            text = f"  {i + 1}. {name}（AppID {appid}）"
            if price:
                text += f"\n     💰 {price}"

            # 尝试附带封面缩略图发送
            if image_url:
                result = event.make_result().url_image(image_url).message(text)
                yield result
            else:
                yield event.plain_result(text)

    # ------------------------------------------------------------------
    # 指令：/steam_search（游戏搜索）
    # ------------------------------------------------------------------

    @filter.command("steam_search")
    async def cmd_steam_search(self, event: AstrMessageEvent):
        """搜索 Steam 游戏。用法：/steam_search {关键词}"""
        if not await self._acl.check_access(event.unified_msg_origin):
            yield event.plain_result("权限不足：您所在的群组或账户未被授权使用此功能。")
            return

        arg = re.sub(
            r"^/?steam_search\s*", "", event.message_str.strip(), flags=re.IGNORECASE
        ).strip()
        if not arg:
            yield event.plain_result(
                "用法：/steam_search {关键词}\n"
                "支持中英文关键词，如 /steam_search Dark Souls 或 /steam_search 怪物猎人"
            )
            return

        keyword = arg[:100]  # 截断超长关键词
        max_results = self._search_max_results()
        enhanced = self._enhanced_search_enabled()

        # ── 方案 B：Steam /search/suggest ──
        results: list[dict] = []
        try:
            results = await self._client.search_suggest(keyword, self._cc(), self._lang())
        except SteamAPIError as e:
            logger.warning(f"[steam] 搜索 suggest 失败: {e}")

        # 限制结果数量（不再回退 /search/results/，因其会返回无关推荐游戏）
        results = results[:max_results]

        # ── 增强搜索关闭：纯 Steam 搜索，无 LLM、无 ITAD ──
        if not enhanced:
            if results:
                async for r in self._send_search_results(event, results, keyword):
                    yield r
            else:
                yield event.plain_result(f"🔍 搜索「{keyword}」未找到相关游戏")
            return

        # ── 以下为增强搜索流程（需要 LLM + ITAD）──

        # ── LLM 校验 #1 ──
        if results:
            validation = await self._llm_validate_search(keyword, results)
            match_level = validation["match_level"]
            matched_indices = validation["matched_indices"]
            is_single_precise = validation["is_single_precise"]

            if match_level == "high" and is_single_precise and len(matched_indices) == 1:
                # 精准匹配 → 直出完整游戏信息
                precise_appid = results[matched_indices[0]]["appid"]
                logger.info(f"[steam] 搜索精准匹配 AppID {precise_appid}，直出完整信息")
                game, cc = await self._query_with_fallback(
                    precise_appid, self._cc(), self._lang(),
                    review_lang=self._review_lang(event.unified_msg_origin),
                )
                if not game.error:
                    if game.short_description:
                        max_len = self._max_desc()
                        if len(game.short_description) > max_len:
                            game.short_description = game.short_description[:max_len] + "..."
                    text, image_url = formatter.format_game_info(game, cc)
                    result = event.make_result().message(text)
                    if image_url:
                        result = result.url_image(image_url)
                    yield result
                    return
                # appdetails 查询失败，回退到搜索卡片
                logger.warning(f"[steam] 精准匹配 AppID {precise_appid} 查询失败，回退搜索卡片")

            if match_level == "high":
                # 多条匹配（系列续作等）→ 输出搜索卡片
                filtered = [results[i] for i in matched_indices if i < len(results)]
                if filtered:
                    async for r in self._send_search_results(event, filtered, keyword):
                        yield r
                    return

            # LLM 判定匹配度低 → 进入 ITAD 增强搜索
        else:
            # Steam 无结果 → 进入 ITAD 增强搜索
            pass

        # 增强搜索：LLM 翻译中文 → ITAD 搜索
        search_keyword = keyword
        if re.search(r"[\u4e00-\u9fff]", keyword):
            search_keyword = await self._translate_to_english(keyword)

        itad_results: list[dict] = []
        if self._itad_client:
            try:
                itad_results = await self._itad_client.search_games(
                    search_keyword, limit=max_results
                )
            except Exception as e:
                logger.warning(f"[steam] ITAD 搜索失败: {type(e).__name__}: {e}")

        if not itad_results:
            if results:
                # ITAD 也无结果，但 Steam 有结果，展示 Steam 结果
                async for r in self._send_search_results(event, results, keyword):
                    yield r
            else:
                yield event.plain_result(f"🔍 搜索「{keyword}」未找到相关游戏")
            return

        # ITAD 结果需要补充 AppID（从 ITAD info 获取）和价格信息（从 Steam 获取）
        enriched_itad: list[dict] = []
        for item in itad_results:
            appid = item.get("appid")
            itad_id = item.get("id", "")

            # AppID 缺失时通过 ITAD info 接口补充获取
            if not appid and itad_id and self._itad_client:
                try:
                    info = await self._itad_client.fetch_game_info(itad_id)
                    if isinstance(info, dict):
                        appid = info.get("appid")
                        if appid:
                            logger.debug(f"[steam] ITAD 补充 AppID: {itad_id} -> {appid}")
                except Exception as e:
                    logger.debug(f"[steam] ITAD 补充 AppID 失败: {e}")

            price_str = ""
            if appid:
                try:
                    game_info = await self._service.get_game_info(
                        appid, self._cc(), self._lang(), enrich=False
                    )
                    if not game_info.error and game_info.price_overview:
                        p = game_info.price_overview
                        price_str = p.final_formatted or ""
                    elif game_info.is_free:
                        price_str = "Free"
                except Exception:
                    pass
            # 过滤掉 AppID 无效的结果（0 或 None），这些游戏不在 Steam 上
            if not appid or appid == 0:
                logger.debug(f"[steam] ITAD 结果 AppID 无效，跳过: {item.get('title', '?')}")
                continue
            enriched_itad.append({
                "appid": appid,
                "name": item.get("title", "未知游戏"),
                "price": price_str,
                "image_url": item.get("image_url", ""),
            })

        # ── LLM 校验 #2（ITAD 结果）──
        validation2 = await self._llm_validate_search(keyword, enriched_itad)
        match_level2 = validation2["match_level"]
        matched_indices2 = validation2["matched_indices"]
        is_single_precise2 = validation2["is_single_precise"]

        if match_level2 == "high" and is_single_precise2 and len(matched_indices2) == 1:
            precise_appid = enriched_itad[matched_indices2[0]].get("appid")
            if precise_appid:
                logger.info(f"[steam] ITAD 搜索精准匹配 AppID {precise_appid}，直出完整信息")
                game, cc = await self._query_with_fallback(
                    precise_appid, self._cc(), self._lang(),
                    review_lang=self._review_lang(event.unified_msg_origin),
                )
                if not game.error:
                    if game.short_description:
                        max_len = self._max_desc()
                        if len(game.short_description) > max_len:
                            game.short_description = game.short_description[:max_len] + "..."
                    text, image_url = formatter.format_game_info(game, cc)
                    result = event.make_result().message(text)
                    if image_url:
                        result = result.url_image(image_url)
                    yield result
                    return
                logger.warning(f"[steam] ITAD 精准匹配 AppID {precise_appid} 查询失败，回退搜索卡片")

        if match_level2 == "high":
            filtered2 = [enriched_itad[i] for i in matched_indices2 if i < len(enriched_itad)]
            if filtered2:
                async for r in self._send_search_results(event, filtered2, keyword):
                    yield r
                return

        # 匹配度低 → 返回未找到
        yield event.plain_result(f"🔍 搜索「{keyword}」暂未搜索到相关内容")

