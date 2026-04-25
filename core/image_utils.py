"""
image_utils.py — 图片压缩工具

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
