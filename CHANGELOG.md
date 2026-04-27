# Changelog

## [v0.4.2] - 2026-04-27

### 项目重命名

- `astrbot_plugin_steamstore_sniper` → `astrbot_plugin_steam_radar`
- 显示名：`Steam 商店速查` → `Steam 雷达`
- 仅更新提示文本和元数据，不涉及功能代码改动

### 新增：群愿望单功能

- `/wish_add {appid}` — 将游戏添加到当前群愿望单，支持多人重复添加同一游戏
  - 首次添加时自动从 Steam + ITAD 拉取游戏信息（名称、发售状态、价格、史低）
  - 同一游戏被多群添加时，全局缓存共享，API 只拉取一次
  - 已存在的游戏追加添加者（按 sender_id 去重）
- `/wish [页码]` — 分页查看当前群愿望单（每页 20 条，纯文本）
  - 未发售游戏标注预计发售日期或"暂未发售"
  - 查询时惰性触发分层刷新检查
- `/wish_remove {appid}` — 从当前群愿望单移除游戏
  - 权限可配置：`wishlist_admin_umos` 管理员列表（为空时所有群成员均可操作）
  - 仅影响当前群，其他群的同一游戏不受影响

### 新增：智能分层刷新系统

- 三层分级刷新策略（热/温/冷），不同状态的游戏采用不同刷新频率：
  - **热层**（正在打折 / 30天内发售）：每 6 小时刷新
  - **温层**（未发售>30天 / 已发售无折扣）：每 24 小时刷新
  - **冷层**（已发售 + 无史低数据）：每 72 小时刷新
- 惰性触发：仅在用户查询愿望单时检查是否需要刷新，无后台循环线程
- 增量写盘：数据 hash 对比，仅在变化时写入 JSON 文件
- 原子写入：临时文件 + `os.replace()`，避免写入中断导致数据损坏
- 分批处理：每批 20 个游戏，批内 2-3s 随机延迟，批间 5s 休息，防风控

### 新增：夜间模式

- 配置 `wishlist_night_start`（默认 23:00）和 `wishlist_night_end`（默认 08:00）
- 夜间时段：刷新正常执行（保持数据新鲜度），通知不发送 → 存入 `pending_notifications` 队列
- 白天时段：首次刷新结束后批量发送夜间排队通知
- 支持跨午夜时间范围（如 23:00 ~ 08:00）

### 新增：发售/史低自动通知

- 游戏从未发售变为已发售 → @提及所有添加者，通知后销毁记录
- 游戏达到 Steam 史低价 → @提及所有添加者，通知后销毁记录
- 跨群通知：同一游戏被多群添加时，逐群发送通知，所有群通知完毕后统一销毁
- aiocqhttp 平台支持 OneBot @提及，其他平台退化为纯文本

### 新增：文件改动

- `models/wishlist_models.py`：新增 `WishAdder` + `WishlistGameCache` + `PendingNotification` dataclass
- `core/wishlist_manager.py`：新增 CRUD、JSON 存储、全局缓存、分层刷新、夜间模式、通知队列
- `main.py`：新增 `cmd_wish_add()` + `cmd_wish_list()` + `cmd_wish_remove()` + 分层刷新调度器 + 通知系统

### 新增：WebUI 配置项

- `wishlist_admin_umos`（愿望单管理员 UMO 列表）
- `wishlist_refresh_hours`（热层刷新间隔，默认 6 小时）
- `wishlist_enabled`（群愿望单功能总开关，默认开启）
- `wishlist_night_start`（夜间模式开始时间，默认 23:00）
- `wishlist_night_end`（夜间模式结束时间，默认 08:00）

### 变更：LLM 提供商独立化

- 新增 `core/llm_client.py`：插件自有 OpenAI 兼容 LLM 客户端，不再依赖 AstrBot LLM Provider
- 新增配置项 `llm_api_url`、`llm_api_key`、`llm_model`，在插件配置页独立管理 LLM 信息
- `_llm_validate_search()` 和 `_translate_to_english()` 改为调用 `LLMClient` 方法
- LLM 未配置时增强搜索降级为纯 ITAD 搜索（无 LLM 校验和翻译）

### 设计决策记录

- 愿望单功能为高级功能，未配置 `itad_api_key` 时完全禁用
- 全局游戏缓存 + 群级索引两层结构，跨群去重减少 API 调用
- 移除操作仅影响当前群，群间互不影响
- 通知后销毁整条记录（跨群通知到位后统一删除）
- SSD 年写入量约 292MB（5群/350游戏场景），对现代 SSD 可忽略

---

## [v0.3.5] - 2026-04-27

### 新增：`/steam_search` 游戏搜索功能

- `/steam_search {关键词}` 指令，支持中英文关键词搜索 Steam 游戏
- 双策略搜索架构：
  - **方案 B（默认）**：Steam 官方 `/search/suggest` 端点，零配置即可使用，支持中文
  - **方案 A（增强）**：开启 `enhanced_search` 后，Steam 匹配度低时自动调用 ITAD `/games/search/v1` 补充搜索
- LLM 智能校验：开启增强搜索后，通过 AstrBot LLM Provider 评估结果匹配度
  - 精准单条匹配 → 直出完整游戏信息（与 `/steam` 指令一致）
  - 多条匹配（系列续作）→ 展示列表
  - 低匹配 → 返回「未找到」
- 续作识别：搜索系列通用名（如 "Dark Souls"）时返回整个系列，含明确编号时锁定单条
- 搜索结果选择：多条结果时用户输入 `#N`（如 `#1`、`#2`）在 2 分钟内选择指定游戏查看详情
  - 缓存按用户 `sender_id`（QQ号）隔离，群聊中互不干扰
  - 仅保留最后一次搜索结果（新搜索覆盖旧缓存）
  - 2 分钟后惰性过期清理，无定时器开销
- 搜索结果输出：逐条发送封面缩略图 + 文字描述（`url_image` + `message`）
- WebUI 新增 `enhanced_search` 配置项（增强搜索开关，默认关闭，需配置 `itad_api_key`）
- WebUI 新增 `search_max_results` 配置项（搜索结果数量，默认 5，范围 1-10）

### 新增：文件改动

- `core/steam_client.py`：新增 `search_suggest()` + `search_results_fallback()` + HTML 正则解析 + `_unescape_html()`
- `core/itad_client.py`：新增 `search_games()` ITAD 搜索方法
- `core/image_utils.py`：新增 `render_search_results_card()` 搜索卡片渲染函数（Steam 风格深色卡片，保留备用）
- `core/formatter.py`：新增 `format_search_results_text()` 纯文本回退格式化
- `main.py`：新增 `cmd_steam_search()` + `_llm_validate_search()` + `_translate_to_english()` + `_send_search_results()` + `cmd_select_search_result()` + 缓存管理方法

### 修复（v0.3.5 迭代中发现并修复）

- 修复搜索不存在于 Steam 的游戏（如原神、无畏契约）时返回 AppID=0 异常结果的 bug
  - 数据层：ITAD 结果过滤 AppID 无效（0 或 None）条目
  - LLM 层：收紧校验 prompt，禁止 LLM 自行联想或推荐不在结果列表中的游戏
  - 翻译层：收紧翻译 prompt，游戏不在 Steam 平台时原样返回输入
- 移除 `/search/results/` 备选回退（该端点会返回无关推荐游戏），仅使用 `/search/suggest`
- 修复 LLM 校验返回低匹配时仍展示不相关结果的问题
- 修复 ITAD 搜索结果 AppID 缺失时未通过 `fetch_game_info()` 补充获取的问题
- LLM 调用添加 15 秒超时限制（`asyncio.wait_for`），避免网络异常时长时间阻塞
- 增强搜索 tokens 消耗已在 README 中标注（每次搜索约 2-3 次 LLM 调用）

### 设计决策记录

- Steam 官方无关键词搜索 API，`/search/suggest` 是非官方端点但稳定可用
- 增强搜索关闭时，整个流程零 LLM 调用、零 ITAD API 调用
- `#N` 选择正则 `^#(\d{1,2})$` 限制为 1-2 位数字，避免与群聊中其他 `#` 开头消息冲突
- 搜索结果缓存使用 `time.monotonic()` 而非 `time.time()`，不受系统时钟调整影响

---

## [v0.2.0] - 2026-04-24

### 新增

- 锁区游戏自动切换地区重试，默认顺序 HK → JP → US，可在 WebUI 自定义
- `/steam_shots` 截图改为拼接长图发送，解决群聊"该消息类型暂不支持查看"问题
- `/steam_rlang` 语言区切换改为一次性生效，下次查询后自动恢复默认

### 修复

- 部分游戏价格折扣字段为空时插件崩溃
- WebUI 配置项填入非数字时插件加载失败
- `/steam_shots` 实际截图数量有时低于设定值；最大张数上限从 9 提升至 15

---

## [v0.1.0] - 2026-04-24

首个可用版本。

### 功能

- `/steam {appid}` 查询游戏详情（名称、简介、标签、开发商、价格、评测、发售日期）
- `/steam_price {appid} {地区}` 指定地区查询价格
- `/steam_rlang {语言}` 切换评测数据语言区（简中 / 繁中 / 日语 / 英语 / 全部）
- `/steam_shots {appid}` 查询游戏截图，含成人内容屏蔽
- 发送 Steam 商店链接自动解析（需在 WebUI 开启）
- ACL 访问控制，支持白名单 / 黑名单模式
