"""
Meta 策略 — 统一推理流水线：分析→选择→执行→反馈→调整。

流程:
  1. 任务分析:    LLM 结构化分析（领域、复杂度、是否需要工具）
  2. 复杂度评估:   1-10 分打分
  3. 选择推理深度: 根据分数选策略 + 参数
  4. 调用工具:     委托给 agent_loop_fn
  5. 失败重试:     自动评估结果，失败则调整策略重试
  6. 反思修复:     提取教训持久化

所有 6 步串联，不中断。
"""

import json
import logging

from .base import BaseStrategy

logger = logging.getLogger("nano_agent.strategies.meta")


class MetaStrategy(BaseStrategy):
    """统一推理流水线策略 — 分析→选择→执行→反馈→调整。"""

    uses_orient = True
    default_params = {"max_retries": 6, "auto_upgrade": True}
    auto_keywords = ("全自动", "高要求", "质量优先", "autopilot", "确保正确", "关键任务")
    auto_priority = 4  # 最高优先：复杂/高要求任务首选

    def __init__(self, *args, max_retries: int = None, auto_upgrade: bool = True,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.max_retries = max_retries if max_retries is not None else self.config.reflexion_max_retries
        self.auto_upgrade = auto_upgrade

    # ── ① ② 任务分析 + 复杂度评估 ─────────────────────────

    def analyze_task(self, task: str) -> dict:
        """LLM 结构化分析任务。返回领域、复杂度、工具需求、质量要求。"""
        prompt = (
            "Analyze the following task and return ONLY a JSON object:\n\n"
            "{\n"
            '  "domain": "code"|"data"|"search"|"creative"|"knowledge"|"general",\n'
            '  "complexity": 1-10,\n'
            '  "needs_tools": true|false,\n'
            '  "quality_critical": true|false,\n'
            '  "estimated_steps": 1-10,\n'
            '  "reasoning": "brief explanation"\n'
            "}\n\n"
            f"Task: {task}"
        )
        messages = [{"role": "user", "content": prompt}]
        data = self._chat_json(messages)
        if data and isinstance(data, dict) and "complexity" in data:
            return data
        return {
            "domain": "general", "complexity": 5,
            "needs_tools": False, "quality_critical": False,
            "estimated_steps": 1, "reasoning": "fallback"
        }

    # ── ③ 选择推理深度 ────────────────────────────────────

    def select_strategy(self, analysis: dict) -> tuple[str, dict]:
        """根据复杂度评分选择策略和参数。"""
        score = analysis.get("complexity", 5)
        quality = analysis.get("quality_critical", False)
        steps = analysis.get("estimated_steps", 1)

        if quality or score >= 7:
            return "reflexion", {"max_retries": min(self.max_retries, 6)}
        elif steps >= 3 or score >= 5:
            return "plan-execute", {}
        elif analysis.get("domain") in ("creative",):
            return "tree-of-thought", {"num_candidates": 3}
        else:
            return "default", {}

    # ── ⑤ 反馈评估 ────────────────────────────────────────

    @staticmethod
    def _extract_circuit_verdict(result: str) -> dict | None:
        """Parse result text for circuit simulation signals.

        Returns structured verdict dict if circuit-related signals found,
        otherwise None (non-circuit task or no simulation data).
        """
        import re

        verdict = {
            "sim_passed": None,      # True/False/None (no sim data)
            "electrical_ok": None,   # True/False/None
            "spec_compliant": None,  # True/False/None
            "errors": [],
            "warnings": [],
            "metrics": {},
            "technical_score": None,  # 0-10 based on objective data
            "is_circuit_task": False,
        }

        # ── Detect circuit task ──
        circuit_keywords = (
            "simulate_spice", "draw_analog_svg", "draw_analog_spice",
            "design_circuit", "SPICE", "ngspice", "circuit", "filter",
            "amplifier", "opamp", "oscillator", "simulation", "gain",
            "cutoff", "rectifier", "Electrical Validation", "Measured fc",
            "Measured gain", "Target fc", "Target gain",
            # Digital circuit keywords
            "design_digital", "simulate_verilog", "synthesize_gates",
            "iverilog", "vvp", "yosys", "Verilog", "gate count",
            "Synthesis", "Compilation", "assertions",
        )
        circuit_patterns = (
            r"Simulation\s*(Complete|verified|passed|Failed)",
            r"Electrical\s*(Validation|Issues)",
            r"SPICE\s*Netlist",
            r"ngspice",
            # Digital patterns
            r"Compilation\s*(passed|Failed|failed)",
            r"Synthesis\s*(complete|Complete|Failed|failed)",
            r"Gate\s*Count",
            r"Assertions?\s*(passed|failed|ok)",
        )
        is_circuit = any(kw.lower() in result.lower() for kw in circuit_keywords)
        if not is_circuit:
            is_circuit = any(re.search(pat, result) for pat in circuit_patterns)
        if not is_circuit:
            return None

        verdict["is_circuit_task"] = True

        # ── Simulation status ──
        sim_ok_patterns = (
            r"✅\s*Simulation\s*(Complete|verified|passed|Passed)",
            r"Simulation\s*\|\s*✅\s*(Passed|passed)",
            r"Simulation\s*verified",
        )
        sim_fail_patterns = (
            r"❌\s*Simulation\s*Failed",
            r"Simulation\s*\|\s*❌\s*(Failed|failed)",
            r"❌\s*\*?\*?Circuit\s*simulation\s*failed",
            r"⚠️\s*Simulation\s*Failed",
            r"⚠️\s*Ngspice\s*validation\s*failed",
            r"Error on line",
            r"FATAL",
        )

        for pat in sim_ok_patterns:
            if re.search(pat, result):
                verdict["sim_passed"] = True
                break

        if verdict["sim_passed"] is None:
            for pat in sim_fail_patterns:
                if re.search(pat, result):
                    verdict["sim_passed"] = False
                    # Extract first error line
                    for line in result.split("\n"):
                        if re.search(r"(Error|FATAL|parse error|too few nodes)", line,
                                     re.IGNORECASE):
                            err = line.strip()[:200]
                            if err and "no errors" not in err.lower():
                                verdict["errors"].append(err)
                            break
                    break

        # ── Electrical validation ──
        if re.search(r"Electrical\s*Validation\s*Failed|Electrical\s*Issues", result):
            verdict["electrical_ok"] = False
            # Extract first electrical issue
            m = re.search(r"-\s*(.+?)(?:\n|$)", result)
            if m:
                verdict["warnings"].append(m.group(1).strip()[:200])
        elif verdict["sim_passed"]:
            verdict["electrical_ok"] = True

        # ── Health check warnings ──
        health_section = re.search(
            r"Health Check.*?\n(.*?)(?=\n\n|\n📥|\n---|\Z)", result, re.DOTALL)
        if health_section:
            for line in health_section.group(1).split("\n"):
                if "⚠️" in line:
                    verdict["warnings"].append(line.strip()[:200])

        # ── Metrics: measured vs target ──
        metric_patterns = [
            (r"\*\*Measured\s+(\w+)\*\*:\s*([\d.]+)\s*(\w*)", "measured"),
            (r"\*\*Target\s+(\w+)\*\*:\s*([\d.]+)\s*(\w*)", "target"),
        ]
        for pat, label in metric_patterns:
            for m in re.finditer(pat, result):
                key = m.group(1)
                val = float(m.group(2))
                unit = m.group(3)
                verdict["metrics"].setdefault(key, {})[label] = val
                if unit:
                    verdict["metrics"][key]["unit"] = unit

        # ── Spec compliance ──
        if re.search(r"✅\s*Within\s*spec", result):
            verdict["spec_compliant"] = True
        elif re.search(r"⚠️\s*Off\s*by\s*([\d.]+)%", result):
            verdict["spec_compliant"] = False
            m = re.search(r"⚠️\s*Off\s*by\s*([\d.]+)%", result)
            if m:
                verdict["warnings"].append(f"Spec off by {m.group(1)}%")

        # ── Q factor ──
        q_match = re.search(r"Q\s*Factor\s*\|\s*\*?\*?([\d.]+)\*?\*?", result)
        if q_match:
            verdict["q_factor"] = float(q_match.group(1))

        # ── P0: GBW ──
        gbw_match = re.search(r"GBW.*?\|\s*\*?\*?([\d.]+)\s*(MHz|kHz|Hz)", result)
        if gbw_match:
            val = float(gbw_match.group(1))
            unit = gbw_match.group(2)
            if unit == "MHz":
                val *= 1e6
            elif unit == "kHz":
                val *= 1e3
            verdict["gbw"] = val

        # ── P1: Phase margin ──
        pm_match = re.search(r"Phase\s*Margin\s*\|\s*\*?\*?([\d.]+)°", result)
        if pm_match:
            verdict["phase_margin_deg"] = float(pm_match.group(1))

        # ── P2: CMRR ──
        cmrr_match = re.search(r"CMRR:\s*~?(\d+)\s*dB", result)
        if cmrr_match:
            verdict["cmrr_db"] = int(cmrr_match.group(1))

        # ── P3: Slew rate ──
        sr_match = re.search(r"Slew\s*Rate\s*\|\s*\*?\*?([\d.]+)\s*(V/µs|V/ms)", result)
        if sr_match:
            val = float(sr_match.group(1))
            unit = sr_match.group(2)
            if unit == "V/ms":
                val *= 1e-3  # convert to V/µs
            verdict["slew_rate"] = val  # stored as V/µs

        # ── Digital circuit metrics ──
        # Compilation (match both table format and free text)
        compile_ok = (
            re.search(r"Compilation\s*\|\s*✅?\s*(Passed|passed)", result) or
            re.search(r"✅\s*Compilation\s*(passed|Passed)", result) or
            re.search(r"Compilation\s*passed", result, re.IGNORECASE)
        )
        compile_fail = (
            re.search(r"Compilation\s*\|\s*❌.*(Failed|failed)", result) or
            re.search(r"❌.*Compilation.*[Ff]ailed", result) or
            re.search(r"Compilation\s*Failed", result)
        )
        if compile_ok:
            verdict["compile_passed"] = True
        elif compile_fail:
            verdict["compile_passed"] = False

        # Gate count (match table format + free text)
        gate_match = (
            re.search(r"Gate\s*Count\s*\|\s*\*?\*?(\d+)\*?\*?", result) or
            re.search(r"\*{0,2}(\d+)\s*gates?\*{0,2}", result)
        )
        if gate_match:
            verdict["gate_count"] = int(gate_match.group(1))

        # Synthesis
        synth_ok = (
            re.search(r"Synthesis\s*\|\s*✅?\s*(Complete|complete)", result) or
            re.search(r"✅\s*Synthesis\s*(complete|Complete)", result) or
            re.search(r"Synthesis\s*(complete|Complete)", result)
        )
        synth_fail = (
            re.search(r"Synthesis\s*\|\s*❌.*(Failed|failed)", result) or
            re.search(r"❌.*Synthesis.*[Ff]ailed", result) or
            re.search(r"Synthesis\s*[Ff]ailed", result)
        )
        if synth_ok:
            verdict["synth_passed"] = True
        elif synth_fail:
            verdict["synth_passed"] = False

        # Assertions from vvp simulation
        ap_match = re.search(r"Assertions?\s*Passed\s*\|\s*(\d+)", result)
        af_match = re.search(r"Assertions?\s*Failed\s*\|\s*(\d+)", result)
        if ap_match:
            verdict["assertions_passed"] = int(ap_match.group(1))
        if af_match:
            verdict["assertions_failed"] = int(af_match.group(1))
        # Also check PASS/FAIL count from simulation output
        pass_count = len(re.findall(r"\bPASS\b", result))
        fail_count = len(re.findall(r"\bFAIL\b", result))
        if pass_count > 0 or fail_count > 0:
            verdict.setdefault("assertions_passed", 0)
            verdict.setdefault("assertions_failed", 0)
            if pass_count > verdict["assertions_passed"]:
                verdict["assertions_passed"] = pass_count
            if fail_count > verdict["assertions_failed"]:
                verdict["assertions_failed"] = fail_count

        # ── Auto-fix / optimization count ──
        opt_match = re.search(r"Optimized in (\d+) iteration", result)
        if opt_match:
            verdict["opt_iterations"] = int(opt_match.group(1))

        # ── Layout quality ──
        lq_match = re.search(
            r"Layout\s*Quality\s*\|\s*.*?\((\d+)/10\)", result)
        if lq_match:
            verdict["layout_score"] = int(lq_match.group(1))
        x_match = re.search(r"Wire\s*Crossings\s*\|\s*(\d+)", result)
        if x_match:
            xc = int(x_match.group(1))
            verdict["wire_crossings"] = xc
            if xc > 0:
                verdict.setdefault("warnings", []).append(
                    f"{xc} wire crossing(s) in schematic")
        o_match = re.search(r"Wire/Gate\s*Overlaps\s*\|\s*(\d+)", result)
        if o_match:
            wo = int(o_match.group(1))
            verdict["wire_overlaps"] = wo
            if wo > 0:
                verdict.setdefault("warnings", []).append(
                    f"{wo} wire/gate overlap(s) in schematic")
        # Rich layout summary (for LLM reasoning)
        lsm = re.search(
            r"Layout:\s*(.+?)(?=\n|$)", result)
        if lsm:
            verdict["layout_summary"] = lsm.group(1).strip()

        # ── Compute technical_score from objective data ──
        verdict["technical_score"] = MetaStrategy._compute_technical_score(verdict)

        return verdict

    @staticmethod
    def _compute_technical_score(verdict: dict) -> float:
        """Compute 0-10 technical score purely from simulation data.

        Scoring logic (additive, capped at 10):
          - sim_passed=True:       +4  (foundation)
          - sim_passed=False:      +0  (automatic fail)
          - electrical_ok=True:    +2
          - spec_compliant=True:   +2  (met target fc/gain)
          - spec_compliant=False:  +0
          - Q in range [0.3,3]:   +1  (reasonable selectivity)
          - no errors/warnings:    +1
          - spec_compliant unset
            but sim+elec ok:       +1  (partial, no spec given)
        """
        score = 0.0

        if verdict["sim_passed"] is True:
            score += 3.0  # simulation passing is the foundation
        elif verdict["sim_passed"] is False:
            return max(0.0, score)  # sim failed: cap at current

        # Analog-specific metrics (skip for pure digital circuits)
        # Digital circuits have compile/synth/gate metrics; analog has SPICE metrics
        is_digital = (verdict.get("compile_passed") is not None
                      or verdict.get("synth_passed") is not None
                      or verdict.get("gate_count") is not None)

        if not is_digital:
            if verdict["electrical_ok"] is True:
                score += 2.0

            if verdict["spec_compliant"] is True:
                score += 2.0
            elif verdict["spec_compliant"] is None:
                # No spec given, but sim + electrical both ok
                if verdict["sim_passed"] and verdict["electrical_ok"]:
                    score += 1.0  # partial credit

            # Q factor: reasonable range for most filter/amp designs
            q_val = verdict.get("q_factor")
            if q_val is not None and 0.3 <= q_val <= 3.0:
                score += 1.0

            # P0: GBW — amplifier must have meaningful gain×bandwidth
            gbw = verdict.get("gbw")
            if gbw is not None and gbw >= 1e3:
                score += 1.0  # good: at least 1kHz GBW

            # P1: Phase margin — stability check
            pm = verdict.get("phase_margin_deg")
            if pm is not None and pm >= 45:
                score += 1.0

            # P2: CMRR — common-mode rejection for diff amps
            cmrr = verdict.get("cmrr_db")
            if cmrr is not None:
                if cmrr >= 40:
                    score += 1.0

            # P3: Slew rate — large-signal speed
            sr = verdict.get("slew_rate")
            if sr is not None:
                if sr >= 0.1:  # at least 0.1 V/µs
                    score += 1.0

        # ── Digital circuit metrics ──
        compile_ok = verdict.get("compile_passed")
        if compile_ok is True:
            score += 2.0  # compilation is foundational
        elif compile_ok is False:
            score -= 2.0  # compilation failure is critical

        synth_ok = verdict.get("synth_passed")
        if synth_ok is True:
            score += 1.0  # synthesis success is good

        gate_count = verdict.get("gate_count")
        if gate_count is not None and gate_count > 0:
            score += 1.0  # has a real gate netlist
            if gate_count > 1000:
                verdict.setdefault("warnings", []).append(
                    f"Large gate count ({gate_count}) — consider optimization")

        ap = verdict.get("assertions_passed", 0)
        af = verdict.get("assertions_failed", 0)
        if ap > 0 or af > 0:
            if af == 0 and ap > 0:
                score += 2.0  # all assertions passed
            elif af > 0:
                score -= 1.0  # some assertions failed

        if not verdict["errors"] and not verdict["warnings"]:
            score += 1.0

        # ── Layout quality (digital circuits) ──
        lq = verdict.get("layout_score")
        if lq is not None:
            if lq >= 8:
                score += 1.0   # clean layout
            elif lq < 5:
                score -= 1.0   # messy layout
            # 5-7: neutral — no bonus, no penalty

        # Floor: digital compile failure is critical
        if compile_ok is False:
            score = min(score, 2.0)

        return min(10.0, max(0.0, score))

    def evaluate_result(self, task: str, result: str, analysis: dict,
                        circuit_verdict: dict = None) -> dict:
        """LLM 评估执行结果。如果提供了 circuit_verdict（仿真数据），
        将其作为客观参考注入评估 prompt，约束 LLM 的评分。
        """
        # ── Build structured circuit verdict section ──
        verdict_text = ""
        if circuit_verdict and circuit_verdict.get("is_circuit_task"):
            v = circuit_verdict
            lines = [
                "\n**Circuit Simulation Verdict (GROUND TRUTH — use this to score):**",
            ]
            # Simulation status
            sim_label = {True: "✅ PASSED", False: "❌ FAILED", None: "⚠️ UNKNOWN"}
            lines.append(f"- Simulation: {sim_label.get(v['sim_passed'], '⚠️ UNKNOWN')}")

            elec_label = {True: "✅ PASSED", False: "❌ FAILED", None: "N/A"}
            lines.append(f"- Electrical validation: {elec_label.get(v['electrical_ok'], 'N/A')}")

            spec_label = {True: "✅ WITHIN SPEC", False: "❌ OUT OF SPEC", None: "N/A"}
            lines.append(f"- Spec compliance: {spec_label.get(v['spec_compliant'], 'N/A')}")

            if v["metrics"]:
                lines.append("- Measured vs Target:")
                for metric, vals in v["metrics"].items():
                    unit = vals.get("unit", "")
                    measured = vals.get("measured")
                    target = vals.get("target")
                    if measured is not None:
                        line = f"  • {metric}: measured={measured}{unit}"
                        if target is not None:
                            if measured > 0 and abs(target) > 1e-12:
                                err = abs(measured - target) / abs(target) * 100
                                line += f", target={target}{unit}, error={err:.1f}%"
                            else:
                                line += f", target={target}{unit}"
                        lines.append(line)

            if v["errors"]:
                lines.append("- Errors from ngspice:")
                for e in v["errors"][:3]:
                    lines.append(f"  • {e[:150]}")

            if v["warnings"]:
                lines.append("- Warnings:")
                for w in v["warnings"][:3]:
                    lines.append(f"  • {w[:150]}")

            technical = v.get("technical_score")
            if technical is not None:
                lines.append(f"\n**Pre-computed technical score (from simulation data): {technical:.0f}/10**")
                lines.append("Use this as your baseline. You may adjust ±1 based on output quality, "
                             "but do NOT override simulation failures with high scores.")

            # Rich layout metrics for LLM reasoning
            layout_summary = v.get("layout_summary", "")
            if layout_summary:
                lines.append(f"\n**Schematic Layout Analysis:**\n{layout_summary}")
                lines.append(
                    "Evaluate layout quality: consider crossings (fewer is better), "
                    "wire density (lower is better), aspect ratio (balanced is better), "
                    "fan-out (lower is more readable). Adjust score by ±1 for layout quality.")

            verdict_text = "\n".join(lines)

        prompt = (
            "Evaluate the result of executing this task. Return ONLY a JSON object:\n\n"
            "{\n"
            '  "status": "success"|"partial"|"failed",\n'
            '  "score": 0-10,\n'
            '  "issues": ["..."],\n'
            '  "suggestion": "how to improve"\n'
            "}\n\n"
            "**Scoring rules when circuit simulation data is present:**\n"
            "- Simulation FAILED → status MUST be 'failed', score ≤ 3\n"
            "- Simulation passed but spec NOT met → status 'partial', score 4-6\n"
            "- Simulation passed AND spec met → status 'success', score ≥ 9\n"
            "- No simulation data → score based on general task completion quality\n\n"
            f"Task: {task}\n"
            f"Expected complexity: {analysis.get('complexity', '?')}/10\n"
            f"{verdict_text}\n"
            f"Result: {result[:2000]}"
        )
        messages = [{"role": "user", "content": prompt}]
        data = self._chat_json(messages)
        if data and isinstance(data, dict) and "status" in data:
            # ── Sanity check: don't let LLM override hard simulation failures ──
            if circuit_verdict and circuit_verdict.get("sim_passed") is False:
                data["status"] = "failed"
                data["score"] = min(data.get("score", 3), 3)
            return data
        return {"status": "success", "score": 7, "issues": [], "suggestion": ""}

    # ── ⑥ 反思修复 ────────────────────────────────────────

    def extract_lesson(self, task: str, result: str, evaluation: dict) -> str:
        """从失败中提取教训。"""
        if evaluation.get("status") == "success":
            return ""

        prompt = (
            "Based on the failed task, extract a general lesson. "
            "Respond with ONLY one line starting with 'LESSON: '\n\n"
            f"Task: {task}\n"
            f"Issues: {json.dumps(evaluation.get('issues', []))}\n"
            f"Suggestion: {evaluation.get('suggestion', '')}"
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            resp = self.llm.chat(messages=messages, tools=[], system="Be concise.",
                                   model=self._model_override)
            text = resp["text"].strip()
            if "LESSON:" in text:
                return text
        except Exception:
            pass
        return ""

    # ── 主流水线 ──────────────────────────────────────────

    def run(self, task: str, agent_loop_fn) -> str:
        """执行统一推理流水线。"""
        logger.info(f"{'='*60}")
        logger.info(f"[Meta] Task: {task}")
        logger.info(f"{'='*60}")

        # ── ① ② 任务分析 + 复杂度评估 ──
        self.emit("text", {"text": "🔍 正在分析任务..."})
        analysis = self.analyze_task(task)
        logger.info(f"[Meta] Analysis: complexity={analysis['complexity']}/10, "
                    f"domain={analysis['domain']}, quality={analysis['quality_critical']}")

        # ── ③ 选择推理深度 ──
        strategy_name, strategy_params = self.select_strategy(analysis)
        logger.info(f"[Meta] Selected: {strategy_name} {strategy_params}")
        self.emit("text", {"text": f"📊 复杂度: {analysis['complexity']}/10 → 策略: {strategy_name}"})

        # ── 创建共享上下文（整个流水线复用）──
        # 子策略通过 ctx.pipeline_state 共享中间结果，
        # 避免 ToT 探索的候选方案被 PlanExecute 重复计算。
        from .context import StrategyContext
        shared_ctx = StrategyContext(
            config=self.config, llm=self.llm, tools=self.tools,
            memory=self.memory, emit=self._emit,
            execute_tool=self._execute_tool, agent_loop=self._agent_loop,
            orient_fn=self._orient_fn, model_override=self._model_override,
            system_prompt_fn=self._system_prompt_fn,
            pipeline_state={"meta": {"attempts": []}},
        )

        # ── ④ ⑤ 执行 + 失败重试 ──
        best_result = ""
        best_score = -1
        current_strategy = strategy_name
        current_params = strategy_params
        last_eval = {"status": "unknown", "score": 0, "issues": [], "suggestion": ""}

        for attempt in range(self.max_retries):
            logger.info(f"[Meta Attempt {attempt+1}/{self.max_retries}] strategy={current_strategy}")

            # 重试时注入前序失败教训 + 前序策略的中间产物
            sub_task = task
            extra_context = ""
            if attempt > 0:
                # 收集前序策略留下的产物
                ps = shared_ctx.pipeline_state
                tot = ps.get("tot", {})
                candidates = tot.get("candidates", [])
                if candidates:
                    best_c = max(candidates, key=lambda c: c.get("score", 0))
                    extra_context += (
                        f"[Previous strategy explored {len(candidates)} approaches. "
                        f"Best (score {best_c.get('score')}/10): {best_c.get('approach', '')}] "
                    )
                reflexion = ps.get("reflexion", {})
                lessons = reflexion.get("lessons", [])
                if lessons:
                    extra_context += (
                        f"[Lessons from previous attempts: {'; '.join(lessons[:3])}] "
                    )

                if last_eval.get("issues"):
                    extra_context += (
                        f"[Previous attempt scored {best_score}/10. "
                        f"Issues: {json.dumps(last_eval.get('issues', []))}. "
                        f"Suggestion: {last_eval.get('suggestion', '')} "
                        f"Please try a different approach.]"
                    )

                if extra_context:
                    sub_task = f"{task}\n\n{extra_context}"

            # 实例化并执行子策略（共享 pipeline_state）
            result = self._dispatch_sub_strategy(
                current_strategy, sub_task, agent_loop_fn,
                shared_ctx=shared_ctx, **current_params)
            logger.info(f"[Meta Result] {result[:300]}...")

            # ── ⑤ 反馈评估 ──
            circuit_verdict = self._extract_circuit_verdict(result)
            evaluation = self.evaluate_result(task, result, analysis,
                                              circuit_verdict=circuit_verdict)
            score = evaluation.get("score", 5)
            logger.info(f"[Meta Eval] status={evaluation['status']} score={score}/10"
                        f"{' (technical=' + str(circuit_verdict.get('technical_score', '?')) + ')' if circuit_verdict else ''}")

            # 记录到 pipeline
            with shared_ctx.pipeline_lock:
                shared_ctx.pipeline_state["meta"]["attempts"].append({
                    "strategy": current_strategy,
                    "score": score,
                    "artifacts_available": [
                        k for k in shared_ctx.pipeline_state
                        if k != "meta" and shared_ctx.pipeline_state.get(k)
                    ],
                })

            # 保留最佳
            if score > best_score:
                best_score = score
                best_result = result
            last_eval = evaluation

            # 成功 → 结束
            # 有 spec 时要求 ≥9 分（仿真+电气+spec 三者俱佳）
            # 无 spec 时降到 7 分（仿真+电气通过即可），避免无效重试
            threshold = 9
            if circuit_verdict and circuit_verdict.get("spec_compliant") is None \
                    and circuit_verdict.get("sim_passed"):
                # No spec was given — lower the bar
                if not circuit_verdict.get("metrics"):
                    threshold = 7  # purely qualitative, sim+elec ok is enough
            if evaluation["status"] == "success" and score >= threshold:
                logger.info(f"[Meta] Success on attempt {attempt+1} (threshold={threshold})")
                break

            # 失败 → 尝试升级策略
            if self.auto_upgrade and attempt < self.max_retries - 1:
                old = current_strategy
                current_strategy = self._upgrade_strategy(current_strategy, score)
                if current_strategy != old:
                    _, current_params = self.select_strategy({
                        "complexity": score, "quality_critical": True,
                        "estimated_steps": 3,
                    })
                    logger.info(f"[Meta] Upgraded: {old} → {current_strategy}")
                    self.emit("text", {"text": f"🔄 升级策略: {old} → {current_strategy}"})

        # ── ⑥ 反思修复 ──
        lesson = self.extract_lesson(task, best_result, last_eval)
        if lesson and self.memory:
            try:
                self.memory.save_reflection(task, lesson, last_eval)
                logger.info(f"[Meta Lesson] {lesson[:200]}")
            except Exception:
                pass

        logger.info(f"[Meta] Complete: {analysis['complexity']}/10 → {current_strategy} "
                    f"→ score {best_score}/10")
        return best_result

    def _dispatch_sub_strategy(self, strategy_name: str, task: str,
                                agent_loop_fn, shared_ctx=None, **params) -> str:
        """实例化并运行子策略。

        Args:
            shared_ctx: 共享 StrategyContext。Meta 流水线创建一次，
                        所有子策略复用，通过 pipeline_state 共享中间结果。
                        为 None 时创建新 context（兼容非 Meta 调用）。
        """
        from . import STRATEGY_REGISTRY
        from .context import StrategyContext

        sub_cls = STRATEGY_REGISTRY.get(strategy_name, STRATEGY_REGISTRY["default"])
        # 防止无限递归：Meta → Meta
        if sub_cls is type(self):
            sub_cls = STRATEGY_REGISTRY["default"]

        # 优先复用共享上下文，否则创建新的（向后兼容）
        ctx = shared_ctx or StrategyContext(
            config=self.config, llm=self.llm, tools=self.tools,
            memory=self.memory, emit=self._emit,
            execute_tool=self._execute_tool, agent_loop=self._agent_loop,
            orient_fn=self._orient_fn, model_override=self._model_override,
            system_prompt_fn=self._system_prompt_fn,
        )
        kwargs = dict(sub_cls.default_params)
        kwargs.update(params)
        kwargs["memory"] = self.memory
        sub = sub_cls(ctx.config, ctx.llm, ctx.tools, context=ctx, **kwargs)
        return sub.run(task, agent_loop_fn)

    @staticmethod
    def _upgrade_strategy(current: str, score: int) -> str:
        """根据失败分数升级策略。"""
        if score < 3:
            return "reflexion"  # 严重失败 → 反思重试
        if score < 5:
            if current == "default":
                return "react"
            if current == "react":
                return "plan-execute"
        return current  # 不变
