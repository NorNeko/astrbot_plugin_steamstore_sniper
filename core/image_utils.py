"""
image_utils.py — 图片压缩工具

设计原则（参考 astrbot_plugin_Xagent_searcher/core/media_processor.py）：
- PIL CPU 密集型任务通过 asyncio.to_thread() 委派线程池，不阻塞事件循环
- 压缩优先级：target_kb > 0 时二分搜索；否则固定质量压缩
- GIF 动图不压缩，透明通道处理为白底 JPEG
"""

import asyncio
import io

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

    except Exception:
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
