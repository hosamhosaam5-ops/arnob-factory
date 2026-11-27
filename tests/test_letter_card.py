import importlib.util
import math
import re
import shutil
import struct
import json
import subprocess
import tempfile
import wave
from unittest import mock
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

    def test_full_render_consumes_actual_rms_envelope_list_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "generation"
            chunks_dir = root / "chunks"
            output = Path(directory) / "render"
            chunks_dir.mkdir(parents=True)
            chunks = []
            details = []
            for index in range(8):
                speaker = "pistachio" if index % 2 == 0 else "hazelnut"
                wav_path = chunks_dir / f"{index + 1:02d}-{speaker}.wav"
                with wave.open(str(wav_path), "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(24000)
                    wav_file.writeframes(b"\x10\x00" * 6000)
                item = {
                    "chunkId": f"chunk-{index + 1:02d}",
                    "speakerId": speaker,
                    "text": "أَلِفٌ، اِخْتِبَارٌ.",
                    "wav": {
                        "path": f"chunks/{wav_path.name}",
                        "durationSeconds": 0.25,
                        "sha256": card.sha(wav_path),
                    },
                    "mp3": {"sha256": f"{index + 1:064x}"},
                }
                chunks.append(item)
                details.append({
                    "item": item,
                    "wav": wav_path,
                    "sourceWav": wav_path,
                    "trim": {
                        "sourceDurationSeconds": 0.25,
                        "trimmedDurationSeconds": 0.20,
                        "removedDurationSeconds": 0.05,
                        "trimmedSha256": card.sha(wav_path),
                    },
                })

            manifest = {
                "status": "complete",
                "dryRun": False,
                "modelInferenceInvoked": True,
                "episodeId": "letter-alif",
                "method": "A",
                "nfeStep": 16,
                "completedChunks": chunks,
                "episodeScriptSha256": "fixture-script-sha",
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            voice_path = output / "episode-audio.wav"

            def fake_make_voice(_root, out, _chunks):
                voice = out / voice_path.name
                voice.write_bytes(b"voice-fixture")
                return voice, details

            def fake_raster(_svg, path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"png-fixture")

            def fake_run(command, timeout=180):
                Path(command[-1]).write_bytes(b"mp4-fixture")

            real_rms_envelope = card.engine.rms_envelope
            observed_envelopes = []

            def observe_rms_envelope(path):
                result = real_rms_envelope(path)
                observed_envelopes.append(result)
                return result

            with (
                mock.patch.object(card, "make_voice", side_effect=fake_make_voice),
                mock.patch.object(card, "raster", side_effect=fake_raster),
                mock.patch.object(card, "draw_caption"),
                mock.patch.object(card, "run", side_effect=fake_run),
                mock.patch.object(card, "duration", return_value=1.0),
                mock.patch.object(
                    card.engine,
                    "rms_envelope",
                    side_effect=observe_rms_envelope,
                ),
            ):
                report = card.full_render(root, output)

            self.assertEqual(report["status"], "rendered-awaiting-qa")
            self.assertEqual(len(observed_envelopes), 8)
            self.assertEqual(report["trimmedChunks"][0]["trimmedDurationSeconds"], 0.20)
            self.assertEqual(report["audioTimeline"][1]["startSeconds"], 0.40)
            self.assertTrue(all(isinstance(values, list) for values, _scale in observed_envelopes))
            self.assertTrue(all(values for values, _scale in observed_envelopes))
            self.assertTrue((output / "render-report.json").is_file())

    def test_edge_trim_preserves_80ms_margins_and_inner_silence(self):
        self.assertIsNotNone(shutil.which("ffmpeg"), "ffmpeg is required for the real filter test")
        sample_rate = 24000

        def silence(seconds):
            return [0] * round(sample_rate * seconds)

        def tone(seconds, frequency):
            return [
                int(8000 * math.sin(2 * math.pi * frequency * index / sample_rate))
                for index in range(round(sample_rate * seconds))
            ]

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "edge-and-inner-silence.wav"
            trimmed = Path(directory) / "trimmed.wav"
            samples = (
                silence(0.45)
                + tone(0.40, 440)
                + silence(0.30)
                + tone(0.40, 660)
                + silence(0.45)
            )
            with wave.open(str(source), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(b"".join(struct.pack("<h", value) for value in samples))

            trim = card.trim_silence_edges(source, trimmed)
            detected = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-nostats", "-i", str(trimmed),
                    "-af", "silencedetect=noise=-50dB:d=0.02",
                    "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(detected.returncode, 0, detected.stderr)
            silence_durations = [
                float(value)
                for value in re.findall(
                    r"silence_duration:\s*([0-9]+(?:\.[0-9]+)?)",
                    detected.stderr,
                )
            ]
            self.assertEqual(len(silence_durations), 3, detected.stderr)
            self.assertGreaterEqual(silence_durations[0], 0.07)
            self.assertLessEqual(silence_durations[0], 0.09)
            self.assertGreaterEqual(silence_durations[1], 0.27)
            self.assertLessEqual(silence_durations[1], 0.33)
            self.assertGreaterEqual(silence_durations[2], 0.07)
            self.assertLessEqual(silence_durations[2], 0.09)
            self.assertAlmostEqual(trim["trimmedDurationSeconds"], 1.26, delta=0.04)

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


if __name__ == "__main__":
    unittest.main()
