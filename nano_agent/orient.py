"""
Orient 模块 — O-O-D-A 中的第二个 O：把原始观察转化为结构化理解。

职责:
  1. INTERPRETATION:  这个观察意味着什么？
  2. ASSOCIATION:     和我之前的经验/记忆有什么关系？
  3. CONTEXTUALIZE:   在当前任务目标下，哪些规则适用？
  4. IMPLICATION:     接下来应该关注什么？对决策的建议？

与 Decide 的边界:
  - Orient 回答 "这意味着什么" （理解层）
  - Decide 回答 "我该做什么" （行动层）
  - Orient 的输出是 Decide 的输入
"""

import json
import logging
import re
from typing import Optional

from .config import Config
from .llm import LLM

logger = logging.getLogger("nano_agent.orient")


class Orient:
    """
    显式 Orient 阶段：在 Observe 和 Decide 之间。

    整合三个信息源:
      - 当前观察 (observation)
      - 持久记忆 (过往经验)
      - 自定义规则 (行为约束)
    """

    def __init__(self, config: Config, llm: LLM):
        self.config = config
        self.llm = llm
        self._rule_cache: Optional[str] = None

    # ── 主入口 ──────────────────────────────────────────

    def orient(self, observation: str, task: str,
               memory_context: str = "", rules: str = "",
               model: str | None = None) -> dict:
        """
        对观察进行定向解读。

        Args:
            observation:    原始观察（工具返回、用户输入等）
            task:           当前任务目标
            memory_context: 相关记忆文本
            rules:          适用规则文本
            model:          模型覆盖（请求级，线程安全）

        Returns:
            {"interpretation": str,   # 观察到的事实
             "association":    str,   # 与记忆/经验的关联
             "implication":    str,   # 对下一步的建议
             "confidence":     int,   # 0-10 解读置信度
             "focus":          str}   # 当前应关注的要点
        """
        prompt = self._build_orient_prompt(
            observation, task, memory_context, rules
        )
        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            system="You are an analytical observer. Be precise and concise.",
            model=model,
        )
        return self._parse_orientation(response["text"], observation, model=model)

    # ── 规则加载与匹配 ──────────────────────────────────

    def load_rules(self) -> str:
        """加载所有用户自定义规则（缓存避免重复读盘）。"""
        if self._rule_cache is not None:
            return self._rule_cache
        rules_dir = self.config.rules_dir
        if not rules_dir:
            self._rule_cache = ""
            return ""
        try:
            from pathlib import Path
            rules = []
            for f in sorted(Path(rules_dir).glob("*.md")):
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    rules.append(f"## {f.stem}\n{content}")
            self._rule_cache = "\n\n".join(rules) if rules else ""
        except Exception as e:
            logger.warning(f"Failed to load rules: {e}")
            self._rule_cache = ""
        return self._rule_cache

    def find_applicable_rules(self, observation: str) -> str:
        """
        从规则库中找到与当前观察相关的规则。

        简单实现：关键词匹配。生产环境可替换为向量检索。
        """
        all_rules = self.load_rules()
        if not all_rules:
            return ""
        # 关键词匹配：支持中英文混合
        # CJK bigram（2字滑窗）+ 英文整词，解决中文 split() 无效的问题
        obs_lower = observation.lower()
        obs_tokens = set(re.findall(r'[a-zA-Z]+', obs_lower))
        obs_cjk = re.findall(r'[\u4e00-\u9fff]', obs_lower)
        for i in range(len(obs_cjk) - 1):
            obs_tokens.add(obs_cjk[i] + obs_cjk[i + 1])
        applicable = []
        for section in all_rules.split("\n\n"):
            section_lower = section.lower()
            section_tokens = set(re.findall(r'[a-zA-Z]+', section_lower))
            section_cjk = re.findall(r'[\u4e00-\u9fff]', section_lower)
            for i in range(len(section_cjk) - 1):
                section_tokens.add(section_cjk[i] + section_cjk[i + 1])
            # 至少 2 个公共 token 才认为相关
            if len(obs_tokens & section_tokens) >= 2:
                applicable.append(section)
        return "\n\n".join(applicable[:3]) if applicable else ""

    def invalidate_cache(self):
        """清除规则缓存（规则文件变更后调用）。"""
        self._rule_cache = None

    # ── 内部方法 ────────────────────────────────────────

    def _build_orient_prompt(self, observation: str, task: str,
                             memory: str, rules: str) -> str:
        parts = [
            "Analyze the following observation in the context of the current task.",
            "",
            f"## Task\n{task}",
            "",
            f"## Observation\n{observation[:3000]}",
        ]
        if memory:
            parts.append(f"\n## Relevant Memories\n{memory[:2000]}")
        if rules:
            parts.append(f"\n## Applicable Rules\n{rules[:1000]}")
        parts.extend([
            "",
            "Respond with ONLY a JSON object:",
            '{',
            '  "interpretation": "<what does this observation mean?>",',
            '  "association": "<how does it relate to known facts, memories, or past experience?>",',
            '  "implication": "<what should be the focus for the next step?>",',
            '  "confidence": <0-10>,',
            '  "focus": "<1 sentence: the single most important thing to address now>"',
            '}',
        ])
        return "\n".join(parts)

    def _parse_orientation(self, text: str, observation: str = "",
                            model: str | None = None) -> dict:
        """解析 LLM 返回的 JSON。含重试逻辑。

        委托给 LLM.chat_json_with_retry 避免重复实现。
        """
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]

        try:
            data = json.loads(text.strip())
            return {
                "interpretation": data.get("interpretation", ""),
                "association": data.get("association", ""),
                "implication": data.get("implication", ""),
                "confidence": int(data.get("confidence", 5)),
                "focus": data.get("focus", ""),
            }
        except (json.JSONDecodeError, ValueError):
            pass

        # 重试：让 LLM 重新生成
        data = self.llm.chat_json_with_retry(
            messages=[{
                "role": "user",
                "content": (
                    f"Observation: {observation[:2000]}\n\n"
                    "Return ONLY a valid JSON object with keys: "
                    "interpretation, association, implication, confidence, focus. "
                    "No markdown, no explanation."
                ),
            }],
            max_retries=2,
            system="You are an analytical observer. Be precise and concise.",
            model=model,
        )

        if data and isinstance(data, dict):
            return {
                "interpretation": data.get("interpretation", ""),
                "association": data.get("association", ""),
                "implication": data.get("implication", ""),
                "confidence": int(data.get("confidence", 5)),
                "focus": data.get("focus", ""),
            }

        logger.warning("Failed to parse orientation JSON after retries")
        return {
            "interpretation": text[:500],
            "association": "",
            "implication": "Proceed with the next action.",
            "confidence": 5,
            "focus": "",
        }
