"""
llm_client.py — 插件自有 LLM 客户端（OpenAI 兼容）

设计原则：
- 不依赖 AstrBot 的 LLM Provider，避免不必要的纠纷
- 通过插件配置独立管理 API Key、URL、模型
- 仅支持 OpenAI 兼容的 Chat Completions API
- 所有方法失败时返回 None，不向上层抛异常
"""

from __future__ import annotations

import json as _json
import re

import aiohttp
from astrbot.api import logger


class LLMClient:
    """
    OpenAI 兼容的 LLM 客户端。
    用于插件内部的文本处理任务（如搜索结果校验、游戏名翻译）。
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str = "gpt-3.5-turbo",
        timeout: int = 15,
        proxy: str | None = None,
    ):
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._proxy = proxy
        self._session: aiohttp.ClientSession | None = None

    async def create_session(self) -> None:
        """创建 aiohttp 会话。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                trust_env=True,
            )
            logger.debug("[llm_client] aiohttp session 已创建")

    async def close_session(self) -> None:
        """关闭 aiohttp 会话。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.debug("[llm_client] aiohttp session 已关闭")

    async def chat_completion(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str | None:
        """
        发送 Chat Completions 请求，返回助手回复文本。
        失败时返回 None。
        """
        if not self._session or self._session.closed:
            logger.warning("[llm_client] HTTP session 未初始化")
            return None

        # 构建消息列表
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # 构建请求体
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        try:
            async with self._session.post(
                self._api_url,
                headers=headers,
                json=payload,
                proxy=self._proxy,
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.warning(
                        f"[llm_client] API 请求失败 HTTP {resp.status}: {error_text[:200]}"
                    )
                    return None

                data = await resp.json(content_type=None)

                # 提取回复文本
                choices = data.get("choices") or []
                if not choices:
                    logger.warning("[llm_client] API 返回空 choices")
                    return None

                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    logger.warning("[llm_client] API 返回空 content")
                    return None

                return content.strip()

        except Exception as e:
            logger.warning(f"[llm_client] API 请求异常: {type(e).__name__}: {e}")
            return None

    async def validate_search_results(
        self,
        keyword: str,
        results: list[dict],
    ) -> dict:
        """
        评估搜索结果与用户意图的匹配度。
        返回 {"match_level": "high"|"low", "matched_indices": [int], "is_single_precise": bool}
        失败时返回默认高匹配（不阻断流程）。
        """
        default_result = {
            "match_level": "high",
            "matched_indices": list(range(len(results))),
            "is_single_precise": len(results) == 1,
        }

        # 构建结果列表文本
        items_text = "\n".join(
            f"  {i}. {r.get('name', '未知')} (AppID {r.get('appid', '?')}) — {r.get('price', '未知')}"
            for i, r in enumerate(results)
        )

        prompt = (
            f"你是一个 Steam 游戏搜索结果评估器。你的唯一任务是评估下方列表中的结果与用户搜索意图的匹配度。\n"
            f"你绝对不能自行搜索、联想、推荐或补充任何不在列表中的游戏。\n\n"
            f"用户搜索了「{keyword}」。\n\n"
            f"以下是 Steam 商店搜索返回的结果列表（仅限这些结果，不要引入其他游戏）：\n{items_text}\n\n"
            f"评估规则：\n"
            f"1. 仅评估列表中已有的结果，不要自行补充或推荐任何不在列表中的游戏。\n"
            f"2. 如果用户搜索的是一个游戏系列的通用名（如\"Dark Souls\"、\"Hollow Knight\"），"
            f"该系列的所有续作/衍生作都应视为高匹配度。\n"
            f"3. 如果用户搜索中包含了明确的编号或副标题（如\"Dark Souls 3\"、\"Hollow Knight Silksong\"），"
            f"则只有对应的那一作是高匹配度。\n"
            f"4. DLC、原声带、粉丝扩展等衍生内容应视为低匹配度，除非用户明确搜索了它们。\n"
            f"5. 列表中的游戏名与用户搜索意图完全不相关时为低匹配度。\n\n"
            f"返回 JSON（只返回 JSON，不要返回其他内容）：\n"
            f'{{"match_level": "high" 或 "low", "matched_indices": [匹配的结果序号，从0开始], '
            f'"is_single_precise": true 或 false, "reason": "简短说明"}}'
        )

        system_prompt = (
            "你是一个 Steam 游戏搜索结果评估器。你的唯一职责是评估给定列表中游戏与用户搜索意图的匹配度。"
            "严禁自行搜索、联想、推荐或补充任何不在列表中的游戏。只返回 JSON 格式的结果。"
        )

        try:
            response_text = await self.chat_completion(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.1,
            )

            if not response_text:
                logger.debug("[llm_client] 搜索校验无响应，使用默认结果")
                return default_result

            # 尝试提取 JSON（可能被 markdown 代码块包裹）
            json_match = re.search(r"\{[^{}]*\}", response_text, re.DOTALL)
            if json_match:
                parsed = _json.loads(json_match.group())
                match_level = parsed.get("match_level", "high")
                matched_indices = parsed.get("matched_indices", [])
                is_single_precise = parsed.get("is_single_precise", False)

                # 校验 matched_indices 范围
                matched_indices = [i for i in matched_indices if 0 <= i < len(results)]
                if not matched_indices:
                    matched_indices = list(range(len(results)))

                logger.info(
                    f"[llm_client] 搜索校验: level={match_level} "
                    f"indices={matched_indices} single={is_single_precise} "
                    f"reason={parsed.get('reason', '')}"
                )
                return {
                    "match_level": match_level,
                    "matched_indices": matched_indices,
                    "is_single_precise": bool(is_single_precise),
                }

        except _json.JSONDecodeError as e:
            logger.warning(f"[llm_client] 搜索校验 JSON 解析失败: {e}")
        except Exception as e:
            logger.warning(f"[llm_client] 搜索校验失败: {type(e).__name__}: {e}")

        return default_result

    async def translate_to_english(self, chinese_text: str) -> str:
        """
        将中文游戏名翻译为 Steam 英文游戏名。
        失败时返回原文。
        """
        prompt = (
            f"请将以下游戏名翻译为 Steam 商店页面上的英文官方名称。\n"
            f"规则：仅输出英文名，不要输出其他任何内容。如果该游戏不在 Steam 平台上，"
            f"请原样输出输入的游戏名，不要编造或联想其他游戏。\n"
            f"游戏名：{chinese_text}"
        )

        system_prompt = (
            "你是一个游戏名称翻译器。仅将中文游戏名翻译为 Steam 商店的英文官方名称。"
            "不要编造、联想或推荐任何游戏。仅输出英文名。"
        )

        try:
            translated = await self.chat_completion(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.1,
            )

            if translated:
                logger.info(f"[llm_client] 翻译: {chinese_text!r} -> {translated!r}")
                return translated

        except Exception as e:
            logger.warning(f"[llm_client] 翻译失败: {type(e).__name__}: {e}")

        return chinese_text
