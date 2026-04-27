"""
wishlist_models.py — 群愿望单数据模型

设计原则：
- 全局游戏缓存（appid → WishlistGameCache）跨群共享，API 只拉一次
- 群级索引（UMO → {appid → [WishAdder]}）群间隔离
- 变化检测字段（was_*）确保同一状态变化只通知一次
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WishAdder:
    """愿望单添加者信息。"""
    sender_id: str          # 添加者 ID（QQ号等）
    sender_name: str = ""   # 添加者昵称（用于通知，best-effort）
    added_at: str = ""      # 添加时间（ISO 8601）


@dataclass
class WishlistGameCache:
    """
    全局游戏信息缓存（按 appid 去重，跨群共享）。
    所有字段允许为 None（接口缺失时不抛异常）。
    """
    appid: int
    name: str
    header_image: str | None = None

    # ── 发售状态 ──
    is_released: bool = False
    was_released: bool = False        # 上次检查时的发售状态（变化检测）
    coming_soon: bool = False
    release_date_str: str | None = None
    release_display: str = "暂未发售"  # "已发售" / "预计2026年6月发售" / "暂未发售"

    # ── 价格信息 ──
    is_free: bool = False
    is_preorder: bool = False         # coming_soon=True 且有价格 → 可预购
    current_price: str | None = None  # 当前价格格式化字符串
    currency: str | None = None       # 货币代码
    is_on_sale: bool = False
    discount_percent: int = 0
    initial_price: str | None = None  # 原价（打折时）

    # ── 史低信息（ITAD）──
    history_low_price: float | None = None
    history_low_currency: str | None = None
    history_low_date: str | None = None
    is_at_history_low: bool = False
    was_at_history_low: bool = False  # 变化检测

    # ── 元数据 ──
    last_updated: str | None = None   # 最后刷新时间（ISO 8601）

    # ------------------------------------------------------------------
    # 序列化 / 反序列化（JSON 存储用）
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的字典。"""
        return {
            "appid": self.appid,
            "name": self.name,
            "header_image": self.header_image,
            "is_released": self.is_released,
            "was_released": self.was_released,
            "coming_soon": self.coming_soon,
            "release_date_str": self.release_date_str,
            "release_display": self.release_display,
            "is_free": self.is_free,
            "is_preorder": self.is_preorder,
            "current_price": self.current_price,
            "currency": self.currency,
            "is_on_sale": self.is_on_sale,
            "discount_percent": self.discount_percent,
            "initial_price": self.initial_price,
            "history_low_price": self.history_low_price,
            "history_low_currency": self.history_low_currency,
            "history_low_date": self.history_low_date,
            "is_at_history_low": self.is_at_history_low,
            "was_at_history_low": self.was_at_history_low,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WishlistGameCache:
        """从字典反序列化。忽略未知字段。"""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class PendingNotification:
    """
    夜间排队的待发通知。
    在夜间模式下检测到发售/史低变化时存入队列，白天批量发送。
    """
    appid: int
    game_name: str
    notification_type: str  # "released" | "history_low"
    # umo → [WishAdder] 该群中关注该游戏的所有添加者
    affected_groups: dict[str, list[WishAdder]] = field(default_factory=dict)
    detected_at: str = ""   # 检测到变化的时间（ISO 8601）

    # ------------------------------------------------------------------
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "appid": self.appid,
            "game_name": self.game_name,
            "notification_type": self.notification_type,
            "affected_groups": {
                umo: [_adder_to_dict(a) for a in adders]
                for umo, adders in self.affected_groups.items()
            },
            "detected_at": self.detected_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PendingNotification:
        affected = {}
        for umo, adders_raw in (data.get("affected_groups") or {}).items():
            affected[umo] = [_adder_from_dict(a) for a in adders_raw]
        return cls(
            appid=data.get("appid", 0),
            game_name=data.get("game_name", ""),
            notification_type=data.get("notification_type", ""),
            affected_groups=affected,
            detected_at=data.get("detected_at", ""),
        )


# ------------------------------------------------------------------
# WishAdder 序列化辅助（避免循环导入）
# ------------------------------------------------------------------

def _adder_to_dict(adder: WishAdder) -> dict:
    return {
        "sender_id": adder.sender_id,
        "sender_name": adder.sender_name,
        "added_at": adder.added_at,
    }


def _adder_from_dict(data: dict) -> WishAdder:
    return WishAdder(
        sender_id=data.get("sender_id", ""),
        sender_name=data.get("sender_name", ""),
        added_at=data.get("added_at", ""),
    )
