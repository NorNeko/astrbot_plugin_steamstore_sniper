from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PriceOverview:
    """price_overview 子结构，对应 Steam appdetails 接口返回的价格字段。"""
    currency: Optional[str] = None
    initial_formatted: Optional[str] = None   # 原价格式化字符串
    final_formatted: Optional[str] = None     # 最终价格格式化字符串
    discount_percent: int = 0                 # 折扣百分比，无折扣为 0


@dataclass
class SteamGameInfo:
    """
    从 appdetails 接口提炼后的游戏信息模型。
    所有字段允许为 None（接口缺失时不抛异常）。
    字段来源：开发圣经 §6 定版字段清单。
    """
    # --- 核心标识 ---
    steam_appid: Optional[int] = None
    name: Optional[str] = None
    type: Optional[str] = None          # game / dlc / demo 等

    # --- 基础信息 ---
    is_free: bool = False
    short_description: Optional[str] = None
    header_image: Optional[str] = None  # 封面图 URL，P0 单独发图

    # --- 分类标签（categories：单人/多人/成就等功能特性标签，图2，不对外展示） ---
    categories: list[str] = field(default_factory=list)

    # --- 开发商指定的游戏类型（genres，图3，如 独立/策略/抢先体验） ---
    genres: list[str] = field(default_factory=list)

    # --- 开发 / 发行商 ---
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)

    # --- 价格 ---
    price_overview: Optional[PriceOverview] = None

    # --- 发售信息 ---
    coming_soon: bool = False
    release_date_str: Optional[str] = None

    # --- DLC ---
    dlc_count: int = 0                  # P0 只显示数量，不展开列表

    # --- 截图（F2） ---
    screenshots: list[dict] = field(default_factory=list)   # [{path_thumbnail, path_full}, ...]
    required_age: int = 0               # 普通=0，成人限制=18；F3 防护辅助依据
    content_descriptor_ids: list[int] = field(default_factory=list)  # Steam 内容描述符 ID；1/3/4 代表成人性内容

    # --- 评测摘要（来自 appreviews 接口） ---
    review_score_desc: Optional[str] = None   # 中文评分标签，如 特别好评/褒贬不一
    review_total_positive: int = 0            # 好评数
    review_total_reviews: int = 0             # 总评测数
    review_lang: str = "all"                  # 评测统计的语言区筛选（schinese/tchinese/japanese/english/all）

    # --- 在线人数（来自 ISteamUserStats/GetNumberOfCurrentPlayers） ---
    current_players: Optional[int] = None     # 当前在线玩家数；None 表示获取失败或未请求

    # --- ITAD 数据（来自 IsThereAnyDeal API，需配置 itad_api_key） ---
    itad_tags: list[str] = field(default_factory=list)       # 用户社区标签（如 roguelike、动作）
    history_low_price: Optional[float] = None                # Steam 平台历史最低价格（金额数字）
    history_low_currency: Optional[str] = None               # Steam 平台历史最低价的货币代码（如 HKD）
    history_low_cut: Optional[int] = None                    # Steam 平台历史最高折扣（百分比，如 75 代表 -75%）
    history_low_date: Optional[str] = None                   # Steam 平台历史最低价日期（YYYY-MM-DD）
    history_low_shop: Optional[str] = None                   # 历史最低价来源商店（应为 Steam）
    has_trading_cards: Optional[bool] = None                 # 是否有 Steam 集换卡牌（来自 ITAD info/v2）
    subscription_services: list[str] = field(default_factory=list)  # 当前可订阅服务列表（如 Game Pass、EA Play）

    # --- 内部状态（非接口字段） ---
    error: Optional[str] = None         # 请求或解析失败时填充，非 None 表示查询失败
