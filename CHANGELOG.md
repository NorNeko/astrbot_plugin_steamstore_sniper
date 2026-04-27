# Changelog

## [v0.3.5] - 2026-04-27

### 修复

- 修复搜索不存在于 Steam 的游戏时返回 AppID=0 异常结果的 bug
- 收紧 LLM prompt，禁止 LLM 自行联想或推荐不在搜索结果列表中的游戏
- 收紧翻译 prompt，游戏不在 Steam 平台时原样返回输入
- ITAD 结果过滤 AppID 无效条目
- LLM 调用添加 15 秒超时限制
- 修复 LLM 校验返回低匹配时仍展示不相关结果的问题
- 修复 ITAD 搜索结果 AppID 缺失时未补充获取的问题
- 增强搜索 tokens 消耗已在文档中标注
- 移除 `/search/results/` 备选回退（该端点会返回无关推荐游戏），仅使用 `/search/suggest` 精确匹配

### 新增

- `/steam_search {关键词}` 游戏搜索功能，支持中英文关键词
- 双策略搜索：优先使用 Steam 官方 `/search/suggest` 端点，匹配度低时可选回退 ITAD 增强搜索
- LLM 智能校验：搜索结果经 LLM 评估匹配度，精准匹配时直接输出完整游戏信息
- 续作识别：搜索系列通用名（如 "Dark Souls"）时返回整个系列，含明确编号时锁定单条
- 搜索结果卡片：压缩封面缩略图 + 文字垂直拼接为 Steam 风格长图
- WebUI 新增 `enhanced_search` 配置项（增强搜索开关，默认关闭）
- WebUI 新增 `search_max_results` 配置项（搜索结果数量，默认 5）

### 变更

- `core/steam_client.py` 新增 `search_suggest()` 和 `search_results_fallback()` 搜索方法
- `core/itad_client.py` 新增 `search_games()` ITAD 搜索方法
- `core/image_utils.py` 新增 `render_search_results_card()` 搜索卡片渲染函数
- `core/formatter.py` 新增 `format_search_results_text()` 纯文本回退格式化

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
