# astrbot_plugin_steamstore_sniper

<p align="center">
  <b>🎮 AstrBot Steam 商店速查插件</b><br>
  无需登录账号即可通过 AppID 或商店链接快速查询 Steam 游戏信息，支持封面图、简介、多地区价格、分语言区评测。<br>
  可选接入 IsThereAnyDeal，额外显示社区标签、Steam 史低、订阅服务（Game Pass / EA Play 等）、集换卡牌信息。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.2.0-blue?style=flat-square" alt="version">
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
| 游戏截图查询 | `/steam_shots` 发送压缩拼接长图，默认全局屏蔽 R18，可由管理员通过 `/steam_adult on/off` 按会话切换豁免，QQ 平台触发风控时回退文本提示 |
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

在 AstrBot WebUI 中进入 **插件市场**，搜索 `steamstore_sniper`，点击安装，等待完成后重启即可。

**方式二：手动安装**

```bash
# 进入 AstrBot 插件目录
cd data/plugins/

# 克隆仓库
git clone https://github.com/NorNeko/astrbot_plugin_steamstore_sniper.git
```

完成后在 WebUI 插件管理页面启用 **Steam 商店速查**，重启 AstrBot 生效。

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

| 指令 | 说明 |
|---|---|
| `/steam {appid}` | 通过 AppID 查询游戏详情 |
| `/steam {appid} {语言代码}` | 查询时临时指定本次评测语言区 |
| 直接发送 Steam 商店链接 | 开启 `auto_parse_enabled` 且通过 ACL 时自动解析 |
| `/steam_price {appid 或商店链接} {地区}` | 指定地区查询价格，如 `/steam_price 730 us` |
| `/steam_rlang {语言代码}` | 为下一次查询设置评测统计语言区 |
| `/steam_rlang` | 查看当前语言区设置与可选值 |
| `/steam_shots {appid 或商店链接}` | 查询游戏截图长图（最多 N 张，WebUI 可配置） |
| `/steam_adult on \| off \| status [UMO]` | 管理 R18 截图屏蔽名单：默认全局不屏蔽。`on` 加入屏蔽名单，`off` 移除，`status` 查看。可在任意会话附加目标 UMO 远程管理其他群聊，省略则默认作用于当前会话，结果写回 WebUI 配置 |
| `/steam help` | 显示指令帮助 |

**评测语言代码**：`schinese`（简体中文区）/ `tchinese`（繁体中文区）/ `japanese`（日语区）/ `english`（英语区）/ `all`（全部语言）

> 说明：当前 `/steam` 指令本身只接收 AppID；商店链接由自动解析处理器负责。`/steam_price` 与 `/steam_shots` 同时支持 AppID 和商店链接。

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
| `itad_api_key` | 字符串 | — | IsThereAnyDeal API Key，留空禁用 ITAD 增强功能 |
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

> **获取会话 UMO**：向机器人发送 `/sid`，即可查看当前会话的唯一标识符，用于填写 `allowed_list` / `banned_list`。

---

## URL 自动解析配置示例

希望在某个群聊中只要发送 Steam 链接就自动触发查询：

1. `acl_mode` 设为 `Whitelist`
2. `allowed_list` 填入目标群 UMO，如 `aiocqhttp:GroupMessage:123456789`
3. `auto_parse_enabled` 设为 `true`

---

## 重要经验与已知边界

- 查询主链路为：`main.py -> StoreService -> SteamClient / ITADClient -> formatter`。其中 `main.py` 负责 AstrBot 指令和平台差异，`formatter.py` 只负责文本排版。
- 地区不可见时，`/steam`、自动解析和 `/steam_shots` 会按 `cc_fallback_order` 自动回退；`/steam_price` 当前不做地区回退。
- 在 aiocqhttp / NapCat 环境下，`/steam_shots` 走 OneBot 本地 `file:///` 直发长图。成功后必须显式终止事件链路，否则 AstrBot 可能把原始命令继续交给后续 LLM 流程。
- 极少数截图会被 QQ NT 服务端风控挂起。当前策略是：单次发送 3 秒超时、最多重试 3 次、每次重拼图加入轻微白边抖动；若仍失败，则统一返回失败提示语，不再输出截图链接。
- `/steam_shots` 采用轻量查询路径（`enrich=False`），只请求 `appdetails` 和截图资源，不额外拉取评测、在线人数和 ITAD 数据。

---

## 数据来源

- 游戏详情：[Steam Store API `appdetails`](https://store.steampowered.com/api/appdetails)（公开接口）
- 评测摘要：[Steam Store API `appreviews`](https://store.steampowered.com/appreviews)（公开接口）

---

## 未来计划
- 增加可自选输出美化图片+文本排版的纯图片输出的功能，增加美观度和便捷性
- 实现简单的网页截图功能，作为部分信息无法满足的补充
- 增加对游戏商店页面相关捆绑包和DLC的显示
- 增加SteamDB等第三方数据库的查询选项，作为信息补充，获取历史低价等公开接口无法提供的数据。
- 群愿望单
- 日志中若无法获取到页面信息启动地区遍历功能时，不应只返回一个“AppID xxx cc=cn 查询失败: AppID xxx 不存在或在当前地区不可见”，只要启用遍历功能并成功获取到信息时，都应该在日志中添加明确反馈。若均失败也应有对应的日志信息。
- 游戏搜索，简单的关键词搜索功能，输入 `/steam_search {关键词}` 返回前 N 个相关游戏的 AppID 和名称，方便用户查询不确定的游戏信息。

## 许可

MIT License

## 彩蛋

LOGO图其实是AI生成的，G胖胖的时候没那么多胡子，留长胡子的时候没那么胖。