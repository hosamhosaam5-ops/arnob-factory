from __future__ import annotations

import importlib.util
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compare_silma_calibration", ROOT / "scripts" / "compare_silma_calibration.py")
assert spec is not None and spec.loader is not None
compare_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_module)


class CalibrationCompareTests(unittest.TestCase):
    def _write(self, path: Path, amplitude: int) -> None:
        rate = 24000
        samples = [0] * 1200 + [
            int(amplitude * math.sin(2 * math.pi * 440 * i / rate))
            for i in range(12000)
        ] + [0] * 1200
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(b"".join(struct.pack("<h", value) for value in samples))

    def test_equivalent_duration_and_rms_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.wav"
            candidate = Path(directory) / "candidate.wav"
            self._write(original, 7000)
            self._write(candidate, 7000)
            result = compare_module.compare(original, candidate)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["durationDeltaSeconds"], 0.0)
            self.assertEqual(result["rmsDeltaDb"], 0.0)


if __name__ == "__main__":
    unittest.main()
