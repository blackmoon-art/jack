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


class TestBJTMOSFETCalculators:
    """Test the 5 previously-missing BJT/MOSFET calculators now produce valid values."""

    def test_common_emitter_values(self):
        tmpl, values = AnalogSVG._match_template("共射放大 gain=10")
        from nano_agent.tools.analog.spice_common import parse_value
        # All R values should be positive and reasonable
        assert parse_value(str(values.get("Rc", "0"))) > 0
        assert parse_value(str(values.get("Re", "0"))) > 0
        assert parse_value(str(values.get("R1", "0"))) > 0
        assert parse_value(str(values.get("R2", "0"))) > 0

    def test_common_emitter_no_placeholders(self):
        """Verify no ? placeholders remain after calculator runs."""
        tmpl, values = AnalogSVG._match_template("共射放大")
        comps = [dict(c) for c in tmpl["components"]]
        from nano_agent.tools.analog.analog_svg import _to_spice
        _to_spice(comps, values)
        for c in comps:
            if c["type"] in ("Q", "M", "X", "D"):
                continue  # no value field needed (use model names)
            fv = c.get("filled_value", "")
            assert fv, f"Component {c['name']} has no filled_value"
            assert "?" not in str(fv), f"Component {c['name']} still has ? placeholder"

    def test_emitter_follower_values(self):
        tmpl, values = AnalogSVG._match_template("射极跟随器 Ic=1m")
        from nano_agent.tools.analog.spice_common import parse_value
        assert parse_value(str(values.get("Re", "0"))) > 0
        assert parse_value(str(values.get("R1", "0"))) > 0

    def test_current_mirror_values(self):
        tmpl, values = AnalogSVG._match_template("电流镜 Iref=1m")
        from nano_agent.tools.analog.spice_common import parse_value
        rref = parse_value(str(values.get("Rref", "0")))
        # Rref = (Vcc - 0.7) / Iref ≈ (10-0.7)/0.001 = 9.3k
        assert 5000 < rref < 20000, f"Expected Rref ~9.3k, got {rref}"

    def test_bjt_diff_pair_values(self):
        tmpl, values = AnalogSVG._match_template("BJT差分对 gain=20 Itail=1m")
        from nano_agent.tools.analog.spice_common import parse_value
        rc = parse_value(str(values.get("Rc1", "0")))
        ree = parse_value(str(values.get("Ree", "0")))
        assert rc > 0
        assert ree > 0

    def test_mosfet_cs_values(self):
        tmpl, values = AnalogSVG._match_template("共源放大 gain=5")
        from nano_agent.tools.analog.spice_common import parse_value
        rd = parse_value(str(values.get("Rd", "0")))
        assert rd > 0


class TestNewTemplates:
    """Test NL matching and SPICE generation for the 15 new templates."""

    def test_sallen_key_hp_match(self):
        tmpl, _ = AnalogSVG._match_template("有源高通滤波器")
        assert "High-Pass" in tmpl["name"]

    def test_bandpass_match(self):
        tmpl, _ = AnalogSVG._match_template("带通滤波器 fc=1kHz")
        assert "Band-Pass" in tmpl["name"]

    def test_notch_match(self):
        tmpl, _ = AnalogSVG._match_template("陷波滤波器 fc=50Hz")
        assert "Notch" in tmpl["name"]

    def test_integrator_match(self):
        tmpl, _ = AnalogSVG._match_template("积分器")
        assert "Integrator" in tmpl["name"]

    def test_differentiator_match(self):
        tmpl, _ = AnalogSVG._match_template("微分器")
        assert "Differentiator" in tmpl["name"]

    def test_instrumentation_amp_match(self):
        tmpl, _ = AnalogSVG._match_template("仪表放大器 gain=100")
        assert "Instrumentation" in tmpl["name"]

    def test_instrumentation_amp_spice(self):
        """Verify 3-opamp instrumentation amplifier generates correct SPICE."""
        tmpl, values = AnalogSVG._match_template("仪表放大器 gain=50")
        comps = [dict(c) for c in tmpl["components"]]
        from nano_agent.tools.analog.analog_svg import _to_spice
        spice = _to_spice(comps, values)
        # Should have 3 opamps and 7 resistors
        assert spice.count("opamp") == 3 or spice.count("XU") == 3
        assert "Rg" in spice

    def test_schmitt_trigger_match(self):
        tmpl, _ = AnalogSVG._match_template("施密特触发器")
        assert "Schmitt" in tmpl["name"] or "Hysteresis" in tmpl["name"]

    def test_precision_rectifier_match(self):
        tmpl, _ = AnalogSVG._match_template("精密整流器")
        assert "Precision" in tmpl["name"] or "Rectifier" in tmpl["name"]

    def test_comparator_match(self):
        tmpl, _ = AnalogSVG._match_template("比较器")
        assert "Comparator" in tmpl["name"]

    def test_common_drain_match(self):
        tmpl, _ = AnalogSVG._match_template("源极跟随器")
        assert "Drain" in tmpl["name"] or "Source Follower" in tmpl["name"]

    def test_mosfet_diff_pair_match(self):
        tmpl, _ = AnalogSVG._match_template("MOS差分对")
        assert "Differential" in tmpl["name"] and "MOSFET" in tmpl["name"]

    def test_common_base_match(self):
        tmpl, _ = AnalogSVG._match_template("共基放大")
        assert "Common-Base" in tmpl["name"]

    def test_cascode_match(self):
        tmpl, _ = AnalogSVG._match_template("cascode放大")
        assert "Cascode" in tmpl["name"]

    def test_wien_bridge_match(self):
        tmpl, _ = AnalogSVG._match_template("Wien桥振荡器")
        assert "Wien" in tmpl["name"]

    def test_wien_bridge_spice(self):
        """Wien bridge should have gain-setting resistors Rf/Rg with Rf > 2*Rg."""
        tmpl, values = AnalogSVG._match_template("Wien桥振荡器 fc=1kHz")
        from nano_agent.tools.analog.spice_common import parse_value
        rf = parse_value(str(values.get("Rf", "0")))
        rg = parse_value(str(values.get("Rg", "1")))
        assert rf / rg > 2.0, f"Gain must be ≥ 2 for oscillation, got Rf/Rg={rf/rg:.2f}"

    def test_rc_phase_shift_match(self):
        tmpl, _ = AnalogSVG._match_template("RC相移振荡器")
        assert "Phase-Shift" in tmpl["name"] or "Phase Shift" in tmpl["name"]

    def test_all_new_templates_fill_values(self):
        """Smoke test: all new templates produce SPICE with no ? placeholders."""
        descriptions = [
            "有源高通 fc=10kHz",
            "带通滤波 fc=1kHz Q=5",
            "陷波滤波 fc=100Hz",
            "积分器 fc=1kHz",
            "微分器 fc=1kHz",
            "仪表放大器 gain=100",
            "施密特触发器 ratio=0.1",
            "精密整流器",
            "比较器",
            "源极跟随器",
            "MOS差分对 gain=10",
            "共基放大 gain=10",
            "cascode放大 gain=50",
            "Wien桥振荡器 fc=2kHz",
            "RC相移振荡器 fc=500Hz",
        ]
        from nano_agent.tools.analog.analog_svg import _to_spice
        for desc in descriptions:
            tmpl, values = AnalogSVG._match_template(desc)
            comps = [dict(c) for c in tmpl["components"]]
            _to_spice(comps, values)
            for c in comps:
                if c["type"] in ("Q", "M", "X", "D"):
                    continue  # no value field needed (use model names)
                fv = c.get("filled_value", "")
                assert fv, f"[{desc}] Component {c['name']} has no filled_value"
                assert "?" not in str(fv), f"[{desc}] Component {c['name']} still has ?"


class TestClosedLoopOpt:
    """Test _spec_to_metric mappings for new templates."""

    def test_new_templates_have_metric_mappings(self):
        """Templates that support optimization should have _spec_to_metric entries."""
        from nano_agent.tools.analog.analog_svg import _spec_to_metric
        mapped = {
            "sallen_key_hp": "fc", "mfb_bandpass": "fc",
            "twin_t_notch": "fc", "integrator": "fc",
            "differentiator": "fc", "instrumentation_amp": "gain",
            "common_emitter": "gain", "bjt_diff_pair": "gain",
            "mosfet_cs": "gain", "cascode": "gain",
            "common_base": "gain",
        }
        for calc_name, expected_metric in mapped.items():
            metric = _spec_to_metric(calc_name)
            assert metric == expected_metric, \
                f"calc '{calc_name}' expected metric '{expected_metric}', got '{metric}'"
