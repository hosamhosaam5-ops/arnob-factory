import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_character_episode.py"
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


class SourceIntegrityTests(unittest.TestCase):
    def test_exactly_eight_approved_sources(self):
        root = Path(__file__).resolve().parents[1] / "assets" / "audio"
        manifest, chunks, sources = renderer.load_sources(root)
        self.assertEqual(manifest["episodeId"], "episode-pilot-1")
        self.assertEqual(len(chunks), 8)
        self.assertEqual(len(sources), 8)
        self.assertTrue(all(path.suffix == ".wav" for path in sources))


if __name__ == "__main__":
    unittest.main()
