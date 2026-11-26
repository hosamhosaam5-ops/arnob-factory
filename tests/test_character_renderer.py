import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_character_episode.py"
SPEC = importlib.util.spec_from_file_location("character_renderer", SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(renderer)


class MouthEnvelopeTests(unittest.TestCase):
    def test_four_audio_driven_shapes(self):
        self.assertEqual(renderer.mouth_shape(0.0), "closed")
        self.assertEqual(renderer.mouth_shape(0.2), "half")
        self.assertEqual(renderer.mouth_shape(0.5), "open")
        self.assertEqual(renderer.mouth_shape(0.9), "round")

    def test_boundaries_are_stable(self):
        self.assertEqual(renderer.mouth_shape(0.1599), "closed")
        self.assertEqual(renderer.mouth_shape(0.16), "half")
        self.assertEqual(renderer.mouth_shape(0.39), "open")
        self.assertEqual(renderer.mouth_shape(0.72), "round")


class CharacterAndStoryTests(unittest.TestCase):
    def test_non_speaking_character_has_closed_mouth(self):
        cat = renderer.cat_svg(0, 0, 0.8, 1.0, False, 0, 0)
        mouse = renderer.mouse_svg(0, 0, 0.8, 1.0, False, 0, 0)
        self.assertIn('M-13,-59 Q0,-50 13,-59', cat)
        self.assertIn('M-13,-13 Q0,-4 13,-13', mouse)

    def test_cat_has_distinct_expressions_and_warm_palette(self):
        surprised = renderer.cat_svg(0, 0, 0.7, 2.0, True, 0, 0, "surprise")
        clever = renderer.cat_svg(0, 0, 0.7, 2.0, True, 0, 0, "clever")
        self.assertIn("M-51,-151 Q-29,-181 -7,-153", surprised)
        self.assertIn("M-51,-151 Q-30,-165 -8,-145", clever)
        self.assertIn("#D99049", surprised)
        self.assertIn("#F4DDBA", surprised)
        self.assertIn("#F4DDBA", surprised)
        self.assertNotIn("#90BBC1", surprised)
        self.assertIn("M-109,-140", surprised)

    def test_mouse_is_smaller_and_has_original_scarf(self):
        mouse = renderer.mouse_svg(0, 0, 0.7, 2.0, True, 0, 0)
        self.assertIn("#4C9A91", mouse)
        self.assertIn("M44,-23", mouse)
        self.assertIn("M-43,-2", mouse)

    def test_story_actions_appear_in_the_correct_segments(self):
        digging = renderer.story_action_svg(2, 0.5, 7.0, 14.0)
        planting = renderer.story_action_svg(3, 0.5, 8.0, 25.0)
        watering = renderer.story_action_svg(4, 0.7, 7.0, 35.0)
        growth = renderer.story_action_svg(7, 0.9, 7.0, 58.0)
        self.assertIn("story-seed", planting)
        self.assertIn("story-dig-spoon", digging)
        self.assertIn("story-watering-vessel", watering)
        self.assertIn("story-growing-sprout", growth)
        self.assertNotIn("story-growing-sprout", renderer.story_action_svg(0, 0.5, 8.0, 4.0))

    def test_arabic_caption_baselines_stay_inside_panel(self):
        self.assertEqual(renderer.caption_baselines(3), [587.0, 621.0, 655.0])
        self.assertEqual(renderer.caption_baselines(1), [621.0])
        with self.assertRaises(ValueError):
            renderer.caption_baselines(4)

    def test_pause_reduced_without_changing_qa_limit(self):
        self.assertAlmostEqual(renderer.PAUSE_SECONDS, 0.20)
        qa = (ROOT / "src" / "production" / "video-qa.js").read_text(encoding="utf-8")
        self.assertIn("maxSilenceSegmentSeconds: 0.5", qa)
        mixer = (ROOT / "src" / "production" / "audio-mixer.js").read_text(encoding="utf-8")
        self.assertIn("maxSilenceSegmentSeconds: 0.5", mixer)


class ParrotAssetTests(unittest.TestCase):
    def test_parrot_is_standalone_and_not_in_the_episode(self):
        path = ROOT / "assets" / "characters" / "parrot-ready.svg"
        content = path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("<svg"))
        self.assertIn("#398B55", content)
        self.assertIn("#E5A94F", content)
        self.assertNotIn("parrot-ready.svg", renderer.svg_frame(
            {"speakerId": "pistachio", "text": "اختبار"}, 0, 0.0, 0.0
        ))


class SourceIntegrityTests(unittest.TestCase):
    def test_exactly_eight_approved_sources(self):
        root = ROOT / "assets" / "audio"
        manifest, chunks, sources = renderer.load_sources(root)
        self.assertEqual(manifest["episodeId"], "episode-pilot-1")
        self.assertEqual(len(chunks), 8)
        self.assertEqual(len(sources), 8)
        self.assertTrue(all(path.suffix == ".wav" for path in sources))


if __name__ == "__main__":
    unittest.main()
