"""
image_utils.py — 图片压缩与渲染工具

设计原则（参考 astrbot_plugin_Xagent_searcher/core/media_processor.py）：
- PIL CPU 密集型任务通过 asyncio.to_thread() 委派线程池，不阻塞事件循环
- 压缩优先级：target_kb > 0 时二分搜索；否则固定质量压缩
- GIF 动图不压缩，透明通道处理为白底 JPEG
"""

import asyncio
import io
import random

from astrbot.api import logger
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont


def _compress_sync(img_data: bytes, target_kb: int = 0, quality: int = 85) -> bytes:
    """
    同步压缩图片字节（设计为在 asyncio.to_thread 中执行）。

    Args:
        img_data:  原始图片字节
        target_kb: 目标体积（KB），0 表示仅按 quality 压缩
        quality:   压缩质量上限（1-100）

    Returns:
        压缩后字节（若压缩后反而更大则返回原数据）
    """
    try:
        with io.BytesIO(img_data) as buf:
            with PILImage.open(buf) as img:
                src_fmt = (img.format or "").upper()

                # GIF 不压缩（保持帧序列完整性）
                if src_fmt == "GIF":
                    return img_data

                quality = max(1, min(100, int(quality)))
                target_kb = max(0, int(target_kb))

                # 转为 JPEG 兼容模式（处理透明通道）
                if img.mode in ("RGBA", "LA"):
                    bg = PILImage.new("RGB", img.size, (255, 255, 255))
                    alpha = img.split()[-1]
                    bg.paste(img.convert("RGBA"), mask=alpha)
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                def _save(q: int) -> bytes:
                    out = io.BytesIO()
                    img.save(out, format="JPEG", quality=q, optimize=True, progressive=True)
                    return out.getvalue()

                # === 按目标体积二分搜索 ===
                if target_kb > 0:
                    target_bytes = target_kb * 1024
                    if len(img_data) <= target_bytes:
                        return img_data
                    low, high, best = 10, quality, None
                    while low <= high:
                        mid = (low + high) // 2
                        candidate = _save(mid)
                        if len(candidate) <= target_bytes:
                            best = candidate
                            low = mid + 1
                        else:
                            high = mid - 1
                    if best:
                        return best
                    # 最低质量兜底
                    fallback = _save(10)
                    return fallback if len(fallback) < len(img_data) else img_data

                # === 固定质量压缩 ===
                if quality >= 100:
                    return img_data
                candidate = _save(quality)
                return candidate if len(candidate) < len(img_data) else img_data

    except Exception as e:
        logger.warning(f"[image_utils] 图片压缩失败，返回原始数据：{type(e).__name__}: {e}")
        return img_data


async def compress_image(img_data: bytes, target_kb: int = 0, quality: int = 85) -> bytes:
    """
    异步图片压缩入口。PIL 压缩在线程池中执行，不阻塞事件循环。

    Args:
        img_data:  原始图片字节
        target_kb: 目标体积 KB，0 表示仅按 quality 压缩
        quality:   JPEG 压缩质量上限（1-100）

    Returns:
        压缩后字节，失败时原样返回
    """
    if not img_data:
        return img_data
    return await asyncio.to_thread(_compress_sync, img_data, target_kb, quality)


def _stitch_vertical_sync(
    images_bytes: list[bytes],
    target_width: int = 600,
    gap: int = 8,
    quality: int = 85,
    target_kb: int = 0,
) -> bytes:
    """
    将多张图片垂直拼接为单列长图（设计为在 asyncio.to_thread 中执行）。

    Args:
        images_bytes: 原始图片字节列表，单张解析失败时跳过并记录警告
        target_width: 每张图等比缩放到的宽度（px）
        gap:          图间白色分隔条高度（px）
        quality:      输出 JPEG 质量（1-100）

    Returns:
        拼接后的 JPEG 字节

    Raises:
        ValueError: 所有图片均解析失败时抛出
    """
    frames: list[PILImage.Image] = []
    for raw in images_bytes:
        try:
            with io.BytesIO(raw) as buf:
                img = PILImage.open(buf)
                img.load()  # 在 BytesIO 关闭前加载完整像素数据
            # 透明通道转白底 RGB
            if img.mode == "RGBA":
                bg = PILImage.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            # 仅向下缩放至 target_width，不将小图放大（放大会无谓增加文件体积）
            if img.width > target_width:
                new_h = max(1, round(img.height * target_width / img.width))
                img = img.resize((target_width, new_h), PILImage.LANCZOS)
            frames.append(img)
        except Exception as e:
            logger.warning(f"[image_utils] 拼图跳过一张（解析失败）：{type(e).__name__}: {e}")

    if not frames:
        raise ValueError("所有图片均解析失败，无法拼接")

    # 画布宽度以最宽帧为准（旧逐帧居中粘贴）
    canvas_width = max(f.width for f in frames)
    # 哈希抖动：在画布底部追加 1~4 像素的白色留白，让相同输入每次产出不同的
    # JPEG 字节流，从而绕过 QQ 服务端基于精确哈希的图片黑名单（视觉上不可感知）。
    extra_bottom = random.randint(1, 4)
    total_h = sum(f.height for f in frames) + gap * (len(frames) - 1) + extra_bottom
    canvas = PILImage.new("RGB", (canvas_width, total_h), (255, 255, 255))
    y = 0
    for i, frame in enumerate(frames):
        x_off = (canvas_width - frame.width) // 2  # 窄帧居中
        canvas.paste(frame, (x_off, y))
        y += frame.height
        if i < len(frames) - 1:
            y += gap  # canvas 底色为白，间隙处无需额外绘制

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    result = out.getvalue()

    # 体积封顶：若超出 target_kb 限制，二分搜索降质量
    if target_kb > 0:
        target_bytes = target_kb * 1024
        if len(result) > target_bytes:
            def _save(q: int) -> bytes:
                buf = io.BytesIO()
                canvas.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                return buf.getvalue()
            low, high, best = 10, quality - 1, None
            while low <= high:
                mid = (low + high) // 2
                candidate = _save(mid)
                if len(candidate) <= target_bytes:
                    best = candidate
                    low = mid + 1
                else:
                    high = mid - 1
            result = best if best else _save(10)

    return result


async def stitch_images_vertical(
    images_bytes: list[bytes],
    target_width: int = 600,
    gap: int = 8,
    quality: int = 85,
    target_kb: int = 0,
) -> bytes:
    """
    异步垂直拼图入口。PIL 操作在线程池中执行，不阻塞事件循环。
    失败时向上抛出异常，由调用方处理。
    """
    return await asyncio.to_thread(_stitch_vertical_sync, images_bytes, target_width, gap, quality, target_kb)


# ------------------------------------------------------------------
# 搜索结果卡片渲染
# ------------------------------------------------------------------

# 卡片布局常量
_CARD_PADDING = 16          # 卡片内边距
_CARD_GAP = 10              # 条目间距
_THUMB_HEIGHT = 69          # 封面缩略图高度（与 Steam capsule_231x87 等比缩放）
_TEXT_LEFT_MARGIN = 12      # 缩略图与文字之间的水平间距
_HEADER_HEIGHT = 48         # 标题行高度
_LINE_SPACING = 4           # 文字行间距
_BG_COLOR = (35, 42, 57)    # 深色背景（Steam 风格）
_TEXT_COLOR = (255, 255, 255)
_SUBTEXT_COLOR = (178, 190, 207)
_HEADER_TEXT_COLOR = (102, 196, 255)


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """尝试加载系统字体，失败时回退到 PIL 默认字体。"""
    # Windows 常见中文字体路径
    _FONT_CANDIDATES = [
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/msyhbd.ttc",      # 微软雅黑粗体
        "C:/Windows/Fonts/simhei.ttf",      # 黑体
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
        "/System/Library/Fonts/PingFang.ttc",  # macOS
    ]
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _render_search_card_sync(
    results: list[dict],
    keyword: str,
    target_width: int = 600,
    target_kb: int = 200,
    quality: int = 80,
) -> bytes:
    """
    将搜索结果渲染为带封面图+文字的垂直长图（同步，设计为在 asyncio.to_thread 中执行）。

    Args:
        results: [{"name": str, "appid": int, "price": str, "image_bytes": bytes|None}, ...]
        keyword: 搜索关键词（用于标题行）
        target_width: 卡片宽度（px）
        target_kb: 输出体积上限（KB），0 = 不限制
        quality: JPEG 输出质量

    Returns:
        JPEG 字节

    Raises:
        ValueError: 结果列表为空时抛出
    """
    if not results:
        raise ValueError("搜索结果为空，无法渲染卡片")

    font_title = _get_font(18)
    font_name = _get_font(16)
    font_sub = _get_font(13)
    font_header = _get_font(15)

    thumb_w = _THUMB_HEIGHT  # 正方形缩略图宽度 = 高度

    # ── 预处理每条结果的缩略图和文字行 ──
    rows: list[dict] = []
    for i, item in enumerate(results):
        # 缩略图
        thumb_img = None
        img_bytes = item.get("image_bytes")
        if img_bytes:
            try:
                with io.BytesIO(img_bytes) as buf:
                    thumb_img = PILImage.open(buf)
                    thumb_img.load()
                # 等比缩放到 _THUMB_HEIGHT 高度
                ratio = _THUMB_HEIGHT / thumb_img.height
                new_w = max(1, round(thumb_img.width * ratio))
                thumb_img = thumb_img.resize((new_w, _THUMB_HEIGHT), PILImage.LANCZOS)
                # 裁剪为正方形（居中裁剪）
                if thumb_img.width > thumb_w:
                    left = (thumb_img.width - thumb_w) // 2
                    thumb_img = thumb_img.crop((left, 0, left + thumb_w, _THUMB_HEIGHT))
                # 转 RGB
                if thumb_img.mode != "RGB":
                    bg = PILImage.new("RGB", thumb_img.size, _BG_COLOR)
                    if thumb_img.mode == "RGBA":
                        bg.paste(thumb_img, mask=thumb_img.split()[-1])
                    else:
                        bg.paste(thumb_img.convert("RGB"))
                    thumb_img = bg
            except Exception:
                thumb_img = None

        # 文字行
        name = item.get("name", "未知游戏")
        appid = item.get("appid", "")
        price = item.get("price", "")
        line1 = f"{i + 1}. {name}"
        line2 = f"AppID {appid}"
        if price:
            line2 += f"  ·  {price}"

        rows.append({
            "thumb": thumb_img,
            "line1": line1,
            "line2": line2,
        })

    # ── 计算画布尺寸 ──
    text_x = _CARD_PADDING + thumb_w + _TEXT_LEFT_MARGIN
    text_area_w = target_width - text_x - _CARD_PADDING

    # 标题行高度
    total_h = _CARD_PADDING + _HEADER_HEIGHT + _CARD_GAP

    # 每条结果的高度
    row_heights: list[int] = []
    for row in rows:
        # 两行文字 + 行间距，取与缩略图高度的较大值
        text_h = 18 + _LINE_SPACING + 14  # line1(18px) + spacing + line2(14px)
        rh = max(_THUMB_HEIGHT, text_h) + _CARD_GAP
        row_heights.append(rh)
        total_h += rh

    total_h += _CARD_PADDING - _CARD_GAP  # 最后一条不需要 gap，但需要 padding

    # ── 创建画布 ──
    canvas = PILImage.new("RGB", (target_width, total_h), _BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    # ── 绘制标题行 ──
    header_text = f"\U0001f50d \u641c\u7d22\u300c{keyword}\u300d  \u627e\u5230 {len(results)} \u4e2a\u76f8\u5173\u7ed3\u679c"
    draw.text((_CARD_PADDING, _CARD_PADDING), header_text, fill=_HEADER_TEXT_COLOR, font=font_header)

    y = _CARD_PADDING + _HEADER_HEIGHT + _CARD_GAP

    # ── 逐条绘制 ──
    for i, row in enumerate(rows):
        # 缩略图
        if row["thumb"]:
            canvas.paste(row["thumb"], (_CARD_PADDING, y))

        # 文字
        draw.text((text_x, y + 2), row["line1"], fill=_TEXT_COLOR, font=font_name)
        draw.text((text_x, y + 2 + 18 + _LINE_SPACING), row["line2"], fill=_SUBTEXT_COLOR, font=font_sub)

        # 分隔线（非最后一条）
        if i < len(rows) - 1:
            sep_y = y + row_heights[i] - _CARD_GAP // 2
            draw.line(
                [(_CARD_PADDING, sep_y), (target_width - _CARD_PADDING, sep_y)],
                fill=(55, 65, 85),
                width=1,
            )

        y += row_heights[i]

    # ── 输出 JPEG ──
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    result = out.getvalue()

    # 体积封顶
    if target_kb > 0:
        target_bytes = target_kb * 1024
        if len(result) > target_bytes:
            def _save(q: int) -> bytes:
                buf = io.BytesIO()
                canvas.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                return buf.getvalue()
            low, high, best = 10, quality - 1, None
            while low <= high:
                mid = (low + high) // 2
                candidate = _save(mid)
                if len(candidate) <= target_bytes:
                    best = candidate
                    low = mid + 1
                else:
                    high = mid - 1
            result = best if best else _save(10)

    return result


async def render_search_results_card(
    results: list[dict],
    keyword: str,
    target_width: int = 600,
    target_kb: int = 200,
    quality: int = 80,
) -> bytes:
    """
    异步搜索结果卡片渲染入口。PIL 操作在线程池中执行，不阻塞事件循环。
    失败时向上抛出异常，由调用方处理。
    """
    return await asyncio.to_thread(
        _render_search_card_sync, results, keyword, target_width, target_kb, quality
    )
