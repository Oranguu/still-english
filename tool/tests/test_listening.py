import copy
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import listening
import packages
import reviews


class ListeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        root_patch = patch.object(packages, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
        clock = patch.object(listening, "_now", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.state = self.root / "isolated-state"
        self.folder = self.root / "source" / "lesson"
        self.folder.mkdir(parents=True)
        (self.folder / "video.mp4").write_bytes(b"synthetic video bytes")
        self.data = {"schema_version": 1, "id": "lesson-id", "title": "An invented lesson", "video": "video.mp4",
                     "segments": [{"id": "s1", "start": 1, "end": 3, "en": "I can jump.", "zh": "我会跳。",
                                   "analysis": {"meaning": "说自己会跳。", "vocabulary": [{"term": "can", "meaning": "能够", "example": "I can run. 我会跑。"}]}}]}
        self.write_package()

    def write_package(self):
        packages.atomic_json(self.folder / "manifest.json", self.data)

    def favorite(self, active=True):
        reviews.save_progress(self.folder.name, {"favorites": ["s1"] if active else []}, self.state)

    def excerpt(self, field="analysis.vocabulary.0.example"):
        text = reviews._field_context(self.data["segments"][0], field)
        return reviews.add_review({"folder": self.folder.name, "segment_id": "s1", "selections": [{"field": field, "start": 0, "end": len(text), "quote": text}]})["item"]

    def mark(self, reason="sound"):
        return listening.mark_difficulty({"folder": self.folder.name, "segment_id": "s1", "reason": reason}, self.state)["item"]

    def items(self):
        return listening.listening_state(self.state)["items"]

    def grade(self, item_id, result="understood", assisted=False, attempt_id=None):
        return listening.grade_attempt({"id": item_id, "result": result, "assisted": assisted, "attempt_id": attempt_id or str(uuid.uuid4())}, self.state)

    def test_existing_excerpts_and_favorite_share_original_sentence_without_rewriting(self):
        self.excerpt()
        self.excerpt("analysis.meaning")
        self.favorite()
        before = {path.name: path.read_bytes() for path in (self.root / "review" / "items").glob("*.json")}
        state = listening.listening_state(self.state)
        self.assertEqual(state["now"], "2026-01-02T12:00:00Z")
        self.assertEqual(len(state["items"]), 1)
        item = state["items"][0]
        self.assertEqual(item["source"]["en"], "I can jump.")
        self.assertNotEqual(item["source"]["en"], "I can run.")
        self.assertEqual((item["listening_count"], item["difficulty"], item["due_at"]), (0, None, None))
        self.assertEqual(item["audio"], {"available": True, "folder": "lesson", "video": "video.mp4", "start": 1, "end": 3})
        self.assertEqual(item["meaning"], "说自己会跳。")
        self.assertEqual(before, {path.name: path.read_bytes() for path in (self.root / "review" / "items").glob("*.json")})
        self.assertFalse((self.root / "review" / "listening").exists())

    def test_get_backfills_old_favorites_using_explicit_state_only(self):
        packages.atomic_json(reviews.progress_path("lesson", self.state), {"favorites": ["s1"]})
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(len(list((self.root / "review" / "items").glob("*.json"))), 1)
        self.assertFalse((self.root / "review" / "listening").exists())
        self.assertFalse((self.root / "tool").exists())

    def test_mark_without_favorite_clear_hide_and_restore_history(self):
        item = self.mark()
        self.assertEqual((item["difficulty"], item["due_at"], item["active"]), ("sound", "2026-01-02T12:00:00Z", True))
        graded = self.grade(item["id"])["item"]
        cleared = self.mark(None)
        self.assertFalse(cleared["active"])
        self.assertEqual(cleared["due_at"], graded["due_at"])
        self.assertEqual(self.items(), [])
        self.assertEqual(json.loads(listening._path(item["id"]).read_text())["listening_count"], 1)
        self.now += timedelta(days=1)
        restored = self.mark("speed")
        self.assertEqual(restored["id"], item["id"])
        self.assertEqual((restored["listening_count"], restored["independent_streak"], restored["last_result"]), (1, 0, "understood"))
        self.assertEqual(restored["due_at"], "2026-01-03T12:00:00Z")

    def test_clearing_difficulty_retains_active_review_and_explicit_marker_retains_archived_favorite(self):
        self.favorite()
        item = self.mark()
        self.assertTrue(self.mark(None)["active"])
        self.mark("meaning")
        self.favorite(False)
        self.assertEqual([row["id"] for row in self.items()], [item["id"]])
        self.mark(None)
        self.assertEqual(self.items(), [])

    def test_independent_schedule_progression_and_hint_miss_resets(self):
        item_id = self.mark()["id"]
        for count, interval in enumerate((3, 7, 14, 30, 30), 1):
            item = self.grade(item_id)["item"]
            self.assertEqual((item["listening_count"], item["independent_streak"], item["interval_days"]), (count, count, interval))
            self.assertEqual(item["due_at"], listening._iso(self.now + timedelta(days=interval)))
            self.now += timedelta(days=interval)
        hinted = self.grade(item_id, "hinted", True)["item"]
        self.assertEqual((hinted["independent_streak"], hinted["interval_days"]), (0, 1))
        self.assertEqual(hinted["due_at"], listening._iso(self.now + timedelta(days=1)))
        self.assertEqual(self.grade(item_id)["item"]["interval_days"], 3)
        missed = self.grade(item_id, "missed", False)["item"]
        self.assertEqual((missed["independent_streak"], missed["interval_days"]), (0, 0))
        self.assertEqual(missed["due_at"], listening._iso(self.now + timedelta(minutes=10)))

    def test_listening_does_not_change_text_review_mastery_counts_or_progress(self):
        card = self.excerpt()
        reviews.update_review({"id": card["id"], "mastered": True, "action": "reviewed"})
        self.favorite()
        before = {path.name: path.read_bytes() for path in (self.root / "review" / "items").glob("*.json")}
        progress = reviews.progress_path("lesson", self.state).read_bytes()
        self.grade(self.items()[0]["id"], "missed")
        self.assertEqual(before, {path.name: path.read_bytes() for path in (self.root / "review" / "items").glob("*.json")})
        self.assertEqual(reviews.progress_path("lesson", self.state).read_bytes(), progress)

    def test_attempt_retry_after_another_grade_never_overwrites_newer_history(self):
        item_id = self.mark()["id"]
        attempt = str(uuid.uuid4())
        self.assertFalse(self.grade(item_id, attempt_id=attempt)["duplicate"])
        latest = self.grade(item_id, "missed")["item"]
        before = listening._path(item_id).read_bytes()
        retry = self.grade(item_id, attempt_id=attempt)
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["item"], latest)
        self.assertEqual(listening._path(item_id).read_bytes(), before)
        with self.assertRaises(ValueError):
            self.grade(item_id, "hinted", True, attempt)
        self.assertEqual(listening._path(item_id).read_bytes(), before)

    def test_invalid_requests_and_assisted_independent_result_do_not_write(self):
        for data in ({"folder": "lesson", "segment_id": "s1", "reason": []}, {"folder": "../escape", "segment_id": "s1", "reason": "sound"}, {"folder": "lesson", "segment_id": "missing", "reason": "sound"}, {"folder": "lesson", "segment_id": "s1"}):
            with self.assertRaises((ValueError, OSError)):
                listening.mark_difficulty(data, self.state)
        self.assertFalse((self.root / "review").exists())
        item = self.mark()
        path = listening._path(item["id"])
        before = path.read_bytes()
        valid = {"id": item["id"], "result": "understood", "assisted": False, "attempt_id": str(uuid.uuid4())}
        for changes in ({"assisted": True}, {"assisted": 1}, {"result": []}, {"result": "known"}, {"attempt_id": "bad"}, {"id": "../escape"}, {"id": "f" * 32}):
            with self.assertRaises(ValueError):
                listening.grade_attempt(valid | changes, self.state)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(path.parent.glob("*.json"))), 1)

    def test_idempotency_history_is_bounded_and_latest_attempt_still_deduplicates(self):
        item_id = self.mark()["id"]
        attempt = None
        for _ in range(102):
            attempt = str(uuid.uuid4())
            self.grade(item_id, "missed", attempt_id=attempt)
        record = json.loads(listening._path(item_id).read_text())
        self.assertEqual((record["listening_count"], len(record["attempts"])), (102, 100))
        self.assertTrue(self.grade(item_id, "missed", attempt_id=attempt)["duplicate"])

    def test_renamed_package_uses_current_matching_times_and_preserves_snapshot(self):
        item = self.mark()
        renamed = self.folder.with_name("renamed")
        self.folder.rename(renamed)
        self.folder = renamed
        self.data["segments"][0].update(start=9.5, end=12)
        self.write_package()
        current = self.items()[0]
        self.assertEqual(current["source"], item["source"])
        self.assertEqual(current["audio"], {"available": True, "folder": "renamed", "video": "video.mp4", "start": 9.5, "end": 12})
        self.assertEqual(self.grade(item["id"])["item"]["listening_count"], 1)

    def test_missing_changed_or_replaced_source_never_plays_wrong_audio_or_accepts_grade(self):
        item = self.mark()
        path = listening._path(item["id"])
        before = path.read_bytes()
        for mutation in ("english", "id", "video"):
            with self.subTest(mutation=mutation):
                original = copy.deepcopy(self.data)
                if mutation == "english":
                    self.data["segments"][0]["en"] = "A different sentence."
                elif mutation == "id":
                    self.data["id"] = "another-package"
                else:
                    self.data["video"] = "missing.mp4"
                self.write_package()
                self.assertFalse(self.items()[0]["audio"]["available"])
                with self.assertRaises(ValueError):
                    self.grade(item["id"])
                self.assertEqual(path.read_bytes(), before)
                self.data = original
                self.write_package()
        shutil.rmtree(self.folder)
        self.assertFalse(self.items()[0]["audio"]["available"])
        with self.assertRaises(ValueError):
            self.grade(item["id"])
        self.assertEqual(path.read_bytes(), before)

    def test_archived_favorite_hides_history_and_restores_the_same_listening_record(self):
        self.favorite()
        item = self.grade(self.items()[0]["id"])["item"]
        self.favorite(False)
        self.assertEqual(self.items(), [])
        before = listening._path(item["id"]).read_bytes()
        with self.assertRaises(ValueError):
            self.grade(item["id"])
        self.assertEqual(listening._path(item["id"]).read_bytes(), before)
        self.favorite()
        restored = self.items()[0]
        self.assertEqual((restored["id"], restored["listening_count"], restored["due_at"]), (item["id"], 1, item["due_at"]))

    def test_legacy_listening_defaults_are_read_only_and_damaged_records_not_overwritten(self):
        self.favorite()
        item = self.items()[0]
        path = listening._path(item["id"])
        packages.atomic_json(path, {"id": item["id"], "source": item["source"]})
        before = path.read_bytes()
        normalized = self.items()[0]
        self.assertEqual((normalized["difficulty"], normalized["listening_count"], normalized["due_at"]), (None, 0, None))
        self.assertEqual(path.read_bytes(), before)
        path.write_text("damaged JSON")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.items(), [])
        with self.assertRaises(ValueError):
            self.grade(item["id"])
        self.assertEqual(path.read_text(), "damaged JSON")

    def test_listening_directory_record_and_media_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            outside = Path(temp)
            self.excerpt()
            directory = self.root / "review" / "listening"
            directory.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.mark()
            with self.assertRaises(ValueError):
                self.items()
            self.assertEqual(list(outside.iterdir()), [])
            directory.unlink()
            item = self.mark()
            path = listening._path(item["id"])
            external = outside / "record.json"
            path.rename(external)
            before = external.read_bytes()
            path.symlink_to(external)
            with self.assertRaises(ValueError):
                self.grade(item["id"])
            self.assertEqual(external.read_bytes(), before)
            path.unlink()
            external.rename(path)
            video = self.folder / "video.mp4"
            external_video = outside / "video.mp4"
            video.rename(external_video)
            video.symlink_to(external_video)
            self.assertFalse(self.items()[0]["audio"]["available"])
            with self.assertRaises(ValueError):
                self.grade(item["id"])


if __name__ == "__main__":
    unittest.main()
