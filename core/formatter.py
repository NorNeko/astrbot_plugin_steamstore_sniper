from ..models.store_models import SteamGameInfo

# 评测语言区的显示名称
_REVIEW_LANG_LABELS: dict[str, str] = {
    "schinese": "简体中文区",
    "tchinese": "繁体中文区",
    "japanese": "日语区",
    "english": "英语区",
    "all": "全部语言",
}

# 评分标签对应的 emoji 氛围色
_SCORE_EMOJI: dict[str, str] = {
    "特别好评": "🥳",
    "好评如潮": "🥳",
    "多半好评": "😊",
    "褒贬不一": "🤔",
    "多半差评": "😞",
    "差评如潮": "😤",
    "压倒性差评": "😤",
    "Overwhelmingly Positive": "🥳",
    "Very Positive": "🥳",
    "Mostly Positive": "😊",
    "Mixed": "🤔",
    "Mostly Negative": "😞",
    "Overwhelmingly Negative": "😤",
}


def format_game_info(game: SteamGameInfo, cc: str) -> tuple[str, str | None]:
    """
    将 SteamGameInfo 格式化为纯文本消息。

    返回值：
        (text, header_image_url)
        text             — 纯文本内容，由 main.py 通过 make_result().message(text) 发送
        header_image_url — 封面图 URL；为 None 时 main.py 不附加图片
    """
    if game.error:
        return f"❌ 查询失败：{game.error}", None

    lines: list[str] = []

    # ── 标题行（游戏名 + AppID）──────────────────────────────────────────
    title = game.name or "未知游戏"
    if game.steam_appid:
        title += f"（AppID {game.steam_appid}）"
    if game.type and game.type != "game":
        title += f"  [{game.type.upper()}]"
    lines.append(f"🎮 {title}")

    # ── 简介 ─────────────────────────────────────────────────────────────
    lines.append("")
    desc = game.short_description or "暂无简介"
    lines.append(f"📋 {desc}")

    # ── 分隔 ─────────────────────────────────────────────────────────────
    lines.append("")

    # ── 标签 / 开发商 / 发行商 ───────────────────────────────────────────
    # 标签优先级：ITAD 用户社区标签 > Steam genres（开发商指定类型）
    if game.itad_tags:
        tags = game.itad_tags[:8]
        extra = len(game.itad_tags) - 8
        tag_str = "、".join(tags)
        if extra > 0:
            tag_str += f"（等{extra}个）"
        lines.append(f"🏷️  {tag_str}")
    elif game.genres:
        lines.append(f"🏷️  {'、'.join(game.genres)}")
    if game.developers:
        lines.append(f"🛠️ 开发商：{'、'.join(game.developers)}")
    if game.publishers:
        lines.append(f"🏢 发行商：{'、'.join(game.publishers)}")

    # ── 价格 ─────────────────────────────────────────────────────────────
    lines.append(f"💰 {format_price_only(game, cc)}")

    # ── Steam 历史最低价（ITAD storelow，有数据时展示）──────────────────
    if game.history_low_price is not None:
        lines.append(_format_history_low(game))

    # ── 订阅服务（有数据时展示）─────────────────────────────────────────
    if game.subscription_services:
        lines.append(f"🎫 可订阅：{'、'.join(game.subscription_services)}")

    # ── 评测 ─────────────────────────────────────────────────────────────
    if game.review_score_desc:
        total = game.review_total_reviews
        lang_label = _REVIEW_LANG_LABELS.get(game.review_lang, game.review_lang)
        score_emoji = _SCORE_EMOJI.get(game.review_score_desc, "📊")
        if total > 0:
            pct = round(game.review_total_positive / total * 100)
            lines.append(
                f"{score_emoji} {game.review_score_desc}"
                f"（{pct}% 好评 · {total:,} 条 · {lang_label}）"
            )
        else:
            lines.append(f"{score_emoji} {game.review_score_desc}（{lang_label}）")

    # ── 当前在线人数 ──────────────────────────────────────────────────────
    if game.current_players is not None:
        lines.append(f"👥 当前在线：{game.current_players:,} 人")

    # ── 发售 / DLC ───────────────────────────────────────────────────────
    lines.append("")
    if game.coming_soon:
        lines.append("📅 发售日期：即将推出")
    elif game.release_date_str:
        lines.append(f"📅 发售日期：{game.release_date_str}")
    if game.dlc_count > 0:
        lines.append(f"📦 关联 DLC：{game.dlc_count} 个")
    if game.has_trading_cards:
        lines.append("🃏 含 Steam 集换卡牌")

    # ── 商店链接 ─────────────────────────────────────────────────────────
    if game.steam_appid:
        lines.append(f"🔗 https://store.steampowered.com/app/{game.steam_appid}/")

    return "\n".join(lines), game.header_image


def format_price_only(game: SteamGameInfo, cc: str) -> str:
    """
    仅返回价格行文本（不含 emoji 前缀）。供 /steam_price 指令单独调用。
    """
    if game.is_free:
        return f"免费游玩（{cc.upper()}）"
    if game.price_overview:
        p = game.price_overview
        if p.discount_percent and p.discount_percent > 0:
            return (
                f"{p.final_formatted}（{cc.upper()}）"
                f"  原价 {p.initial_formatted} · {p.discount_percent}% off 🎉"
            )
        return f"{p.final_formatted}（{cc.upper()}）"
    return f"该地区价格暂不可见（{cc.upper()}）"


def _format_history_low(game: SteamGameInfo) -> str:
    """
    格式化历史最低价行，仅供 format_game_info 内部调用。
    示例：💸 史低 HKD 9.75（-75% · 2024-06-28 · Steam）
    """
    currency = game.history_low_currency or ""
    price_str = f"{game.history_low_price:.2f}"
    price_part = f"{currency} {price_str}".strip()

    details: list[str] = []
    if game.history_low_cut:
        details.append(f"-{game.history_low_cut}%")
    if game.history_low_date:
        details.append(game.history_low_date)
    if game.history_low_shop:
        details.append(game.history_low_shop)

    if details:
        return f"💸 史低 {price_part}（{'  ·  '.join(details)}）"
    return f"💸 史低 {price_part}"


def format_search_results_text(results: list[dict], keyword: str) -> str:
    """
    纯文本格式的搜索结果（图片发送失败时的回退）。
    results: [{"name": str, "appid": int, "price": str}, ...]
    """
    if not results:
        return f"🔍 搜索「{keyword}」未找到相关游戏"

    lines: list[str] = []
    lines.append(f"🔍 搜索「{keyword}」找到 {len(results)} 个相关结果：\n")
    for i, item in enumerate(results):
        name = item.get("name", "未知游戏")
        appid = item.get("appid", "")
        price = item.get("price", "")
        line = f"  {i + 1}. {name}（AppID {appid}）"
        if price:
            line += f"\n     💰 {price}"
        lines.append(line)

    return "\n".join(lines)
