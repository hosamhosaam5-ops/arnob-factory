from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


silma = load_module("silma_episode", ROOT / "scripts" / "silma_episode.py")
merge = load_module(
    "replace_silma_chunk",
    ROOT / "scripts" / "replace_silma_chunk.py",
)
evaluator = load_module(
    "evaluate_silma_seed",
    ROOT / "scripts" / "evaluate_silma_seed.py",
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class SilmaSeedTests(unittest.TestCase):
    def test_selects_only_requested_chunk_without_mutating_source_list(self):
        chunks = [
            {"chunkId": "chunk-01", "speakerId": "pistachio"},
            {"chunkId": "chunk-03", "speakerId": "pistachio"},
            {"chunkId": "chunk-04", "speakerId": "hazelnut"},
        ]
        self.assertEqual(silma.select_chunks(chunks, "chunk-03"), [chunks[1]])
        self.assertEqual(len(chunks), 3)
        self.assertEqual(silma.select_chunks(chunks, None), chunks)

    def test_unknown_or_duplicate_chunk_id_is_rejected(self):
        with self.assertRaises(ValueError):
            silma.select_chunks([{"chunkId": "chunk-01"}], "chunk-03")
        with self.assertRaises(ValueError):
            silma.select_chunks(
                [{"chunkId": "chunk-03"}, {"chunkId": "chunk-03"}],
                "chunk-03",
            )

    def test_explicit_seed_overrides_default_and_default_is_unchanged(self):
        self.assertEqual(silma.resolve_seed(3, None), 4245)
        self.assertEqual(silma.resolve_seed(3, 50123), 50123)

    def test_invalid_seed_is_rejected(self):
        for seed in (-1, 2**31):
            with self.assertRaises(ValueError):
                silma.resolve_seed(3, seed)

    def test_push_requires_matching_one_chunk_run_request(self):
        import os

        old_event = os.environ.get("GITHUB_EVENT_NAME")
        old_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
        old_request = os.environ.get("ARNOB_RUN_REQUEST_PATH")
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "episodeId": "letter-alif",
                        "purpose": "seed-trials",
                        "chunkId": "chunk-03",
                        "seeds": [50001, 50002],
                    }
                ),
                encoding="utf-8",
            )
            os.environ["GITHUB_EVENT_NAME"] = "push"
            os.environ["GITHUB_RUN_ATTEMPT"] = "1"
            os.environ["ARNOB_RUN_REQUEST_PATH"] = str(request_path)
            try:
                result = silma.authorize_run_event("push", "1", "chunk-03", 50001)
                self.assertEqual(result["mode"], "seed-trials")
                with self.assertRaises(RuntimeError):
                    silma.authorize_run_event("push", "1", "chunk-02", 50001)
                with self.assertRaises(RuntimeError):
                    silma.authorize_run_event("push", "1", "chunk-03", 50003)
                with self.assertRaises(RuntimeError):
                    silma.authorize_run_event("push", "2", "chunk-03", 50001)
            finally:
                for key, value in (
                    ("GITHUB_EVENT_NAME", old_event),
                    ("GITHUB_RUN_ATTEMPT", old_attempt),
                    ("ARNOB_RUN_REQUEST_PATH", old_request),
                ):
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_internal_silence_parser_excludes_clip_edges(self):
        log = """
        silence_start: 0
        silence_end: 0.080 | silence_duration: 0.080
        silence_start: 0.478 | silence_end: 0.900 | silence_duration: 0.422
        silence_start: 2.510
        """
        intervals = evaluator.parse_internal_silences(log, 2.552)
        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(intervals[0]["durationSeconds"], 0.422)

    def test_single_chunk_merge_updates_only_target_and_preserves_other_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_dir = root / "base"
            candidate_dir = root / "candidate"
            (base_dir / "chunks").mkdir(parents=True)
            (candidate_dir / "chunks").mkdir(parents=True)
            base_items = []
            for index in range(1, 9):
                speaker = "pistachio" if index % 2 else "hazelnut"
                stem = f"{index:02d}-{speaker}"
                wav_bytes = f"base-wav-{index}".encode()
                mp3_bytes = f"base-mp3-{index}".encode()
                (base_dir / "chunks" / f"{stem}.wav").write_bytes(wav_bytes)
                (base_dir / "chunks" / f"{stem}.mp3").write_bytes(mp3_bytes)
                persona = "pistachio" if speaker == "pistachio" else "hazelnut"
                base_items.append(
                    {
                        "chunkId": f"chunk-{index:02d}",
                        "speakerId": speaker,
                        "speakerArabic": "فستق" if speaker == "pistachio" else "بندق",
                        "text": f"text-{index}",
                        "wordCount": 1,
                        "settings": {
                            "method": "A",
                            "voice": "ar+f3" if speaker == "pistachio" else "ar+m3",
                            "referenceSpeed": 160 if speaker == "pistachio" else 140,
                            "referencePitch": 68 if speaker == "pistachio" else 43,
                            "synthesisSpeed": 1.0,
                            "nfeStep": 16,
                        },
                        "reference": {
                            "path": f"{persona}-reference.wav",
                            "sha256": f"reference-{persona}",
                        },
                        "timing": {
                            "cpuSeconds": 1.0,
                            "wallSeconds": 1.0,
                            "audioDurationSeconds": 2.5,
                            "rtf": 0.4,
                            "cpuRtf": 0.4,
                            "encodeWallSeconds": 0.01,
                        },
                        "wav": {
                            "path": f"chunks/{stem}.wav",
                            "sha256": digest(wav_bytes),
                            "peakAmplitude": 0.8,
                            "rmsAmplitude": 0.1,
                        },
                        "mp3": {
                            "path": f"chunks/{stem}.mp3",
                            "sha256": digest(mp3_bytes),
                        },
                    }
                )
            base_manifest = {
                "episodeId": "letter-alif",
                "status": "complete",
                "chunksPlanned": 8,
                "episodeScriptSha256": "approved-script-sha",
                "completedChunks": base_items,
                "episode": {"wav": {"sha256": "old-episode"}},
            }
            (base_dir / "manifest.json").write_text(
                json.dumps(base_manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            new_wav = b"new-wav-chunk-03"
            new_mp3 = b"new-mp3-chunk-03"
            (candidate_dir / "chunks" / "03-pistachio.wav").write_bytes(new_wav)
            (candidate_dir / "chunks" / "03-pistachio.mp3").write_bytes(new_mp3)
            candidate_item = json.loads(json.dumps(base_items[2]))
            candidate_item["settings"]["seed"] = 50123
            candidate_item["wav"]["sha256"] = digest(new_wav)
            candidate_item["mp3"]["sha256"] = digest(new_mp3)
            candidate_manifest = {
                "episodeId": "letter-alif",
                "status": "complete",
                "chunksPlanned": 1,
                "episodeScriptSha256": "approved-script-sha",
                "completedChunks": [candidate_item],
            }
            (candidate_dir / "manifest.json").write_text(
                json.dumps(candidate_manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            with unittest.mock.patch.object(
                merge,
                "make_episode_mix",
                return_value={
                    "wav": {"sha256": "rebuilt-episode-wav"},
                    "mp3": {"sha256": "rebuilt-episode-mp3"},
                },
            ):
                result = merge.replace_chunk(base_dir, candidate_dir, "chunk-03")
            updated = json.loads((base_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result["preservedChunkCount"], 7)
            self.assertEqual(result["preservedMediaCount"], 14)
            self.assertEqual(updated["replacementHistory"][0]["seed"], 50123)
            self.assertEqual(updated["completedChunks"][2]["settings"]["seed"], 50123)
            for index in range(8):
                if index == 2:
                    self.assertEqual(updated["completedChunks"][index]["wav"]["sha256"], digest(new_wav))
                    self.assertEqual(updated["completedChunks"][index]["mp3"]["sha256"], digest(new_mp3))
                    continue
                self.assertEqual(updated["completedChunks"][index], base_items[index])
                stem = f"{index + 1:02d}-{'pistachio' if index % 2 == 0 else 'hazelnut'}"
                self.assertEqual(
                    digest((base_dir / "chunks" / f"{stem}.wav").read_bytes()),
                    base_items[index]["wav"]["sha256"],
                )
                self.assertEqual(
                    digest((base_dir / "chunks" / f"{stem}.mp3").read_bytes()),
                    base_items[index]["mp3"]["sha256"],
                )


if __name__ == "__main__":
    unittest.main()
