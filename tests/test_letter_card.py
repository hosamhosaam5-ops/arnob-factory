import importlib.util
import contextlib
import io
import os
import sys
import tempfile
import types
from unittest import mock
import json
import subprocess
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


SILMA_SPEC = importlib.util.spec_from_file_location(
    "silma_episode_guard_tests",
    ROOT / "scripts" / "silma_episode.py",
)
assert SILMA_SPEC is not None and SILMA_SPEC.loader is not None
silma_episode = importlib.util.module_from_spec(SILMA_SPEC)
SILMA_SPEC.loader.exec_module(silma_episode)


class SilmaEventGuardTests(unittest.TestCase):
    def invoke_run_real(self, event_name, ref_name):
        fake_numpy = types.SimpleNamespace(__version__="1.26.4")
        fake_soundfile = types.SimpleNamespace()
        fake_torch = types.SimpleNamespace(
            __version__="2.11.0",
            cuda=types.SimpleNamespace(is_available=lambda: False),
            version=types.SimpleNamespace(cuda=None),
            set_num_threads=lambda count: None,
            set_num_interop_threads=lambda count: None,
        )
        fake_torchaudio = types.SimpleNamespace(__version__="2.11.0")
        versions = {
            "silma-tts": "1.0.5",
            "torch": "2.11.0",
            "torchaudio": "2.11.0",
        }
        error_output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            references = Path(directory) / "references"
            environment = {
                "GITHUB_EVENT_NAME": event_name,
                "GITHUB_REF": ref_name,
                "GITHUB_RUN_ATTEMPT": "1",
            }
            with (
                mock.patch.dict(os.environ, environment),
                mock.patch.dict(
                    sys.modules,
                    {
                        "numpy": fake_numpy,
                        "soundfile": fake_soundfile,
                        "torch": fake_torch,
                        "torchaudio": fake_torchaudio,
                    },
                ),
                mock.patch.object(
                    silma_episode.importlib.metadata,
                    "version",
                    side_effect=versions.__getitem__,
                ),
                contextlib.redirect_stderr(error_output),
            ):
                result = silma_episode.run_real(
                    root,
                    references,
                    silma_episode.time.perf_counter(),
                )
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            return result, error_output.getvalue(), manifest

    def test_accepts_push_on_approved_pr_branch(self):
        result, error, manifest = self.invoke_run_real(
            "push",
            "refs/heads/feat/letter-card-silma-20261127",
        )
        self.assertEqual(result, 1)
        self.assertIn("Missing Method A reference manifest", error)
        self.assertNotIn("Run guard rejected event", error)
        self.assertEqual(manifest["runPolicy"]["event"], "push")
        self.assertEqual(
            manifest["runPolicy"]["branch"],
            "refs/heads/feat/letter-card-silma-20261127",
        )

    def test_rejects_push_on_another_branch(self):
        result, error, manifest = self.invoke_run_real(
            "push",
            "refs/heads/feat/not-the-approved-branch",
        )
        self.assertEqual(result, 1)
        self.assertIn(
            "Run guard rejected event: expected workflow_dispatch or push on the approved PR branch",
            error,
        )
        self.assertEqual(manifest["runPolicy"]["event"], "push")
        self.assertEqual(
            manifest["runPolicy"]["branch"],
            "refs/heads/feat/not-the-approved-branch",
        )




if __name__ == "__main__":
    unittest.main()
