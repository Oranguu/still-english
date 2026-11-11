import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobs
import packages
import server


def manifest():
    return {"schema_version": 1, "id": "sample", "title": "Sample", "video": "video.mp4", "segments": []}


def sample_package():
    return packages.prepare_captions(manifest(), [
        {"start": 0, "end": .5, "text": "Um."},
        {"start": 10, "end": 10.5, "text": "Yes."},
        {"start": 11, "end": 14, "text": "We should bring our books to school."},
        {"start": 25, "end": 26, "text": "Thanks."},
    ])


class PlaybackCaptionTests(unittest.TestCase):
    def test_prepare_keeps_all_original_captions_separate_from_learning(self):
        data = sample_package()
        self.assertEqual(data["segmentation_version"], 2)
        self.assertEqual(len(data["subtitle_segments"]), 4)
        self.assertEqual(len(data["segments"]), 1)
        self.assertEqual(data["segments"][0]["source_ids"], ["s0002", "s0003"])
        self.assertEqual(set(data["subtitle_segments"][0]), {"id", "start", "end", "en", "zh"})
        self.assertEqual(packages.playback_segments(data), data["subtitle_segments"])
        packages.validate(data)

    def test_new_translation_plays_the_merged_english_and_chinese_together(self):
        data = sample_package()
        data["segments"][0]["zh"] = "是的。我们应该带课本去学校。"
        original = copy.deepcopy(data)
        rows = packages.playback_segments(data)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["id"] for row in rows], ["s0001", "s0002", "s0004"])
        self.assertEqual((rows[1]["start"], rows[1]["end"]), (10, 14))
        self.assertEqual(rows[1]["en"], data["segments"][0]["en"])
        self.assertEqual(rows[1]["zh"], data["segments"][0]["zh"])
        self.assertEqual([rows[0]["en"], rows[2]["en"]], ["Um.", "Thanks."])
        self.assertEqual(data, original)

    def test_existing_precise_translations_are_never_widened(self):
        data = sample_package()
        data["segments"][0]["zh"] = "是的。我们应该带课本去学校。"
        data["subtitle_segments"][1]["zh"] = "是的。"
        data["subtitle_segments"][2]["zh"] = "我们应该带课本去学校。"
        self.assertEqual(packages.playback_segments(data), data["subtitle_segments"])
        # Even a partially translated original group must retain its good timing.
        data["subtitle_segments"][2]["zh"] = ""
        self.assertEqual(packages.playback_segments(data), data["subtitle_segments"])

    def test_legacy_package_keeps_its_existing_subtitles(self):
        data = manifest()
        data["segments"] = [{"id": "old", "start": 1, "end": 3, "en": "Please open the window.", "zh": "请打开窗户。"}]
        self.assertEqual(packages.playback_segments(data), data["segments"])
        self.assertTrue(packages.has_captions(data))
        packages.validate(data)

    def test_noncontiguous_source_group_cannot_hide_intervening_captions(self):
        data = manifest()
        data["subtitle_segments"] = [
            {"id": "a", "start": 0, "end": 1, "en": "Go home.", "zh": ""},
            {"id": "b", "start": 1.1, "end": 1.5, "en": "Um.", "zh": ""},
            {"id": "c", "start": 2, "end": 5, "en": "The rain will start very soon.", "zh": ""},
        ]
        data["segments"] = [{"id": "new", "start": 0, "end": 5,
                             "en": "Go home. The rain will start very soon.", "zh": "回家吧，很快就要下雨了。", "source_ids": ["a", "c"]}]
        packages.validate(data)
        self.assertEqual(packages.playback_segments(data), data["subtitle_segments"])

    def test_malformed_raw_captions_and_source_references_are_rejected(self):
        changes = [
            lambda d: d.update(subtitle_segments=None),
            lambda d: d["subtitle_segments"][0].update(start=float("nan")),
            lambda d: d["subtitle_segments"][0].update(end=float("inf")),
            lambda d: d["subtitle_segments"][0].update(start=True),
            lambda d: d["subtitle_segments"][0].update(start=-1),
            lambda d: d["subtitle_segments"][1].update(start=.2),
            lambda d: d["subtitle_segments"][1].update(id="s0001"),
            lambda d: d["subtitle_segments"][1].update(zh=[]),
            lambda d: d["segments"][0].update(source_ids="s0002"),
            lambda d: d["segments"][0].update(source_ids=[]),
            lambda d: d["segments"][0].update(source_ids=["missing"]),
            lambda d: d["segments"][0].update(source_ids=[{}]),
            lambda d: d["segments"][0].update(source_ids=["s0002", "s0002"]),
            lambda d: d["segments"][0].update(source_ids=["s0003", "s0002"]),
            lambda d: d["segments"][0].update(start=9),
            lambda d: d["segments"][0].update(end=15),
            lambda d: d.pop("subtitle_segments"),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                data = sample_package()
                change(data)
                with self.assertRaises(ValueError):
                    packages.validate(data)
                with self.assertRaises(ValueError):
                    packages.playback_segments(data)

    def test_exports_keep_skipped_subtitles_and_joined_translations(self):
        data = sample_package()
        data["segments"][0]["zh"] = "是的。我们应该带课本去学校。"
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "video.mp4").write_bytes(b"sample")
            packages.save_package(folder, data)
            english = (folder / "english.vtt").read_text("utf8")
            bilingual = (folder / "bilingual.vtt").read_text("utf8")
            self.assertIn("Um.", english)
            self.assertIn("Thanks.", english)
            self.assertIn("00:00:10.000 --> 00:00:14.000", bilingual)
            self.assertIn(data["segments"][0]["en"] + "\n" + data["segments"][0]["zh"], bilingual)
            self.assertNotIn("playback_segments", json.loads((folder / "manifest.json").read_text("utf8")))

    def test_filler_only_package_has_captions_and_a_library_count(self):
        data = packages.prepare_captions(manifest(), [{"start": 0, "end": 1, "text": "Thank you."}])
        self.assertFalse(data["segments"])
        self.assertTrue(packages.has_captions(data))
        self.assertEqual(len(packages.playback_segments(data)), 1)
        with tempfile.TemporaryDirectory() as temp, patch.object(packages, "ROOT", Path(temp)):
            folder = Path(temp) / "source" / "sample"
            folder.mkdir(parents=True)
            (folder / "video.mp4").write_bytes(b"sample")
            packages.save_package(folder, data)
            summary = packages.list_packages()[0]
            self.assertEqual((summary["count"], summary["caption_count"]), (0, 1))


class CaptionPipelineTests(unittest.TestCase):
    def test_transcribe_accepts_filler_only_audio_and_keeps_original_subtitles(self):
        data = manifest()
        job = Mock()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "video.mp4").write_bytes(b"sample")
            output = root / "state" / "transcripts" / "sample.json"

            def fake_run(*args, **kwargs):
                output.write_text(json.dumps([{"start": 0, "end": 1, "text": "Yes."}]), "utf8")
                output.with_suffix(".metadata.json").write_text(json.dumps({"model": "test"}), "utf8")

            with patch.object(jobs, "STATE", root / "state"), patch.object(jobs.speech, "backend", return_value=True), patch.object(jobs, "run", side_effect=fake_run):
                jobs.transcribe(root, data, job)
            stored = json.loads((root / "manifest.json").read_text("utf8"))
            self.assertEqual(stored["status"], "captions_ready")
            self.assertEqual(stored["segments"], [])
            self.assertEqual(len(stored["subtitle_segments"]), 1)

    def test_retry_does_not_transcribe_a_filler_only_package_again(self):
        data = packages.prepare_captions(manifest(), [{"start": 0, "end": 1, "text": "Yes."}])
        data["source_kind"] = "local"

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        for kind in ("local", "analyze"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                with patch.object(jobs, "STATE", Path(temp)), patch.object(jobs, "JOBS", {}), patch.object(jobs, "read_package", return_value=data), patch.object(jobs, "package_folder", return_value=Path(temp) / "sample"), patch.object(jobs, "transcribe") as transcribe, patch.object(jobs.ai, "analyze"), patch.object(jobs.threading, "Thread", ImmediateThread):
                    result = jobs.launch(kind, {"folder": "sample", "analyze": False})
                self.assertEqual(result["status"], "complete")
                transcribe.assert_not_called()

    def test_package_endpoint_exposes_derived_subtitles_without_changing_manifest(self):
        data = sample_package()
        data["segments"][0]["zh"] = "是的。我们应该带课本去学校。"
        original = copy.deepcopy(data)
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/api/package?folder=sample"
        handler.permitted = Mock(return_value=True)
        handler.reply = Mock()
        with patch.object(server, "read_package", return_value=data):
            handler.do_GET()
        response = handler.reply.call_args.args[0]
        self.assertEqual(response["playback_segments"], packages.playback_segments(data))
        self.assertEqual(data, original)

    def test_manual_caption_import_uses_compact_lessons_and_complete_subtitles(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "sample"
            folder.mkdir()
            (folder / "video.mp4").write_bytes(b"sample")
            data = manifest()
            handler = server.Handler.__new__(server.Handler)
            handler.path = "/api/captions?folder=sample&ext=.srt"
            handler.permitted = Mock(return_value=True)
            handler.reply = Mock()
            handler.body = Mock(return_value=b"1\n00:00:00,000 --> 00:00:01,000\nThank you.\n")
            with patch.object(server, "package_folder", return_value=folder), patch.object(server, "read_package", return_value=data):
                handler.do_POST()
            handler.reply.assert_called_once_with({"ok": True})
            stored = json.loads((folder / "manifest.json").read_text("utf8"))
            self.assertEqual(stored["segments"], [])
            self.assertEqual(len(stored["subtitle_segments"]), 1)
            self.assertEqual(stored["status"], "captions_ready")


if __name__ == "__main__":
    unittest.main()
