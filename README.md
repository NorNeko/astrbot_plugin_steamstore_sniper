# astrbot_plugin_steam_radar

<p align="center">
  <b>🎮 AstrBot Steam 雷达插件</b><br>
  一款基于Astrbot平台，功能强大的Steam 全能助手：涵盖游戏详情查询、价格、查看预览图、游戏搜索、群内URL自动解析。<br>
  以上基础功能均提供多地区选择，无需登录任何账号，开箱即用。<br>
  可选接入 IsThereAnyDeal，开启增强模式，解锁增强检索、群愿望单、发售/史低自动通知、Steam 史低、订阅服务（Game Pass / EA Play 等）、集换卡牌信息等等高级功能。<br>
  群愿望单支持功能自动隔离群聊、智能分层刷新、自动向添加愿望单用户发送发售/史低通知。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.4.2-blue?style=flat-square" alt="version">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="license">
  <img src="https://img.shields.io/badge/Python-3.10%2B-yellow?style=flat-square" alt="python">
  <img src="https://img.shields.io/badge/AstrBot-%3E%3D4.0.0-orange?style=flat-square" alt="astrbot">
</p>

---

## 功能概览

### 基础功能（无需任何额外配置）

| 功能 | 说明 |
|---|---|
| 游戏详情查询 | 输入 `/steam {appid}` 查询；发送商店链接可在开启自动解析后直接触发查询 |
| 多地区价格查询 | `/steam_price {appid 或商店链接} {地区}` 实时查询价格及折扣 |
| 当前在线人数 | 实时显示该游戏当前 Steam 在线人数 |
| 评测语言区筛选 | 支持按简中 / 繁中 / 日语 / 英语 / 全部 分区统计好评率 |
| 临时评测语言区切换 | `/steam_rlang` 为下一次查询设置评测统计语言，`/steam {appid} {语言代码}` 也可内联指定 |
| URL 自动解析 | 白名单会话中发送 Steam 商店链接即自动触发查询，无需输入指令 |
| 游戏搜索 | `/steam_search {关键词}` 搜索 Steam 游戏，支持中英文，精准匹配时直接输出完整游戏信息 |
| 增强搜索（可选） | 开启 `enhanced_search` 后，Steam 搜索匹配度低时自动调用 ITAD 补充搜索，并通过插件自有 LLM 支持中文关键词翻译和结果校验（需配置 `llm_api_url` + `llm_api_key`） |
| 游戏截图查询 | `/steam_shots` 发送压缩拼接长图，默认全局屏蔽 R18，可由管理员通过 `/steam_adult on/off` 按会话切换豁免，QQ 平台触发风控时回退文本提示 |
| 群愿望单 | `/wish_add` 添加、`/wish` 查询、`/wish_remove` 移除；跨群共享游戏缓存，智能分层刷新，发售/史低自动通知（需配置 `itad_api_key`） |
| 访问控制（ACL） | 支持白名单 / 黑名单 / 关闭三种模式，按 UMO 精确控制使用权限 |

### 增强功能（需配置 `itad_api_key`）

以下信息来自 **[IsThereAnyDeal](https://isthereanydeal.com)** 数据源，需在插件配置页填写 API Key 后启用。  
API Key 免费申请：[https://isthereanydeal.com/apps/my/](https://isthereanydeal.com/apps/my/)

| 功能 | 说明 |
|---|---|
| 🏷️ 社区标签 | 显示 ITAD 社区用户打的游戏标签（替代 Steam 官方 genres，信息更丰富） |
| 💸 Steam 史低 | 显示该游戏在 Steam 上的历史最低价格、折扣幅度及日期 |
| 🎫 订阅服务 | 显示该游戏当前在哪些订阅服务中可用（如 Game Pass、EA Play） |
| 🃏 集换卡牌 | 标注游戏是否包含 Steam 集换卡牌 |

> 未配置 `itad_api_key` 时，标签回退显示 Steam 官方 genres，其余 ITAD 字段静默不显示。

---

## 快速开始

### 1. 安装

**方式一：通过 AstrBot 插件市场安装（推荐）**

在 AstrBot WebUI 中进入 **插件市场**，搜索 `steam_radar`，点击安装，等待完成后重启即可。

**方式二：手动安装**

```bash
# 进入 AstrBot 插件目录
cd data/plugins/

# 克隆仓库
git clone https://github.com/NorNeko/astrbot_plugin_steam_radar.git
```

完成后在 WebUI 插件管理页面启用 **Steam 雷达**，重启 AstrBot 生效。

### 2. 依赖

插件启动时会自动安装以下依赖，无需手动操作：

```
aiohttp>=3.9.0    # 异步 HTTP 客户端
Pillow>=10.0.0    # 截图功能图像处理（/steam_shots 指令）
```

### 3. 配置

安装完成后，在 WebUI 插件配置页面按需填写以下项目：

| 配置项 | 必填 | 说明 |
|---|---|---|
| **默认地区** (`default_cc`) | 推荐 | 查询价格时的默认地区，如 `hk`、`us`、`cn` |
| **代理地址** (`proxy`) | 推荐 | 国内用户必须配置，如 `http://127.0.0.1:7890` |
| **ITAD API Key** (`itad_api_key`) | 可选 | 启用史低、订阅、卡牌、社区标签等增强功能 |
| 访问控制模式 (`acl_mode`) | 可选 | `Off` / `Whitelist` / `Blacklist`，默认关闭 |
| URL 自动解析 (`auto_parse_enabled`) | 可选 | 开启后在授权会话中发送链接自动触发查询 |

> 完整配置项说明见文末 [WebUI 配置项](#webui-配置项) 表格。

---

## 指令

### 基础指令

| 指令 | 说明 |
|---|---|
| `/steam {appid}` | 通过 AppID 查询游戏详情 |
| `/steam {appid} {语言代码}` | 查询时临时指定本次评测语言区 |
| `/steam` 或 `/steam help` | 显示快速帮助（支持的命令和用法） |
| `/steam_price {appid 或商店链接} {地区}` | 指定地区查询价格，如 `/steam_price 730 us` |
| `/steam_search {关键词}` | 搜索 Steam 游戏（支持中英文关键词） |
| `#N`（如 `#1`、`#2`） | 从上次搜索结果中选择指定序号的游戏查看详情（2 分钟内有效，按用户隔离） |
| `/steam_shots {appid 或商店链接}` | 查询游戏截图长图（最多 N 张，WebUI 可配置） |
| 直接发送 Steam 商店链接 | 开启 `auto_parse_enabled` 且通过 ACL 时自动解析 |

### 群愿望单指令（需配置 `itad_api_key`）

| 指令 | 说明 |
|------|------|
| `/wish_add {appid}` | 将游戏添加到当前群愿望单（支持多人重复添加） |
| `/wish [页码]` | 查看当前群愿望单（每页 20 条，纯文本） |
| `/wish_remove {appid}` | 从当前群愿望单移除游戏（权限可配置） |

**说明**：
- 群愿望单为高级功能，**未配置 `itad_api_key` 时完全禁用**
- 愿望单按群聊隔离，每个群共享一个愿望单
- 同一游戏被多群添加时，全局缓存共享，API 只拉取一次
- 智能分层刷新：热层（打折/即将发售）每 6h、温层每 24h、冷层每 72h
- 发售/史低自动通知：@提及添加者，通知后销毁记录
- 夜间模式：配置时段内通知排队，白天批量发送

### 配置和管理指令（正则表达式）

| 指令 | 说明 |
|---|---|
| `/steam_rlang` | 查看当前评测语言区设置与可选值 |
| `/steam_rlang {语言代码}` | 为下一次查询临时设置评测统计语言区（一次性生效） |
| `/steam_adult status [UMO]` | 查询 R18 截图屏蔽名单状态；省略 UMO 查当前会话，附加 UMO 查指定会话 |
| `/steam_adult on {UMO}` | 将指定 UMO 加入 R18 截图屏蔽名单；省略 UMO 时对当前会话操作 |
| `/steam_adult off {UMO}` | 将指定 UMO 从 R18 截图屏蔽名单中移除；省略 UMO 时对当前会话操作 |

**说明**：
- 评测语言代码：`schinese`（简体中文区）/ `tchinese`（繁体中文区）/ `japanese`（日语区）/ `english`（英语区）/ `all`（全部语言）
- `/steam` 指令本身只接收 AppID；商店链接由自动解析处理器负责
- `/steam_price` 与 `/steam_shots` 同时支持 AppID 和商店链接
- `/steam_rlang` 无参数时显示帮助，无参数发送查询后自动消费（一次性）
- `/steam_adult` 无参数时默认为 `status` 且作用于当前会话；可附加 UMO 参数远程管理任意会话，变更写回 WebUI 配置
- 获取会话 UMO：向机器人发送 `/sid`，即可查看当前会话的唯一标识符

**商店链接支持格式示例**：
```
https://store.steampowered.com/app/2868840/Slay_the_Spire_2/
https://store.steampowered.com/app/2989760/_/
```

---

## 输出示例

![示例](sample/Snipaste_2026-04-24_02-05-43.png)

---

## WebUI 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `default_cc` | 字符串 | `hk` | 默认查询地区（hk / cn / us / jp / tw / sg） |
| `default_lang` | 字符串 | `schinese` | 游戏信息显示语言 |
| `review_lang` | 字符串 | `schinese` | 默认评测统计语言区 |
| `request_timeout` | 整数 | `10` | 请求超时秒数（5–30） |
| `rate_limit_per_minute` | 整数 | `4` | 全局查询频率上限（次/分钟），0 = 不限制 |
| `proxy` | 字符串 | `http://127.0.0.1:7897` | 代理地址，留空禁用 |
| `itad_api_key` | 字符串 | — | IsThereAnyDeal API Key，留空禁用 ITAD 增强功能和群愿望单 |
| `enhanced_search` | 布尔 | `false` | 启用增强搜索（ITAD）。开启后，当 Steam 官方搜索结果匹配度较低时，自动调用 ITAD 搜索接口进行补充搜索，并调用插件自有 LLM 支持中文关键词搜索和结果校验。需要配置 `itad_api_key` 和 LLM 相关配置才能完全生效 |
| `llm_api_url` | 字符串 | — | OpenAI 兼容的 LLM API 地址（如 `https://api.openai.com/v1/chat/completions`）。留空则禁用 LLM 功能 |
| `llm_api_key` | 字符串 | — | LLM API Key。留空则禁用 LLM 功能 |
| `llm_model` | 字符串 | `gpt-3.5-turbo` | LLM 模型名称 |
| `search_max_results` | 整数 | `5` | `/steam_search` 每次最多显示的搜索结果条数（1–10） |
| `max_description_length` | 整数 | `200` | 简介最大字符数，0 = 不截断 |
| `acl_mode` | 字符串 | `Off` | 访问控制模式：`Off` / `Whitelist` / `Blacklist` |
| `allowed_list` | 列表 | — | Whitelist 模式下允许使用的 UMO 列表 |
| `banned_list` | 列表 | — | Blacklist 模式下拒绝使用的 UMO 列表 |
| `auto_parse_enabled` | 布尔 | `false` | 启用后，通过 ACL 的会话发送商店链接将自动解析 |
| `adult_screenshots_block_list` | 列表（UMO） | `[]` | R18 截图屏蔽名单。默认对所有会话不屏蔽成人内容截图；仅名单中的 UMO 且游戏 `required_age >= 18` 或 `content_descriptors.ids` ∩ {1,3,4} 时才拒绝发送。推荐用 `/steam_adult on {UMO}` 在任意会话远程管理名单并自动持久化 |
| `max_screenshots` | 整数 | `6` | `/steam_shots` 每次最多发送的截图张数（1–15） |
| `screenshot_width` | 整数 | `600` | 拼接长图时每张缩略图的目标宽度 |
| `screenshot_stitch_max_kb` | 整数 | `200` | 拼接长图体积上限，超限时自动继续压缩 |
| `cc_fallback_order` | 字符串 | `hk;jp;us` | 当前地区不可见时的自动回退顺序，英文分号分隔 |
| `wishlist_enabled` | 布尔 | `true` | 群愿望单功能总开关。关闭后所有愿望单指令不可用，定时刷新和通知也停止 |
| `wishlist_admin_umos` | 列表（UMO） | `[]` | 愿望单管理员 UMO 列表。为空时所有群成员均可移除愿望单条目 |
| `wishlist_refresh_hours` | 整数 | `6` | 热层刷新间隔（小时），温层=×4（24h），冷层=×12（72h） |
| `wishlist_night_start` | 字符串 | `23:00` | 夜间模式开始时间 |
| `wishlist_night_end` | 字符串 | `08:00` | 夜间模式结束时间 |

> **获取会话 UMO**：向机器人发送 `/sid`，即可查看当前会话的唯一标识符，用于填写 `allowed_list` / `banned_list`。

---

## URL 自动解析配置示例

希望在某个群聊中只要发送 Steam 链接就自动触发查询：

1. `acl_mode` 设为 `Whitelist`
2. `allowed_list` 填入目标群 UMO，如 `aiocqhttp:GroupMessage:123456789`
3. `auto_parse_enabled` 设为 `true`

---

## 数据来源

- 游戏详情：[Steam Store API `appdetails`](https://store.steampowered.com/api/appdetails)（公开接口）
- 评测摘要：[Steam Store API `appreviews`](https://store.steampowered.com/appreviews)（公开接口）

---

## 未来计划
- 增加可自选输出美化图片+文本排版的纯图片输出的功能，增加美观度和便捷性
- 增加对游戏商店页面相关捆绑包和DLC的显

---

## 许可

MIT License

## 彩蛋

LOGO图其实是AI生成的，G胖胖的时候没那么多胡子，留长胡子的时候没那么胖。