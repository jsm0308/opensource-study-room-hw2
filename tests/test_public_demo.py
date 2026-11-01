import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from offline_bridge import OfflineBridge
from server import application


class PublicDemoTests(unittest.TestCase):
    def test_seed_contains_only_the_open_source_course(self):
        seed = json.loads((ROOT / "app" / "seed.json").read_text(encoding="utf-8"))
        self.assertEqual([course["id"] for course in seed["courses"]], ["opensource-sw-2026-2"])
        self.assertEqual(len(seed["courses"][0]["weeks"]), 8)
        self.assertEqual(len(seed["cards"]), 8)
        self.assertTrue(all(card["courseId"] == "opensource-sw-2026-2" for card in seed["cards"]))

    def test_public_source_is_a_derivative_note(self):
        seed = json.loads((ROOT / "app" / "seed.json").read_text(encoding="utf-8"))
        self.assertTrue(all(card["source"]["path"] == "app/course-notes.md" for card in seed["cards"]))
        self.assertFalse(any(ROOT.rglob("*.pdf")))

    def test_offline_bridge_creates_two_questions_per_card(self):
        bridge = OfflineBridge(ROOT)
        card = json.loads((ROOT / "app" / "seed.json").read_text(encoding="utf-8"))["cards"][0]
        schema = {"properties": {"questions": {}}}
        payload = {"cards": [card], "count": 2}
        result = bridge.ask("make questions\n" + json.dumps(payload, ensure_ascii=False), schema)
        self.assertEqual(len(result["questions"]), 2)
        self.assertEqual({q["cardId"] for q in result["questions"]}, {card["id"]})

    def test_application_bootstraps_course_without_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            store, bridge, service = application(directory, demo_mode=True)
            try:
                data = store.bootstrap()
                self.assertEqual(len(data["courses"]), 1)
                self.assertEqual(len(data["cards"]), 8)
                self.assertEqual(bridge.status["transport"], "offline")
                self.assertEqual(service.workspace, Path(directory).resolve())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
