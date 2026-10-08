import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import packages
import reviews


def example():
    return {
        "schema_version": 1, "id": "sample-id", "title": "Sample lesson", "video": "video.mp4",
        "segments": [{"id": "s1", "start": 1, "end": 3, "en": "I can jump.", "zh": "我会跳。", "analysis_version": 2,
                      "analysis": {"meaning": "🙂 小朋友会跳。", "vocabulary": [{"term": "can", "meaning": "能够", "example": "I can run. 我会跑。"}]}}],
    }


class StorageFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(packages, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def package(self, folder="lesson", parent=None):
        path = (parent or self.root) / folder
        path.mkdir(parents=True)
        (path / "video.mp4").write_bytes(b"sample video")
        packages.atomic_json(path / "manifest.json", example())
        return path


class StorageTests(StorageFixture):
    def test_migration_preserves_package_identity_media_and_progress(self):
        import server
        folder = self.package()
        manifest = (folder / "manifest.json").read_bytes()
        progress = self.root / "tool" / ".state" / "progress" / (hashlib.sha256(b"sample-id").hexdigest()[:24] + ".json")
        packages.atomic_json(progress, {"favorites": ["s1"], "lastTime": 2.1})
        self.assertEqual(packages.migrate_library(), ["lesson"])
        self.assertEqual(packages.ROOT, self.root)
        self.assertFalse(folder.exists())
        self.assertEqual((self.root / "source" / "lesson" / "manifest.json").read_bytes(), manifest)
        self.assertEqual((self.root / "source" / "lesson" / "video.mp4").read_bytes(), b"sample video")
        with patch.object(server, "STATE", self.root / "tool" / ".state"):
            self.assertEqual(server.progress_path("lesson"), progress)
        self.assertEqual(json.loads(progress.read_text())["favorites"], ["s1"])
        self.assertEqual(packages.migrate_library(), [])
        self.assertEqual(packages.list_packages()[0]["detailed"], 1)

    def test_migration_checks_all_conflicts_before_moving_anything(self):
        first, second = self.package("a"), self.package("z")
        conflict = self.package("z", self.root / "source")
        original = (conflict / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "同名"):
            packages.migrate_library()
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertFalse((self.root / "source" / "a").exists())
        self.assertEqual((conflict / "manifest.json").read_bytes(), original)

    def test_unrelated_and_incomplete_folders_are_untouched(self):
        path = self.root / "notes"
        path.mkdir()
        (path / "manifest.json").write_text('{"a": "personal notes"}')
        partial = self.root / "partial"
        partial.mkdir()
        packages.atomic_json(partial / "manifest.json", example())
        self.assertEqual(packages.migrate_library(), [])
        self.assertTrue(path.exists())
        self.assertTrue(partial.exists())

    def test_symlinks_never_migrate_or_redirect_library(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere)
            (self.root / "source").symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                packages.migrate_library()
            with self.assertRaises(ValueError):
                packages.package_folder("sample")
            (self.root / "source").unlink()
            folder = self.package()
            (folder / "unexpected-link").symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                packages.migrate_library()
            self.assertTrue(folder.exists())
            self.assertEqual(list(target.iterdir()), [])

    def test_library_listing_ignores_invalid_versions(self):
        self.package(parent=self.root / "source")
        data = example()
        data["segments"][0]["analysis_version"] = "2"
        packages.atomic_json(self.root / "source" / "lesson" / "manifest.json", data)
        self.assertEqual(packages.list_packages()[0]["detailed"], 0)
        for name in ("../lesson", "", ".hidden", "a/b", "a\\b"):
            with self.assertRaises(ValueError):
                packages.package_folder(name)


class ReviewTests(StorageFixture):
    def setUp(self):
        super().setUp()
        self.folder = self.package(parent=self.root / "source")
        self.request = {"folder": "lesson", "segment_id": "s1", "selections": [{"field": "analysis.meaning", "start": 2, "end": 5, "quote": "小朋友"}], "note": "明天复习"}

    def test_review_snapshot_persists_after_lesson_changes_and_removal(self):
        result = reviews.add_review(self.request)
        self.assertFalse(result["existing"])
        item = result["item"]
        self.assertEqual(item["selections"][0]["context"], "🙂 小朋友会跳。")
        self.assertEqual(item["source"]["en"], "I can jump.")
        self.assertEqual(item["source"]["package_id"], "sample-id")
        path = self.root / "review" / "items" / (item["id"] + ".json")
        self.assertEqual(json.loads(path.read_text()), item)
        data = example()
        data["segments"][0]["analysis"]["meaning"] = "新版讲解"
        packages.atomic_json(self.folder / "manifest.json", data)
        self.assertEqual(reviews.list_reviews(), [item])
        shutil.rmtree(self.folder)
        self.assertEqual(reviews.list_reviews(), [item])
        updated = reviews.update_review({"id": item["id"], "action": "reviewed"})["item"]
        self.assertEqual(updated["review_count"], 1)
        self.assertIsNotNone(updated["last_reviewed_at"])

    def test_duplicate_preserves_notes_mastery_and_review_count(self):
        self.request["selections"].append({"field": "analysis.vocabulary.0.meaning", "start": 0, "end": 2, "quote": "能够"})
        item = reviews.add_review(self.request)["item"]
        edited = reviews.update_review({"id": item["id"], "note": "自己的补充", "mastered": True, "action": "reviewed"})["item"]
        self.request["selections"].reverse()
        self.request["note"] = "不应该替换笔记"
        duplicate = reviews.add_review(self.request)
        self.assertTrue(duplicate["existing"])
        self.assertEqual(duplicate["item"], edited)
        self.assertEqual(len(reviews.list_reviews()), 1)
        reviews.delete_review({"id": item["id"]})
        self.assertEqual(reviews.list_reviews(), [])
        self.assertEqual(reviews.delete_review({"id": item["id"]}), {"ok": True})

    def test_offsets_are_unicode_codepoints_and_whitespace_is_preserved(self):
        request = copy.deepcopy(self.request)
        request["selections"] = [{"field": "analysis.meaning", "start": 0, "end": 2, "quote": "🙂 "}]
        self.assertEqual(reviews.add_review(request)["item"]["quote"], "🙂 ")
        request["selections"][0].update(start=1, end=2, quote=" ")
        with self.assertRaises(ValueError):
            reviews.add_review(request)

    def test_multi_field_highlight_preserves_reading_order_while_deduplicating(self):
        vocabulary = example()["segments"][0]["analysis"]["vocabulary"][0]
        fields = ("term", "meaning", "example")
        self.request["selections"] = [
            {"field": f"analysis.vocabulary.0.{field}", "start": 0, "end": len(vocabulary[field]), "quote": vocabulary[field]}
            for field in fields
        ]
        item = reviews.add_review(self.request)["item"]
        self.assertEqual([s["field"] for s in item["selections"]], [f"analysis.vocabulary.0.{field}" for field in fields])
        self.assertEqual(item["quote"], "can\n能够\nI can run. 我会跑。")
        self.request["selections"].reverse()
        duplicate = reviews.add_review(self.request)
        self.assertTrue(duplicate["existing"])
        self.assertEqual(duplicate["item"], item)
        self.assertEqual(reviews.list_reviews(), [item])

    def test_changed_full_context_creates_a_new_snapshot_for_same_quote(self):
        original = reviews.add_review(self.request)["item"]
        data = example()
        data["segments"][0]["analysis"]["meaning"] = "🙂 小朋友会跳，也会跑。"
        packages.atomic_json(self.folder / "manifest.json", data)
        result = reviews.add_review(self.request)
        self.assertFalse(result["existing"])
        updated = result["item"]
        self.assertNotEqual(updated["id"], original["id"])
        self.assertEqual(updated["quote"], original["quote"])
        self.assertEqual(updated["selections"][0]["context"], "🙂 小朋友会跳，也会跑。")
        self.assertEqual(original["selections"][0]["context"], "🙂 小朋友会跳。")
        self.assertEqual(len(reviews.list_reviews()), 2)
        self.assertTrue(reviews.add_review(self.request)["existing"])

    def test_stale_ranges_non_leaf_fields_and_untrusted_shapes_are_rejected(self):
        invalid = [
            {"quote": "不匹配"}, {"start": -1}, {"end": 1000}, {"start": True},
            {"field": "analysis.vocabulary"}, {"field": "en"}, {"field": "analysis.__class__"},
            {"field": "analysis.vocabulary.999999999999999999999999.meaning"},
            {"field": "analysis/../../private"}, {"field": ["analysis"]},
        ]
        for mutation in invalid:
            with self.subTest(mutation=mutation):
                request = copy.deepcopy(self.request)
                request["selections"][0].update(mutation)
                with self.assertRaises(ValueError):
                    reviews.add_review(request)
        for mutation in ({"selections": []}, {"selections": [None]}, {"folder": "../lesson"}, {"segment_id": []}, {"note": []}, {"note": "a" * 10001}):
            with self.assertRaises(ValueError):
                reviews.add_review(self.request | mutation)
        with self.assertRaises(ValueError):
            reviews.add_review(None)
        self.assertEqual(reviews.list_reviews(), [])

    def test_html_is_saved_as_plain_text_and_invalid_updates_do_not_write(self):
        text = '<script>alert("example")</script>'
        self.request["note"] = text
        item = reviews.add_review(self.request)["item"]
        self.assertEqual(reviews.list_reviews()[0]["note"], text)
        for mutation in ({"note": []}, {"mastered": 1}, {"action": "delete"}, {"id": "../secret"}):
            with self.assertRaises(ValueError):
                reviews.update_review({"id": item["id"]} | mutation)
        self.assertEqual(reviews.list_reviews()[0], item)

    def test_damaged_cards_are_preserved_and_do_not_hide_healthy_cards(self):
        item = reviews.add_review(self.request)["item"]
        broken = self.root / "review" / "items" / ("f" * 32 + ".json")
        broken.write_text("not valid JSON")
        with self.assertLogs(level="WARNING") as logs:
            self.assertEqual(reviews.list_reviews(), [item])
        self.assertIn("已跳过并保留原文件", logs.output[0])
        with self.assertRaises(ValueError):
            reviews.update_review({"id": "f" * 32, "note": "cannot overwrite"})
        self.assertEqual(broken.read_text(), "not valid JSON")

    def test_review_symlink_directories_and_files_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere)
            (self.root / "review").symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                reviews.add_review(self.request)
            self.assertEqual(list(target.iterdir()), [])
            (self.root / "review").unlink()
            item = reviews.add_review(self.request)["item"]
            card = self.root / "review" / "items" / (item["id"] + ".json")
            copy_path = target / "private.json"
            card.rename(copy_path)
            original = copy_path.read_bytes()
            card.symlink_to(copy_path)
            with self.assertRaises(ValueError):
                reviews.update_review({"id": item["id"], "note": "never write"})
            with self.assertRaises(ValueError):
                reviews.delete_review({"id": item["id"]})
            self.assertEqual(copy_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
