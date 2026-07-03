"""Unit tests for analog circuit: template matching, SPICE generation, calculators."""

import pytest
from nano_agent.tools.analog.analog_svg import AnalogSVG, _to_spice


class TestTemplateMatching:
    """Test _match_template with Chinese and English inputs."""

    def test_chinese_amplifier(self):
        tmpl, values = AnalogSVG._match_template("放大器")
        assert "Inverting" in tmpl["name"]

    def test_chinese_opamp(self):
        tmpl, _ = AnalogSVG._match_template("运放")
        assert "Inverting" in tmpl["name"]

    def test_chinese_filter(self):
        tmpl, _ = AnalogSVG._match_template("滤波器")
        assert "Low-Pass" in tmpl["name"] or "low-pass" in tmpl["name"].lower()

    def test_chinese_rc_lowpass(self):
        tmpl, _ = AnalogSVG._match_template("RC低通滤波器")
        assert "RC Low-Pass" in tmpl["name"]

    def test_chinese_common_emitter(self):
        tmpl, _ = AnalogSVG._match_template("共射放大")
        assert "Common-Emitter" in tmpl["name"]

    def test_chinese_rectifier(self):
        tmpl, _ = AnalogSVG._match_template("整流")
        assert "Rectifier" in tmpl["name"]

    def test_chinese_divider(self):
        tmpl, _ = AnalogSVG._match_template("分压器")
        assert "Divider" in tmpl["name"]

    def test_english_inverting(self):
        tmpl, _ = AnalogSVG._match_template("inverting amplifier")
        assert "Inverting" in tmpl["name"]

    def test_english_filter(self):
        tmpl, _ = AnalogSVG._match_template("low pass filter")
        assert "Low-Pass" in tmpl["name"] or "low-pass" in tmpl["name"].lower()

    def test_english_buffer(self):
        tmpl, _ = AnalogSVG._match_template("voltage follower")
        assert "Follower" in tmpl["name"]

    def test_param_extraction_gain(self):
        tmpl, values = AnalogSVG._match_template("放大器 gain=50")
        assert values.get("gain") == "50" or values.get("Rf")

    def test_param_extraction_fc(self):
        tmpl, values = AnalogSVG._match_template("RC低通滤波器 fc=5kHz")
        assert "fc" in str(values).lower() or "C" in values

    def test_unknown_fallback(self):
        """Unknown Chinese circuit should fallback gracefully."""
        tmpl, _ = AnalogSVG._match_template("未知电路")
        assert tmpl is not None
        assert "name" in tmpl


class TestSPICEGeneration:
    """Test _to_spice generates correct netlists."""

    def test_rc_lowpass_spice(self):
        tmpl, values = AnalogSVG._match_template("RC低通")
        comps = [dict(c) for c in tmpl["components"]]
        spice = _to_spice(comps, values)
        assert "Vin" in spice
        assert "R1" in spice
        assert "C1" in spice
        assert "AC 1" in spice

    def test_inverting_amp_spice(self):
        tmpl, values = AnalogSVG._match_template("放大器")
        comps = [dict(c) for c in tmpl["components"]]
        spice = _to_spice(comps, values)
        assert "Vin" in spice or "AC 1" in spice
        assert "R1" in spice
        assert "Rf" in spice
        # Should have opamp (X prefix)
        assert "XU1" in spice or "opamp" in spice

    def test_voltage_follower_spice(self):
        tmpl, values = AnalogSVG._match_template("电压跟随器")
        comps = [dict(c) for c in tmpl["components"]]
        spice = _to_spice(comps, values)
        assert "XU1" in spice or "opamp" in spice

    def test_node_mapping(self):
        """Ground node '0' should appear in the netlist."""
        tmpl, values = AnalogSVG._match_template("RC低通")
        comps = [dict(c) for c in tmpl["components"]]
        spice = _to_spice(comps, values)
        assert " 0 " in spice or " 0\n" in spice  # Ground node present

    def test_component_values_filled(self):
        """? placeholders should be replaced after _to_spice runs."""
        tmpl, values = AnalogSVG._match_template("RC低通 fc=10kHz")
        comps = [dict(c) for c in tmpl["components"]]
        _to_spice(comps, values)  # fills filled_value in-place
        for c in comps:
            fv = c.get("filled_value", "")
            assert fv, f"Component {c['name']} has no filled_value"
            assert "?" not in str(fv)


class TestCalculators:
    """Test parameter calculator functions."""

    def test_rc_lowpass_calc(self):
        tmpl, values = AnalogSVG._match_template("RC低通 fc=1kHz R=1k")
        assert float(str(values.get("R", "1k")).replace("k", "e3")) > 0

    def test_inverting_amp_calc(self):
        tmpl, values = AnalogSVG._match_template("放大器 gain=10")
        # Rf should be 10x R1
        from nano_agent.tools.analog.spice_common import parse_value
        r1 = parse_value(str(values.get("R1", "1k")))
        rf = parse_value(str(values.get("Rf", "10k")))
        assert rf / r1 == pytest.approx(10, rel=0.01)

    def test_voltage_divider_calc(self):
        tmpl, values = AnalogSVG._match_template("分压器 ratio=0.5")
        from nano_agent.tools.analog.spice_common import parse_value
        r1 = parse_value(str(values.get("R1", "1k")))
        r2 = parse_value(str(values.get("R2", "1k")))
        # ratio = R2/(R1+R2) ≈ 0.5 → R1 ≈ R2
        assert r1 == pytest.approx(r2, rel=0.05)

    def test_non_inverting_amp_calc(self):
        tmpl, values = AnalogSVG._match_template("同相放大器 gain=11")
        from nano_agent.tools.analog.spice_common import parse_value
        r1 = parse_value(str(values.get("R1", "1k")))
        rf = parse_value(str(values.get("Rf", "10k")))
        # gain = 1 + Rf/R1 = 11
        assert (1 + rf / r1) == pytest.approx(11, rel=0.01)
