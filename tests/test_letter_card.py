import importlib.util
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "render_letter_card",
    ROOT / "scripts" / "render_letter_card.py",
)
assert SPEC is not None and SPEC.loader is not None
card = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(card)


class LetterCardTests(unittest.TestCase):
    def test_svg_is_well_formed_and_has_alif_and_four_original_drawings(self):
        svg = card.card_svg("pistachio", 0.4, 0.25)
        ET.fromstring(svg)
        for token in (
            "letter-rabbit",
            "letter-lion",
            "letter-ear",
            "letter-needle",
            "حَرْفُ الْأَلِفِ",
            "url(#alif-fill)",
        ):
            self.assertIn(token, svg)

    def test_vowelled_words_are_inside_card_source(self):
        svg = card.card_svg("hazelnut", 1.0, 0.0, 0)
        for word in ("أَرْنَبٌ", "أَسَدٌ", "أُذُنٌ", "إِبْرَةٌ"):
            self.assertIn(word, svg)

    def test_character_who_is_listening_has_closed_mouth(self):
        cat = card.engine.cat_svg(0, 0, 0.9, 1.0, False, 0, 0)
        mouse = card.engine.mouse_svg(0, 0, 0.9, 1.0, False, 0, 0)
        self.assertIn("M-13,-59 Q0,-50 13,-59", cat)
        self.assertIn("M-13,-13 Q0,-4 13,-13", mouse)

    def test_smoke_uses_real_successful_manifest_wav_fingerprint(self):
        audio_root = ROOT / "assets" / "audio"
        manifest_path = audio_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = audio_root / manifest["completedChunks"][0]["wav"]["path"]
        verified = card.verify_smoke_source(manifest_path, source)
        self.assertEqual(verified["sha256"], manifest["completedChunks"][0]["wav"]["sha256"])
        self.assertEqual(verified["chunkId"], manifest["completedChunks"][0]["chunkId"])

    def test_video_qa_silence_limit_is_not_relaxed(self):
        from src.production.video_qa import DEFAULT_LIMITS

        self.assertEqual(DEFAULT_LIMITS.maxSilenceSegmentSeconds, 0.5)


if __name__ == "__main__":
    unittest.main()
