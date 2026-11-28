import importlib.util
import math
import re
import shutil
import struct
import subprocess
import tempfile
import wave
import xml.etree.ElementTree as ET
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
    def test_preview_selection_includes_seed_digging_and_comparison_scene(self):
        self.assertEqual(set(renderer.SELECTED_PREVIEW_SCENE_IDS), {0, 2, 3, 7})
        self.assertIn(2, renderer.SELECTED_PREVIEW_SCENE_IDS)
        self.assertIn(3, renderer.SELECTED_PREVIEW_SCENE_IDS)

    def test_three_second_smoke_window_uses_only_first_scene(self):
        scenes = [
            {"index": 1, "startSeconds": 0.0, "durationSeconds": 6.0, "endSeconds": 6.0},
            {"index": 2, "startSeconds": 6.2, "durationSeconds": 5.0, "endSeconds": 11.2},
        ]
        clipped = renderer.smoke_scene_window(scenes, 3.0)
        self.assertEqual(len(clipped), 1)
        self.assertEqual(clipped[0]["index"], 1)
        self.assertEqual(clipped[0]["durationSeconds"], 3.0)
        self.assertEqual(clipped[0]["endSeconds"], 3.0)
        with self.assertRaises(ValueError):
            renderer.smoke_scene_window(scenes, 2.0)
        with self.assertRaises(RuntimeError):
            renderer.smoke_scene_window([{"startSeconds": 0.0, "durationSeconds": 2.5}], 3.0)

    def test_all_scene_frames_are_well_formed_svg_xml(self):
        for scene_index in range(8):
            scene = {
                "speakerId": "pistachio" if scene_index % 2 == 0 else "hazelnut",
                "text": "اِخْتِبَارُ مَشْهَدٍ",
            }
            svg = renderer.svg_frame(
                scene,
                scene_index,
                0.0,
                0.0,
                include_text=False,
                local_time=0.0,
                scene_duration=1.0,
            )
            with self.subTest(scene_index=scene_index):
                ET.fromstring(svg)

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


    def test_character_render_trim_filter_is_edge_only(self):
        self.assertNotIn("stop_periods=-1", renderer.TRIM_FILTER)
        self.assertNotIn("stop_duration=", renderer.TRIM_FILTER)
        self.assertEqual(renderer.TRIM_FILTER.count("start_periods=1"), 2)

    def test_character_render_preserves_internal_silence(self):
        self.assertIsNotNone(shutil.which("ffmpeg"), "ffmpeg is required for the real filter test")
        sample_rate = 24000

        def tone(seconds: float, frequency: float) -> bytes:
            samples = [
                int(9000 * math.sin(2 * math.pi * frequency * index / sample_rate))
                for index in range(round(sample_rate * seconds))
            ]
            return b"".join(struct.pack("<h", value) for value in samples)

        def silence(seconds: float) -> bytes:
            return bytes(round(sample_rate * seconds) * 2)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            trimmed = Path(directory) / "edge-only.wav"
            with wave.open(str(source), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(
                    silence(0.40)
                    + tone(0.40, 440)
                    + silence(0.32)
                    + tone(0.40, 660)
                    + silence(0.40)
                )
            rendered = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                    "-i", str(source), "-af", renderer.TRIM_FILTER,
                    "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le", str(trimmed),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            detected = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-i", str(trimmed),
                    "-af", "silencedetect=noise=-50dB:d=0.02", "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(detected.returncode, 0, detected.stderr)
            durations = [
                float(value)
                for value in re.findall(
                    r"silence_duration:\s*([0-9]+(?:\.[0-9]+)?)",
                    detected.stderr,
                )
            ]
            self.assertTrue(any(0.27 <= value <= 0.37 for value in durations), detected.stderr)

    def test_pause_reduced_without_changing_qa_limit(self):
        self.assertAlmostEqual(renderer.PAUSE_SECONDS, 0.20)
        check = subprocess.run(
            [
                "node",
                "--input-type=module",
                "--eval",
                (
                    "import { DEFAULT_LIMITS } from './src/production/video-qa.js'; "
                    "if (DEFAULT_LIMITS.maxSilenceSegmentSeconds !== 0.5) "
                    "throw new Error('maxSilenceSegmentSeconds must remain 0.5');"
                ),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(check.returncode, 0, check.stderr)


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
