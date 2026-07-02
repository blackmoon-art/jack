"""
Visual Router 单元测试 — 验证路由命中率和正确性。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from nano_agent.visual_router import route_visual, is_visual_request, reset_stats, get_stats


class TestLayer1ExactMatch(unittest.TestCase):
    """Layer 1: 精确关键词匹配。"""

    def setUp(self):
        reset_stats()

    def test_line_chart(self):
        self.assertEqual(route_visual("画个折线图"),
                         ("generate_chart", {"chart_type": "line"}))

    def test_bar_chart(self):
        self.assertEqual(route_visual("画柱状图"),
                         ("generate_chart", {"chart_type": "bar"}))

    def test_pie_chart(self):
        self.assertEqual(route_visual("画个饼图"),
                         ("generate_chart", {"chart_type": "pie"}))

    def test_contour(self):
        result = route_visual("画梯度下降等高线图")
        self.assertEqual(result[0], "generate_chart")
        self.assertEqual(result[1]["chart_type"], "contour")

    def test_waveform(self):
        self.assertEqual(route_visual("画时钟波形"),
                         ("generate_chart", {"chart_type": "waveform"}))

    def test_regression(self):
        self.assertEqual(route_visual("做线性回归拟合"),
                         ("generate_chart", {"chart_type": "regression"}))

    def test_function(self):
        self.assertEqual(route_visual("画 y=x² 的函数图"),
                         ("generate_chart", {"chart_type": "function"}))

    def test_geometry(self):
        self.assertEqual(route_visual("证明勾股定理"),
                         ("generate_chart", {"chart_type": "geometry"}))

    def test_wireframe(self):
        self.assertEqual(route_visual("画个3D模型"),
                         ("generate_chart", {"chart_type": "wireframe"}))

    def test_scatter(self):
        self.assertEqual(route_visual("画散点分布"),
                         ("generate_chart", {"chart_type": "scatter"}))

    def test_heatmap(self):
        self.assertEqual(route_visual("画热力图"),
                         ("generate_chart", {"chart_type": "heatmap"}))

    def test_radar(self):
        self.assertEqual(route_visual("画雷达图"),
                         ("generate_chart", {"chart_type": "radar"}))

    def test_histogram(self):
        self.assertEqual(route_visual("画直方图"),
                         ("generate_chart", {"chart_type": "histogram"}))

    def test_curve(self):
        self.assertEqual(route_visual("画平滑曲线"),
                         ("generate_chart", {"chart_type": "curve"}))

    def test_mermaid_flowchart(self):
        result = route_visual("画个流程图")
        self.assertEqual(result[0], "mermaid_chart")

    def test_mermaid_sequence(self):
        result = route_visual("画用户登录时序图")
        self.assertEqual(result[0], "mermaid_chart")

    def test_waveform_hardware_timing(self):
        """硬件时序图应该路由到 waveform，不是 mermaid sequenceDiagram"""
        result = route_visual("画 OCC 时序图")
        self.assertEqual(result[0], "generate_chart")
        self.assertEqual(result[1].get("chart_type"), "waveform")

        result = route_visual("SPI 时序图")
        self.assertEqual(result[0], "generate_chart")
        self.assertEqual(result[1].get("chart_type"), "waveform")

    def test_mermaid_state_machine(self):
        result = route_visual("画状态机")
        self.assertEqual(result[0], "mermaid_chart")

    def test_mermaid_architecture(self):
        result = route_visual("画系统架构图")
        self.assertEqual(result[0], "mermaid_chart")

    def test_mermaid_gantt(self):
        result = route_visual("画甘特图")
        self.assertEqual(result[0], "mermaid_chart")

    def test_draw_circuit(self):
        """通用电路关键词 → LLM 分类触发器（不再盲猜模拟电路）"""
        result = route_visual("画电路原理图")
        self.assertEqual(result[0], "__classify_circuit__")

    def test_ai_image(self):
        result = route_visual("画一只猫")
        self.assertEqual(result[0], "ai_image")

    def test_create_ppt(self):
        result = route_visual("做个PPT")
        self.assertEqual(result[0], "create_ppt")


class TestLayer2IntentMatch(unittest.TestCase):
    """Layer 2: 意图推断（动词+名词组合）。"""

    def setUp(self):
        reset_stats()

    def test_comparison_intent(self):
        """比较 → bar"""
        result = route_visual("比较三个季度的销售数据")
        self.assertEqual(result[0], "generate_chart")
        self.assertEqual(result[1]["chart_type"], "bar")

    def test_trend_intent(self):
        """趋势 → line"""
        result = route_visual("展示温度变化趋势")
        self.assertEqual(result[0], "generate_chart")
        self.assertEqual(result[1]["chart_type"], "line")

    def test_proportion_intent(self):
        """占比 → pie"""
        result = route_visual("各类型占比比例")
        self.assertEqual(result[0], "generate_chart")
        self.assertEqual(result[1]["chart_type"], "pie")

    def test_correlation_intent(self):
        """相关性 → scatter"""
        result = route_visual("看这两个变量的关系")
        self.assertEqual(result[0], "generate_chart")
        self.assertEqual(result[1]["chart_type"], "scatter")


class TestLayer3Fallback(unittest.TestCase):
    """Layer 3: 未命中 → None。"""

    def setUp(self):
        reset_stats()

    def test_vague_request(self):
        """太模糊，交给 LLM"""
        self.assertIsNone(route_visual("画个图看看"))

    def test_non_visual(self):
        """非画图请求"""
        self.assertIsNone(route_visual("今天天气怎么样"))

    def test_empty_string(self):
        self.assertIsNone(route_visual(""))

    def test_stats_tracked(self):
        """统计正确"""
        route_visual("画折线图")     # layer1
        route_visual("比较销售数据")   # layer2
        route_visual("画个图")        # fallback
        stats = get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["layer1_hit"], 1)
        self.assertEqual(stats["layer2_hit"], 1)
        self.assertEqual(stats["fallback"], 1)


class TestIsVisualRequest(unittest.TestCase):
    """is_visual_request 快速判断。"""

    def test_explicit_keyword(self):
        self.assertTrue(is_visual_request("画个折线图"))
        self.assertTrue(is_visual_request("draw a flowchart"))

    def test_non_visual(self):
        self.assertFalse(is_visual_request("今天天气怎么样"))
        self.assertFalse(is_visual_request("计算 1+1"))

    def test_intent_with_draw_hint(self):
        self.assertTrue(is_visual_request("画一下数据的比较"))
        self.assertFalse(is_visual_request("比较两家公司的理念"))


if __name__ == "__main__":
    unittest.main()
