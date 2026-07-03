"""SPICE regression tests — known circuits must produce consistent results.

Each test runs a circuit through simulate_spice and checks key metrics against
expected values with tolerance. Catches regressions in the simulation pipeline.
"""

import unittest
import tempfile
import re
from nano_agent.tools.analog.spice_simulator import SpiceSimulator


class BaseRegressionTest(unittest.TestCase):
    """Base class with shared simulation helpers."""

    @classmethod
    def setUpClass(cls):
        cls.sim = SpiceSimulator(charts_dir=tempfile.mkdtemp())

    def _sim_ac(self, spice: str) -> str:
        """Run AC simulation, return output text."""
        if ".ac" not in spice.lower():
            spice += "\n.ac dec 20 1 1e6"
        return self.sim.simulate_spice(spice)

    def _extract_gain_db(self, output: str) -> float:
        """Extract DC gain in dB from simulation output."""
        m = re.search(r'DC Gain.*?\|\s*([\d.+-]+)\s*dB', output)
        if m:
            return float(m.group(1))
        # Try linear gain fallback
        m2 = re.search(r'gain=([\d.]+)x', output)
        if m2:
            import math
            return 20 * math.log10(float(m2.group(1)))
        raise ValueError(f"Could not extract gain from output")

    def _extract_cutoff(self, output: str) -> float:
        """Extract -3dB cutoff frequency in Hz."""
        m = re.search(r'-3 dB Cutoff.*?\|\s*([\d.]+)\s*(k?Hz|Hz)?', output)
        if m:
            val = float(m.group(1))
            unit = m.group(2) or "Hz"
            if "k" in unit.lower():
                val *= 1000
            return val
        raise ValueError(f"Could not extract cutoff from output")

    def _sim_tran(self, spice: str) -> str:
        """Run transient simulation."""
        if ".tran" not in spice.lower():
            spice += "\n.tran 1u 200u"
        return self.sim.simulate_spice(spice)


class TestRCFilters(BaseRegressionTest):
    """RC low-pass and high-pass filter regression."""

    def test_rc_lowpass_default(self):
        """Default RC LPF: R=1k, C≈159n → fc≈1kHz."""
        result = self._sim_ac("Vin in 0 AC 1\nR1 in out 1k\nC1 out 0 159.15n")
        fc = self._extract_cutoff(result)
        self.assertAlmostEqual(fc, 1000, delta=50, msg=f"fc={fc}Hz, expected ~1000Hz")

    def test_rc_lowpass_10khz(self):
        """RC LPF fc=10kHz: R=1k, C≈15.9n."""
        result = self._sim_ac("Vin in 0 AC 1\nR1 in out 1k\nC1 out 0 15.9n")
        fc = self._extract_cutoff(result)
        self.assertAlmostEqual(fc, 10000, delta=500, msg=f"fc={fc}Hz, expected ~10000Hz")

    def test_rc_highpass(self):
        """RC HPF: blocks DC, passes high frequencies."""
        result = self._sim_ac("Vin in 0 AC 1\nC1 in out 159.15n\nR1 out 0 1k")
        gain = self._extract_gain_db(result)
        # At DC (1Hz), gain should be very low (< -20dB) for HPF
        self.assertLess(gain, -20, msg=f"DC gain={gain}dB, should be blocked")

    def test_rc_lowpass_rolloff(self):
        """Verify -20dB/decade roll-off."""
        result = self._sim_ac("Vin in 0 AC 1\nR1 in out 1k\nC1 out 0 159.15n\n.ac dec 20 1 100k")
        m = re.search(r'Roll-off.*?\|\s*([\d.+-]+)\s*dB/decade', result)
        if m:
            rolloff = abs(float(m.group(1)))
            # 1st-order filter should have ~20dB/decade roll-off
            self.assertGreater(rolloff, 10, f"Roll-off={rolloff}dB/dec, expected ~20")
            self.assertLess(rolloff, 25, f"Roll-off={rolloff}dB/dec, expected ~20")


class TestOpampAmplifiers(BaseRegressionTest):
    """Opamp-based amplifier regression."""

    def test_inverting_gain_10(self):
        """Inverting amp: R1=1k, Rf=10k → gain=10 (20dB)."""
        result = self._sim_ac(
            "Vin in 0 AC 1\nR1 in n1 1k\nRf n1 out 10k\n"
            "EU1 out 0 0 n1 100000")
        gain_db = self._extract_gain_db(result)
        self.assertAlmostEqual(gain_db, 20, delta=1,
                               msg=f"Gain={gain_db}dB, expected 20dB")

    def test_non_inverting_gain_11(self):
        """Non-inverting amp: R1=1k, Rf=10k → gain=11 (~20.8dB)."""
        result = self._sim_ac(
            "Vin in 0 AC 1\nR1 n1 0 1k\nRf n1 out 10k\n"
            "EU1 out 0 in n1 100000")
        gain_db = self._extract_gain_db(result)
        self.assertAlmostEqual(gain_db, 20.8, delta=1,
                               msg=f"Gain={gain_db}dB, expected ~20.8dB")

    def test_differential_gain_10(self):
        """Differential amp: R1/R2=1k, Rf/Rg=10k → gain=10 (20dB)."""
        result = self._sim_ac(
            "V1 in1 0 AC 1\nV2 in2 0 DC 0\n"
            "R1 in1 n1 1k\nR2 in2 n2 1k\n"
            "Rf n1 out 10k\nRg n2 0 10k\n"
            "EU1 out 0 n2 n1 100000")
        gain_db = self._extract_gain_db(result)
        self.assertAlmostEqual(gain_db, 20, delta=1,
                               msg=f"Gain={gain_db}dB, expected 20dB")


class TestVoltageDivider(BaseRegressionTest):
    """Passive voltage divider regression."""

    def test_divider_half(self):
        """R1=R2=1k → Vout/Vin=0.5 (-6dB)."""
        result = self._sim_ac("Vin in 0 AC 1\nR1 in out 1k\nR2 out 0 1k")
        gain_db = self._extract_gain_db(result)
        self.assertAlmostEqual(gain_db, -6, delta=1,
                               msg=f"Gain={gain_db}dB, expected -6dB")


class TestSimulationValidity(BaseRegressionTest):
    """Cross-cutting simulation validity checks."""

    def test_ac_output_not_empty(self):
        """AC simulation must produce data points."""
        result = self._sim_ac("Vin in 0 AC 1\nR1 in out 1k\nC1 out 0 1u")
        self.assertIn("Data points", result)
        self.assertNotIn("0 data points", result)

    def test_no_spice_errors(self):
        """Valid circuits must not produce SPICE errors."""
        result = self._sim_ac("Vin in 0 AC 1\nR1 in out 1k\nR2 out 0 2k")
        self.assertNotIn("Simulation Failed", result)

    def test_broken_circuit_detected(self):
        """Invalid SPICE (bad component type) should fail."""
        result = self._sim_ac("Vin in 0 AC 1\nZ1 in out 1k")  # Z is not a valid SPICE type
        self.assertIn("Simulation Failed", result)


if __name__ == "__main__":
    unittest.main()
