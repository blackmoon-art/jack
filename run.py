#!/usr/bin/env python3
"""
Nano Agent Plus — CLI 入口

Usage:
    python run.py "你的任务"                          # 默认策略
    python run.py --strategy reflexion "复杂任务"     # Reflexion 反思策略
    python run.py --strategy tot "复杂任务"           # Tree-of-Thought 策略
    python run.py --strategy plan "复杂任务"          # Plan-Execute 策略
    python run.py                                    # 交互模式

Strategies:
    default       标准 agent loop
    plan / pe     Plan-Execute: 分解 → 逐步执行 → 评估 → 必要时重规划
    reflexion     Reflexion: 自我反思 + 失败重试 + 教训学习
    tot           Tree-of-Thought: 多路径探索 → 评估 → 回溯

Supports:
  - Anthropic (Claude)
  - OpenAI
  - DeepSeek
  - OpenRouter
  - Ollama (本地)
"""

import os
import sys
import logging

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nano_agent import Agent, Config

logger = logging.getLogger("nano_agent.cli")

# 策略名称别名
STRATEGY_MAP = {
    "default": "default",
    "react": "react",
    "plan": "plan-execute",
    "pe": "plan-execute",
    "plan-execute": "plan-execute",
    "reflexion": "reflexion",
    "ref": "reflexion",
    "tot": "tree-of-thought",
    "tree-of-thought": "tree-of-thought",
    "tree": "tree-of-thought",
    "meta": "meta",
    "auto": "auto",
}


def interactive(agent: Agent):
    """交互模式。"""
    print(f"🤖 Nano Agent Plus")
    print(f"📝 模型: {agent.config.model}")
    print(f"🔗 后端: {agent.config.provider}")
    print(f"💾 {agent.memory_summary}")
    print()
    print("命令:")
    print("  '退出' / 'quit'       — 退出")
    print("  '清除' / 'clear'      — 清除记忆")
    print("  '记忆' / 'mem'        — 查看记忆状态")
    print("  '策略 <name>'          — 切换推理策略")
    print()
    print("可用策略: default, react, plan-execute, reflexion, tree-of-thought")
    print()

    current_strategy = "default"

    while True:
        try:
            user = input(f"\033[36m[{current_strategy}] >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见!")
            break

        if not user:
            continue
        if user.lower() in ("退出", "quit", "exit", "q"):
            print("👋 再见!")
            break
        if user.lower() in ("清除", "clear"):
            agent.clear_memory()
            continue
        if user.lower() in ("记忆", "mem"):
            print(f"📊 {agent.memory_summary}")
            continue
        if user.lower().startswith("策略 "):
            strat_name = user[3:].strip().lower()
            mapped = STRATEGY_MAP.get(strat_name)
            if mapped:
                current_strategy = mapped
                print(f"✅ 切换到策略: {mapped}")
            else:
                print(f"❌ 未知策略: {strat_name}")
                print(f"   可用: {list(STRATEGY_MAP.keys())}")
            continue

        print()
        result = agent.run(user, strategy=current_strategy)
        print(f"\n🤖 {result}\n")


def parse_args(argv: list[str]) -> tuple[str, str, list[str]]:
    """解析命令行参数，返回 (strategy, task_string)。"""
    strategy = "default"
    remaining = []

    i = 0
    while i < len(argv):
        if argv[i] in ("--strategy", "-s") and i + 1 < len(argv):
            strat_name = argv[i + 1].lower()
            strategy = STRATEGY_MAP.get(strat_name, "default")
            i += 2
        elif argv[i].startswith("--strategy="):
            strat_name = argv[i].split("=", 1)[1].lower()
            strategy = STRATEGY_MAP.get(strat_name, "default")
            i += 1
        else:
            remaining.append(argv[i])
            i += 1

    task = " ".join(remaining)
    return strategy, task


def main():
    strategy, task = parse_args(sys.argv[1:])

    config = Config()
    agent = Agent(config)

    if task:
        print(f"Task: {task}")
        print(f"Strategy: {strategy}")
        logger.info(f"Running task with strategy={strategy}")
        result = agent.run(task, strategy=strategy)
        print(f"\n{result}")
    else:
        interactive(agent)


if __name__ == "__main__":
    main()
