import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog
import packages


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        root_patch = patch.object(packages, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        catalog.CACHE.clear()
        self.addCleanup(catalog.CACHE.clear)
        self.first = self.material("one", "Morning travel vlog", 500, "2025-01-01")
        self.second = self.material("two", "A course about cameras", 4000, "2025-02-01")

    def material(self, name, title, duration, created):
        folder = self.root / "source" / name
        folder.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"synthetic video")
        data = {"schema_version": 1, "id": name, "title": title, "creator": "Example channel", "video": "video.mp4", "source_url": "https://www.youtube.com/watch?v=example0001", "duration": duration, "created_at": created,
                "segments": [{"id": "s1", "start": 1, "end": 3, "en": "Let's catch a bus.", "zh": "我们去坐公共汽车吧。"}, {"id": "s2", "start": 4, "end": 6, "en": "A quiet morning.", "zh": "安静的早晨。"}]}
        packages.save_package(folder, data)
        return folder

    def organize(self, folder, category="想反复听", tags=None, **extra):
        return catalog.organize({"folder": folder.name, "package_id": folder.name, "category": category, "tags": ["口语", "旅行"] if tags is None else tags, **extra}, self.state)

    def test_automatic_defaults_do_not_modify_materials_or_create_preferences(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*.json")}
        result = catalog.library(self.state)
        rows = {p["id"]: p for p in result["packages"]}
        self.assertEqual(rows["one"]["category"], "Vlog")
        self.assertTrue({"YouTube", "10 分钟内", "旅行"}.issubset(rows["one"]["tags"]))
        self.assertEqual(rows["two"]["category"], "课程")
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*.json")}, before)
        self.assertFalse(self.state.exists())
        self.assertEqual(catalog.defaults({"title": "An untitled story", "duration": 5000, "source_kind": "local"})["category"], "长视频")
        self.assertEqual(catalog.defaults({"title": "No hints", "source_url": "https://[bad"})["category"], "未分类")

    def test_preferences_survive_rename_restart_and_lesson_updates(self):
        before = (self.first / "manifest.json").read_bytes()
        self.organize(self.first, tags=["Everyday", "ｅｖｅｒｙｄａｙ", "  口语  "])
        self.assertEqual((self.first / "manifest.json").read_bytes(), before)
        renamed = self.first.with_name("renamed")
        self.first.rename(renamed)
        data = packages.read_package("renamed")
        data["segments"][0].update(zh="新的中文翻译", analysis={"meaning": "New lesson"})
        packages.save_package(renamed, data)
        catalog.CACHE.clear()
        row = next(p for p in catalog.library(self.state)["packages"] if p["id"] == "one")
        self.assertEqual(row["folder"], "renamed")
        self.assertEqual(row["category"], "想反复听")
        self.assertEqual(row["tags"], ["Everyday", "口语"])
        self.assertFalse(row["automatic"])
        self.assertEqual(catalog.library(self.state, query="新的中文翻译")["matched"], 1)

    def test_clear_and_reset_are_explicit_and_preserve_lessons(self):
        before = (self.first / "manifest.json").read_bytes()
        self.organize(self.first, category="电影", tags=[])
        row = next(p for p in catalog.library(self.state)["packages"] if p["id"] == "one")
        self.assertEqual(row["tags"], [])
        self.organize(self.first, reset=True)
        row = next(p for p in catalog.library(self.state)["packages"] if p["id"] == "one")
        self.assertTrue(row["automatic"])
        self.assertEqual(row["category"], "Vlog")
        self.assertIn("YouTube", row["tags"])
        self.assertEqual((self.first / "manifest.json").read_bytes(), before)

    def test_search_combines_category_tags_and_bilingual_subtitles(self):
        self.organize(self.first)
        self.organize(self.second, tags=["口语"])
        result = catalog.library(self.state, query="ＣＡＴＣＨ 公共汽车", category="想反复听", tags=["口语", "旅行"])
        self.assertEqual([p["id"] for p in result["packages"]], ["one"])
        self.assertEqual(result["packages"][0]["match"]["id"], "s1")
        self.assertEqual(result["packages"][0]["match"]["start"], 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(catalog.library(self.state, query="Let’s")["matched"], 2)
        self.assertEqual(catalog.library(self.state, query="Example")["matched"], 2)
        self.assertEqual(catalog.library(self.state, query="课程")["matched"], 0)
        empty = catalog.library(self.state, query="not-present")
        self.assertEqual(empty["matched"], 0)
        self.assertEqual(sum(c["count"] for c in empty["categories"]), 2)

    def test_cache_refreshes_when_translations_arrive_and_removes_missing_media(self):
        self.assertEqual(catalog.library(self.state, query="新生成")["matched"], 0)
        data = packages.read_package("one")
        data["segments"][0]["zh"] = "新生成的翻译"
        packages.save_package(self.first, data)
        self.assertEqual(catalog.library(self.state, query="新生成")["matched"], 1)
        (self.first / "video.mp4").unlink()
        self.assertEqual(catalog.library(self.state)["total"], 1)

    def test_sorting_and_casefolded_facets(self):
        self.assertEqual([p["id"] for p in catalog.library(self.state)["packages"]], ["two", "one"])
        self.assertEqual([p["id"] for p in catalog.library(self.state, sort="short")["packages"]], ["one", "two"])
        self.assertEqual([p["id"] for p in catalog.library(self.state, sort="long")["packages"]], ["two", "one"])
        self.assertEqual([p["id"] for p in catalog.library(self.state, sort="title")["packages"]], ["two", "one"])
        self.organize(self.first, category="Vlog", tags=["Travel"])
        self.organize(self.second, category="vlog", tags=["travel"])
        result = catalog.library(self.state, category="VLOG", tags=["TRAVEL"])
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["categories"], [{"name": "Vlog", "count": 2}])
        self.assertEqual(result["tags"], [{"name": "Travel", "count": 2}])

    def test_invalid_edits_and_stale_identity_leave_existing_preferences_untouched(self):
        self.organize(self.first)
        path = catalog.preference_path(self.state, "one")
        before = path.read_bytes()
        for extra in ({"package_id": "another"}, {"category": ""}, {"category": "x" * 33}, {"tags": "text"}, {"tags": ["x"] * 17}, {"tags": ["bad\nlabel"]}, {"reset": "yes"}, {"folder": "../one"}):
            request = {"folder": "one", "package_id": "one", "category": "Valid", "tags": []} | extra
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                catalog.organize(request, self.state)
            self.assertEqual(path.read_bytes(), before)
        for params in ({"query": "x" * 201}, {"sort": "invalid"}, {"category": "x" * 33}):
            with self.assertRaises(ValueError):
                catalog.library(self.state, **params)

    def test_corrupt_preferences_remain_visible_and_are_never_silently_overwritten(self):
        self.organize(self.first)
        path = catalog.preference_path(self.state, "one")
        path.write_text('{"broken":true}')
        row = next(p for p in catalog.library(self.state)["packages"] if p["id"] == "one")
        self.assertIn("catalog_error", row)
        with self.assertRaises(ValueError):
            self.organize(self.first, reset=True)
        self.assertEqual(path.read_text(), '{"broken":true}')

    def test_symlink_preferences_cannot_read_or_write_outside_state(self):
        outside = self.root / "outside.json"
        outside.write_text("private data")
        path = catalog.preference_path(self.state, "one")
        path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.organize(self.first)
        row = next(p for p in catalog.library(self.state)["packages"] if p["id"] == "one")
        self.assertIn("catalog_error", row)
        self.assertNotIn("private data", json.dumps(row))
        self.assertEqual(outside.read_text(), "private data")


if __name__ == "__main__":
    unittest.main()
