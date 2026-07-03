"""Tests for shared SPICE utilities in spice_common.py."""
import unittest
from nano_agent.tools.analog.spice_common import (
    parse_value, format_value, extract_nodes, has_opamp, has_diode,
    replace_opamp_with_e_source, GROUND_NAMES,
)


class TestParseValue(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_value("1k"), 1000)
        self.assertEqual(parse_value("10n"), 1e-8)
        self.assertEqual(parse_value("1u"), 1e-6)
        self.assertEqual(parse_value("100"), 100)
        self.assertEqual(parse_value("1Meg"), 1e6)
        self.assertEqual(parse_value("5kHz"), 5000)
        self.assertEqual(parse_value("0"), 0)

    def test_edge_cases(self):
        self.assertEqual(parse_value(""), 0)
        self.assertEqual(parse_value("invalid"), 0)
        self.assertEqual(parse_value("1.5k"), 1500)


class TestFormatValue(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(format_value(1000), "1k")
        self.assertEqual(format_value(1e-8), "10n")
        self.assertEqual(format_value(1e-6), "1u")
        self.assertEqual(format_value(100), "100")
        self.assertEqual(format_value(1e6), "1Meg")

    def test_edge_cases(self):
        self.assertEqual(format_value(0), "0")
        self.assertEqual(format_value(1500), "1.5k")
        self.assertEqual(format_value(0.001), "1m")


class TestExtractNodes(unittest.TestCase):
    def test_rc_filter(self):
        spice = "Vin in 0 AC 1\nR1 in out 1k\nC1 out 0 10n"
        nodes = extract_nodes(spice)
        self.assertIn("in", nodes)
        self.assertIn("out", nodes)
        self.assertNotIn("0", nodes)

    def test_opamp(self):
        spice = "XU1 n1 n2 out vcc 0 opamp"
        nodes = extract_nodes(spice)
        self.assertEqual(set(nodes), {"n1", "n2", "out", "vcc"})

    def test_ground_excluded(self):
        for gnd in GROUND_NAMES:
            spice = f"R1 1 {gnd} 1k"
            nodes = extract_nodes(spice)
            self.assertNotIn(gnd, nodes)


class TestOpampDetection(unittest.TestCase):
    def test_has_opamp(self):
        self.assertTrue(has_opamp("XU1 1 2 3 4 0 opamp"))
        self.assertTrue(has_opamp("R1 1 2 1k\nX1 a b c d e opamp"))
        self.assertFalse(has_opamp("R1 1 2 1k\nC1 2 0 10n"))

    def test_has_diode(self):
        self.assertTrue(has_diode("D1 1 2"))
        self.assertTrue(has_diode("R1 1 2 1k\nD1 anode cathode"))
        self.assertFalse(has_diode("V1 1 0 DC 5"))  # DC in value, not component

    def test_replace_opamp(self):
        spice = "XU1 4 3 5 6 0 opamp"
        result = replace_opamp_with_e_source(spice)
        self.assertIn("EU1 5 0 4 3", result)
        self.assertNotIn("XU1", result)


if __name__ == "__main__":
    unittest.main()
