"""Golden waveform comparison — compare transient simulation output against reference.

Saves reference waveforms as .csv files in tests/analog/golden/.
On subsequent runs, compares new simulation output against the golden reference
using NRMSE (Normalized Root Mean Square Error) with configurable tolerance.
"""

import json
import math
import os
import re
import tempfile
import unittest
from pathlib import Path

from nano_agent.tools.analog.spice_simulator import SpiceSimulator

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_DIR.mkdir(exist_ok=True)

# NRMSE tolerance — < 5% deviation from golden is acceptable
DEFAULT_TOLERANCE = 0.05


class GoldenWaveformTest(unittest.TestCase):
    """Base class for golden waveform tests.

    Usage:
        class MyTest(GoldenWaveformTest):
            GOLDEN_NAME = "rc_step_response"
            SPICE = "V1 1 0 PULSE(0 5 0 1u 1u 50u 100u)\\nR1 1 2 1k\\nC1 2 0 1u"
            SIGNAL = "v(2)"  # which signal to compare

    First run: saves golden waveform to tests/analog/golden/<name>.csv
    Subsequent runs: compares against golden, asserts NRMSE < tolerance
    """

    GOLDEN_NAME: str = ""
    SPICE: str = ""
    SIGNAL: str = "v(2)"
    TOLERANCE: float = DEFAULT_TOLERANCE
    UPDATE_GOLDEN: bool = False  # set True to regenerate golden files

    @classmethod
    def setUpClass(cls):
        cls.sim = SpiceSimulator(charts_dir=tempfile.mkdtemp())

    def _sim_tran(self, spice: str) -> dict:
        """Run transient simulation, return parsed signals."""
        if ".tran" not in spice.lower():
            spice += "\n.tran 1u 200u"
        result = self.sim.simulate_spice(spice)
        data = {}
        # Parse time + signal values from output
        lines = result.split("\n")
        in_table = False
        headers = []
        for line in lines:
            if "| Time" in line:
                # Parse headers
                headers = [h.strip() for h in line.split("|")[1:] if h.strip()]
                in_table = True
                for h in headers:
                    data[h] = []
                continue
            if in_table and line.strip().startswith("|"):
                parts = [p.strip() for p in line.split("|")[1:]]
                if len(parts) >= len(headers):
                    try:
                        vals = [float(p) for p in parts[:len(headers)]]
                        for h, v in zip(headers, vals):
                            data[h].append(v)
                    except ValueError:
                        continue
            if in_table and "Showing" in line:
                in_table = False
        return data

    def _compute_nrmse(self, actual: list[float], reference: list[float]) -> float:
        """Normalized Root Mean Square Error.

        NRMSE = sqrt(mean((actual - ref)^2)) / (max(ref) - min(ref))
        Returns 0.0 for perfect match.
        """
        n = min(len(actual), len(reference))
        if n == 0:
            return float("inf")
        actual = actual[:n]
        reference = reference[:n]
        ref_range = max(reference) - min(reference)
        if ref_range < 1e-12:
            ref_range = 1.0
        mse = sum((a - r) ** 2 for a, r in zip(actual, reference)) / n
        return math.sqrt(mse) / ref_range

    def _get_golden_path(self) -> Path:
        return GOLDEN_DIR / f"{self.GOLDEN_NAME}.csv"

    def _get_meta_path(self) -> Path:
        return GOLDEN_DIR / f"{self.GOLDEN_NAME}.json"

    def _save_golden(self, signal_data: list[float]):
        """Save golden waveform to CSV + metadata."""
        path = self._get_golden_path()
        with open(path, "w") as f:
            f.write("value\n")
            for v in signal_data:
                f.write(f"{v:.8e}\n")

        meta = {"name": self.GOLDEN_NAME, "spice": self.SPICE,
                "signal": self.SIGNAL, "points": len(signal_data),
                "min": min(signal_data), "max": max(signal_data)}
        with open(self._get_meta_path(), "w") as f:
            json.dump(meta, f, indent=2)

    def _load_golden(self) -> list[float]:
        """Load golden waveform from CSV."""
        path = self._get_golden_path()
        if not path.exists():
            return []
        data = []
        with open(path) as f:
            next(f)  # skip header
            for line in f:
                line = line.strip()
                if line:
                    data.append(float(line))
        return data

    def test_golden_waveform(self):
        """Compare current simulation against golden reference."""
        if not self.GOLDEN_NAME or not self.SPICE:
            self.skipTest("No GOLDEN_NAME/SPICE defined")

        data = self._sim_tran(self.SPICE)
        signal_key = self.SIGNAL
        # Find matching key (case-insensitive, partial match)
        found_key = None
        for k in data:
            if signal_key in k or signal_key.lower() in k.lower():
                found_key = k
                break
        if not found_key and data:
            found_key = list(data.keys())[-1]  # last signal is usually output

        self.assertIsNotNone(found_key, f"Signal '{signal_key}' not found in simulation output. Available: {list(data.keys())}")
        actual = data[found_key]

        golden = self._load_golden()
        if not golden or self.UPDATE_GOLDEN:
            self._save_golden(actual)
            if self.UPDATE_GOLDEN:
                return  # don't compare when updating
            self.skipTest(f"Golden waveform '{self.GOLDEN_NAME}' not found — created now. Re-run to compare.")

        nrmse = self._compute_nrmse(actual, golden)
        self.assertLess(nrmse, self.TOLERANCE,
                        f"NRMSE={nrmse:.4f} exceeds tolerance {self.TOLERANCE}. "
                        f"Waveform has diverged from golden reference.\n"
                        f"To update: set UPDATE_GOLDEN=True in test class.")


# ── Concrete golden waveform tests ──

class TestRCStepResponse(GoldenWaveformTest):
    """RC low-pass step response golden test."""
    GOLDEN_NAME = "rc_step_response"
    SPICE = "V1 1 0 PULSE(0 5 0 1u 1u 50u 100u)\nR1 1 2 1k\nC1 2 0 1u"
    SIGNAL = "v(2)"


class TestVoltageDividerStep(GoldenWaveformTest):
    """Voltage divider step response."""
    GOLDEN_NAME = "voltage_divider_step"
    SPICE = "V1 1 0 PULSE(0 5 0 1u 1u 50u 100u)\nR1 1 2 1k\nR2 2 0 1k"
    SIGNAL = "v(2)"


class TestInvertingAmpPulse(GoldenWaveformTest):
    """Inverting amplifier pulse response."""
    GOLDEN_NAME = "inverting_amp_pulse"
    SPICE = ("V1 1 0 PULSE(0 0.1 0 1u 1u 50u 100u)\n"
             "R1 1 n1 1k\nRf n1 out 10k\nEU1 out 0 0 n1 100000")
    SIGNAL = "v(out)"


if __name__ == "__main__":
    unittest.main()
