"""
wishlist_manager.py — 群愿望单存储管理层

职责：
- 全局游戏缓存（appid → WishlistGameCache）的 CRUD
- 群级索引（UMO → {appid → [WishAdder]}）的 CRUD
- JSON 文件原子读写（临时文件 + os.replace）
- asyncio.Lock 并发保护
- 层级分类（热/温/冷）
- 夜间模式检测
- 通知队列管理

不负责：
- 网络请求（由 SteamClient / ITADClient 处理）
- 文本格式化（由 formatter 处理）
- AstrBot 指令注册（由 main.py 处理）
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger

from ..models.wishlist_models import (
    WishAdder,
    WishlistGameCache,
    PendingNotification,
    _adder_to_dict,
    _adder_from_dict,
)


class WishlistManager:
    """
    群愿望单管理器。
    所有数据在内存中维护，仅在变化时落盘。
    使用 asyncio.Lock 防止并发读写冲突。
    """

    def __init__(self, data_dir: str | Path):
        """
        Args:
            data_dir: 数据文件存放目录（插件根目录下的 data 子目录）
        """
        self._data_dir = Path(data_dir) / "data"
        self._data_file = self._data_dir / "wishlist_data.json"
        self._lock = asyncio.Lock()

        # ── 内存数据 ──
        # 全局游戏缓存：appid → WishlistGameCache
        self._games: dict[int, WishlistGameCache] = {}
        # 群级索引：UMO → {appid → [WishAdder]}
        self._wishlists: dict[str, dict[int, list[WishAdder]]] = {}
        # 刷新时间戳（分层）
        self._last_refresh: dict[str, float] = {
            "hot": 0.0,
            "warm": 0.0,
            "cold": 0.0,
        }
        # 夜间排队通知
        self._pending_notifications: list[PendingNotification] = []

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def load_from_disk(self) -> None:
        """从 JSON 文件加载数据到内存。文件不存在时静默跳过。"""
        if not self._data_file.exists():
            logger.info("[wishlist] 数据文件不存在，使用空数据初始化")
            return

        try:
            # 读取文件（在线程池中执行，避免阻塞事件循环）
            raw = await asyncio.to_thread(self._data_file.read_text, encoding="utf-8")
            data = _json.loads(raw)
        except Exception as e:
            logger.warning(f"[wishlist] 数据文件读取失败: {e}，使用空数据初始化")
            return

        async with self._lock:
            # 加载游戏缓存
            games_raw = data.get("games") or {}
            for appid_str, game_dict in games_raw.items():
                try:
                    entry = WishlistGameCache.from_dict(game_dict)
                    self._games[entry.appid] = entry
                except Exception as e:
                    logger.warning(f"[wishlist] 游戏缓存反序列化失败 appid={appid_str}: {e}")

            # 加载群愿望单索引
            wishlists_raw = data.get("wishlists") or {}
            for umo, apps_dict in wishlists_raw.items():
                group_wishlist: dict[int, list[WishAdder]] = {}
                for appid_str, adders_raw in apps_dict.items():
                    try:
                        appid = int(appid_str)
                        adders = [_adder_from_dict(a) for a in adders_raw]
                        group_wishlist[appid] = adders
                    except Exception as e:
                        logger.warning(f"[wishlist] 群索引反序列化失败 umo={umo} appid={appid_str}: {e}")
                if group_wishlist:
                    self._wishlists[umo] = group_wishlist

            # 加载刷新时间戳
            refresh_raw = data.get("last_refresh") or {}
            for tier in ("hot", "warm", "cold"):
                ts_str = refresh_raw.get(tier)
                if ts_str:
                    try:
                        self._last_refresh[tier] = datetime.fromisoformat(ts_str).timestamp()
                    except (ValueError, TypeError):
                        pass

            # 加载待发通知
            pending_raw = data.get("pending_notifications") or []
            for notif_dict in pending_raw:
                try:
                    self._pending_notifications.append(PendingNotification.from_dict(notif_dict))
                except Exception as e:
                    logger.warning(f"[wishlist] 待发通知反序列化失败: {e}")

        logger.info(
            f"[wishlist] 数据加载完成: {len(self._games)} 个游戏, "
            f"{len(self._wishlists)} 个群, "
            f"{len(self._pending_notifications)} 条待发通知"
        )

    async def save_to_disk(self) -> None:
        """将内存数据原子写入 JSON 文件（临时文件 + os.replace）。"""
        async with self._lock:
            data = self._serialize()

        try:
            content = _json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            # 原子写入：先写临时文件，再 replace
            await asyncio.to_thread(self._atomic_write, content)
            logger.debug(f"[wishlist] 数据已写入 {self._data_file}")
        except Exception as e:
            logger.warning(f"[wishlist] 数据写入失败: {e}")

    def _atomic_write(self, content: str) -> None:
        """同步原子写入（设计为在 asyncio.to_thread 中执行）。"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._data_dir), suffix=".tmp", prefix="wishlist_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(self._data_file))
        except Exception:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _serialize(self) -> dict:
        """将内存数据序列化为可 JSON 化的字典（需在 lock 内调用）。"""
        # 游戏缓存
        games_dict: dict[str, dict] = {}
        for appid, entry in self._games.items():
            games_dict[str(appid)] = entry.to_dict()

        # 群愿望单索引
        wishlists_dict: dict[str, dict] = {}
        for umo, apps in self._wishlists.items():
            apps_dict: dict[str, list] = {}
            for appid, adders in apps.items():
                apps_dict[str(appid)] = [_adder_to_dict(a) for a in adders]
            wishlists_dict[umo] = apps_dict

        # 刷新时间戳
        refresh_dict: dict[str, str] = {}
        for tier, ts in self._last_refresh.items():
            if ts > 0:
                refresh_dict[tier] = datetime.fromtimestamp(ts).isoformat()

        # 待发通知
        pending_list = [n.to_dict() for n in self._pending_notifications]

        return {
            "last_refresh": refresh_dict,
            "games": games_dict,
            "wishlists": wishlists_dict,
            "pending_notifications": pending_list,
        }

    # ------------------------------------------------------------------
    # 游戏缓存 CRUD
    # ------------------------------------------------------------------

    def get_game(self, appid: int) -> WishlistGameCache | None:
        """从全局缓存获取游戏信息。"""
        return self._games.get(appid)

    def set_game(self, entry: WishlistGameCache) -> None:
        """写入或更新全局游戏缓存。"""
        self._games[entry.appid] = entry

    def remove_game(self, appid: int) -> WishlistGameCache | None:
        """从全局缓存删除游戏，返回被删除的条目（不存在则返回 None）。"""
        return self._games.pop(appid, None)

    def get_all_games(self) -> list[WishlistGameCache]:
        """获取所有缓存的游戏列表。"""
        return list(self._games.values())

    def is_game_referenced(self, appid: int, exclude_umo: str = "") -> bool:
        """检查游戏是否仍被任何群引用（exclude_umo 排除指定群）。"""
        for umo, apps in self._wishlists.items():
            if umo == exclude_umo:
                continue
            if appid in apps:
                return True
        return False

    # ------------------------------------------------------------------
    # 群愿望单 CRUD
    # ------------------------------------------------------------------

    def get_group_wishlist(self, umo: str) -> dict[int, list[WishAdder]]:
        """获取指定群的愿望单字典 {appid → [WishAdder]}。"""
        return self._wishlists.get(umo, {})

    def get_group_appids(self, umo: str) -> list[int]:
        """获取指定群的愿望单 AppID 列表。"""
        return list(self._wishlists.get(umo, {}).keys())

    def add_to_group(self, umo: str, appid: int, adder: WishAdder) -> bool:
        """
        将添加者追加到指定群的指定游戏。
        如果该群尚未关注此游戏，创建新条目。
        如果添加者已存在（按 sender_id 去重），不重复添加。
        返回是否为新增添加者。
        """
        if umo not in self._wishlists:
            self._wishlists[umo] = {}

        group_apps = self._wishlists[umo]

        if appid not in group_apps:
            group_apps[appid] = [adder]
            return True

        # 检查是否已存在
        existing_ids = {a.sender_id for a in group_apps[appid]}
        if adder.sender_id in existing_ids:
            return False

        group_apps[appid].append(adder)
        return True

    def remove_from_group(self, umo: str, appid: int) -> list[WishAdder] | None:
        """
        从指定群移除指定游戏的整条记录。
        返回被移除的添加者列表（不存在则返回 None）。
        """
        group_apps = self._wishlists.get(umo)
        if not group_apps or appid not in group_apps:
            return None

        adders = group_apps.pop(appid)

        # 清理空群
        if not group_apps:
            del self._wishlists[umo]

        return adders

    def get_adders_for_game(self, appid: int) -> dict[str, list[WishAdder]]:
        """
        获取所有群中关注指定游戏的添加者。
        返回 {umo → [WishAdder]}。
        """
        result: dict[str, list[WishAdder]] = {}
        for umo, apps in self._wishlists.items():
            if appid in apps:
                result[umo] = apps[appid]
        return result

    def get_group_game_count(self, umo: str) -> int:
        """获取指定群的愿望单游戏数量。"""
        return len(self._wishlists.get(umo, {}))

    # ------------------------------------------------------------------
    # 刷新时间戳管理
    # ------------------------------------------------------------------

    def get_last_refresh(self, tier: str) -> float:
        """获取指定层级的上次刷新时间戳（monotonic）。"""
        return self._last_refresh.get(tier, 0.0)

    def set_last_refresh(self, tier: str, timestamp: float | None = None) -> None:
        """设置指定层级的刷新时间戳（默认为当前时间）。"""
        self._last_refresh[tier] = timestamp if timestamp is not None else time.monotonic()

    # ------------------------------------------------------------------
    # 待发通知队列
    # ------------------------------------------------------------------

    def add_pending_notification(self, notif: PendingNotification) -> None:
        """添加一条待发通知到队列。"""
        self._pending_notifications.append(notif)

    def get_pending_notifications(self) -> list[PendingNotification]:
        """获取所有待发通知。"""
        return list(self._pending_notifications)

    def clear_pending_notifications(self) -> None:
        """清空待发通知队列。"""
        self._pending_notifications.clear()

    # ------------------------------------------------------------------
    # 层级分类
    # ------------------------------------------------------------------

    @staticmethod
    def classify_game(entry: WishlistGameCache) -> str:
        """
        将游戏分类到热/温/冷层。

        热层：正在打折 或 即将发售（30天内）
        冷层：已发售 + 无史低数据
        温层：其他所有情况
        """
        # 热层：正在打折
        if entry.is_on_sale:
            return "hot"

        # 热层：即将发售（30天内）
        if entry.coming_soon and entry.release_date_str:
            days = WishlistManager._parse_days_until_release(entry.release_date_str)
            if days is not None and 0 < days <= 30:
                return "hot"

        # 冷层：已发售 + 无史低数据
        if entry.is_released and entry.history_low_price is None:
            return "cold"

        # 温层：其他
        return "warm"

    @staticmethod
    def _parse_days_until_release(date_str: str) -> int | None:
        """
        尝试解析发售日期字符串，返回距今天数。
        支持格式：ISO 8601（2026-06-15）、Steam 风格（2026年6月、Jun 2026）。
        解析失败返回 None。
        """
        if not date_str:
            return None

        # 尝试 ISO 8601
        try:
            target = datetime.fromisoformat(date_str)
            delta = (target - datetime.now()).days
            return max(0, delta)
        except ValueError:
            pass

        # 尝试 YYYY-MM-DD
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
            delta = (target - datetime.now()).days
            return max(0, delta)
        except ValueError:
            pass

        return None

    # ------------------------------------------------------------------
    # 夜间模式
    # ------------------------------------------------------------------

    @staticmethod
    def is_night_time(night_start: str, night_end: str) -> bool:
        """
        检查当前是否在夜间时段。
        支持跨午夜范围（如 23:00 ~ 08:00）。
        """
        now = datetime.now().strftime("%H:%M")

        if night_start <= night_end:
            # 同日范围（如 01:00 ~ 06:00）
            return night_start <= now < night_end
        else:
            # 跨午夜范围（如 23:00 ~ 08:00）
            return now >= night_start or now < night_end

    # ------------------------------------------------------------------
    # 统计信息
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """返回愿望单统计信息（用于日志/调试）。"""
        total_games = len(self._games)
        total_groups = len(self._wishlists)
        total_entries = sum(
            len(apps) for apps in self._wishlists.values()
        )
        total_adders = sum(
            len(adders)
            for apps in self._wishlists.values()
            for adders in apps.values()
        )
        return {
            "unique_games": total_games,
            "groups": total_groups,
            "total_entries": total_entries,
            "total_adders": total_adders,
            "pending_notifications": len(self._pending_notifications),
        }
