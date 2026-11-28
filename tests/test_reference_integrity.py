from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "assets" / "references"

EXPECTED = {
    "pistachio-reference.wav": {
        "pcm": "724b5f6020aef1f87d0942d15cb2f6aa78a70fbbfbf86d2800cd2b99972a49cb",
        "samples": 115637,
    },
    "hazelnut-reference.wav": {
        "pcm": "225479707276b3947c1e453da4074798b968183a2a969ceae0869ca69e14e9da",
        "samples": 134371,
    },
}

spec = importlib.util.spec_from_file_location(
    "silma_episode_reference_tests",
    ROOT / "scripts" / "silma_episode.py",
)
assert spec is not None and spec.loader is not None
silma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(silma)


class CommittedReferencePcmTests(unittest.TestCase):
    def test_each_committed_reference_matches_approved_pcm_digest(self):
        for name, expected in EXPECTED.items():
            with self.subTest(reference=name):
                path = REFERENCE_DIR / name
                self.assertTrue(path.is_file())
                actual, channels, rate, width, samples = silma.pcm_sha256(path)
                self.assertEqual((channels, rate, width), (1, 24000, 2))
                self.assertEqual(samples, expected["samples"])
                self.assertEqual(actual, expected["pcm"])

    def test_a_changed_pcm_frame_changes_the_reference_fingerprint(self):
        source = REFERENCE_DIR / "pistachio-reference.wav"
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / source.name
            with wave.open(str(source), "rb") as wav:
                params = wav.getparams()
                pcm = bytearray(wav.readframes(wav.getnframes()))
            pcm[0] ^= 0x01
            with wave.open(str(changed), "wb") as wav:
                wav.setparams(params)
                wav.writeframes(bytes(pcm))

            actual, channels, rate, width, samples = silma.pcm_sha256(changed)
            self.assertEqual((channels, rate, width, samples), (1, 24000, 2, 115637))
            self.assertNotEqual(actual, EXPECTED["pistachio-reference.wav"]["pcm"])

    def test_pcm_fingerprint_is_independent_of_container_file_digest(self):
        path = REFERENCE_DIR / "pistachio-reference.wav"
        pcm, *_ = silma.pcm_sha256(path)
        container = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(pcm, EXPECTED["pistachio-reference.wav"]["pcm"])
        self.assertNotEqual(container, pcm)


if __name__ == "__main__":
    unittest.main()
