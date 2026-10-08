import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai
import jobs
from packages import save_package


def lesson(identifier):
    return {
        "id": identifier,
        "zh": "我们可以一起学习。",
        "analysis": {
            "meaning": "说话的人邀请大家一起学。can 在这里告诉我们：这件事可以做到。",
            "vocabulary": [{"term": "We", "meaning": "我们，包括说话的人和其他人。", "example": "We play. 我们玩。"}],
            "phrases": [],
            "sentence_parts": [{"chunk": "We can learn.", "meaning": "我们可以学习。", "role": "先说是谁，再说可以做什么。"}],
            "grammar": [{"pattern": "We + can + learn", "explanation": "We 说的是谁，can 表示可以，learn 表示学习。"}],
            "speech": [],
            "paraphrase": "We can study. 我们可以学习。",
            "practice": "先读 I can learn（我可以学习），再把 I 换成 We 试试。",
        },
    }


class LessonValidationTests(unittest.TestCase):
    def test_malformed_batch_is_rejected_without_type_errors(self):
        for output in (None, [], {"segments": [None]}, {"segments": ["s0"]}):
            with self.subTest(output=output), self.assertRaises(ValueError):
                ai.validate_batch(output, [{"id": "s0"}])

    def test_required_sections_and_entries_cannot_be_blank(self):
        for field in ("vocabulary", "sentence_parts", "grammar"):
            row = lesson("s0")
            row["analysis"][field] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                ai.validate_batch({"segments": [row]}, [{"id": "s0"}])
        for field, key in (("vocabulary", "example"), ("sentence_parts", "role"), ("grammar", "explanation")):
            for value in (" ", None, 4):
                row = lesson("s0")
                row["analysis"][field][0][key] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    ai.validate_batch({"segments": [row]}, [{"id": "s0"}])
        row = lesson("s0")
        row["analysis"] = []
        with self.assertRaises(ValueError):
            ai.validate_batch({"segments": [row]}, [{"id": "s0"}])

    def test_optional_content_sections_still_require_correct_types(self):
        for field, key in (("phrases", "meaning"), ("speech", "tip")):
            for bad in (None, [{key: ""}], ["not an entry"]):
                row = lesson("s0")
                row["analysis"][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    ai.validate_batch({"segments": [row]}, [{"id": "s0"}])
        valid = {"segments": [lesson("s0")]}
        self.assertEqual(ai.validate_batch(valid, [{"id": "s0"}]), valid["segments"])


class AnalysisUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "package"
        self.folder.mkdir()
        (self.folder / "video.mp4").write_bytes(b"local test video")
        self.state = self.root / "state"
        state_patch = patch.object(ai, "STATE", self.state)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        auth_patch = patch.object(ai, "auth_status", return_value={"ready": True})
        self.auth = auth_patch.start()
        self.addCleanup(auth_patch.stop)
        cli_patch = patch.object(ai, "codex_path", return_value="mock-codex")
        cli_patch.start()
        self.addCleanup(cli_patch.stop)
        self.requests = []
        self.fail_batch = None

    def package(self, count):
        data = {
            "schema_version": 1, "id": "test-package", "title": "Test lesson", "video": "video.mp4",
            "segments": [dict(id=f"s{i}", start=i * 2, end=i * 2 + 1, en="We can learn.",
                              zh="旧翻译", analysis={"meaning": "旧讲解"}) for i in range(count)],
        }
        save_package(self.folder, data)
        return data

    def job(self, identifier="job-one"):
        return SimpleNamespace(id=identifier, update=Mock(), check=Mock())

    def run_model(self, command, job, **kwargs):
        request = json.loads(kwargs["input_text"][len(ai.INSTRUCTIONS):].strip())
        self.requests.append(request)
        rows = [lesson(segment["id"]) for segment in request["requested"]]
        if len(self.requests) == self.fail_batch:
            rows[-1]["analysis"]["sentence_parts"] = []
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({"segments": rows}), encoding="utf8")

    def test_default_only_fills_missing_content_and_preserves_old_lessons(self):
        data = self.package(3)
        data["segments"][1]["analysis"] = None
        save_package(self.folder, data)
        original = copy.deepcopy(data)
        ai.analyze(self.folder, data, self.job(), self.run_model)
        self.assertEqual([row["id"] for row in self.requests[0]["requested"]], ["s1"])
        self.assertEqual(data["segments"][0], original["segments"][0])
        self.assertEqual(data["segments"][2], original["segments"][2])
        self.assertEqual(data["segments"][1]["analysis_version"], 2)
        backup = self.state / "backups" / "job-one" / "manifest.json"
        self.assertEqual(json.loads(backup.read_text()), original)

    def test_segment_upgrade_keeps_other_segments_and_skips_finished_work(self):
        data = self.package(3)
        original = copy.deepcopy(data)
        ai.analyze(self.folder, data, self.job(), self.run_model, upgrade=True, segment_id="s1")
        self.assertEqual(data["segments"][0], original["segments"][0])
        self.assertEqual(data["segments"][2], original["segments"][2])
        self.assertEqual(data["segments"][1]["analysis_version"], 2)
        self.auth.reset_mock()
        ai.analyze(self.folder, data, self.job("unused"), self.run_model, upgrade=True, segment_id="s1")
        self.auth.assert_not_called()
        self.assertEqual(len(self.requests), 1)
        with self.assertRaises(ValueError):
            ai.analyze(self.folder, data, self.job(), self.run_model, upgrade=True, segment_id="unknown")

    def test_invalid_batch_cannot_replace_any_old_lesson(self):
        data = self.package(3)
        original = copy.deepcopy(data)
        self.fail_batch = 1
        with self.assertRaises(ValueError):
            ai.analyze(self.folder, data, self.job(), self.run_model, upgrade=True)
        self.assertEqual(data, original)
        self.assertEqual(json.loads((self.folder / "manifest.json").read_text()), original)
        self.assertFalse((self.state / "backups").exists())

    def test_interrupted_upgrade_keeps_completed_batch_and_resumes_remaining(self):
        data = self.package(ai.BATCH_SIZE + 2)
        original = copy.deepcopy(data)
        self.fail_batch = 2
        with self.assertRaises(ValueError):
            ai.analyze(self.folder, data, self.job(), self.run_model, upgrade=True)
        self.assertEqual(sum(s.get("analysis_version") == 2 for s in data["segments"]), ai.BATCH_SIZE)
        self.assertEqual(data["segments"][-2:], original["segments"][-2:])
        backup = self.state / "backups" / "job-one" / "manifest.json"
        self.assertEqual(json.loads(backup.read_text()), original)
        self.fail_batch = None
        ai.analyze(self.folder, data, self.job("retry"), self.run_model, upgrade=True)
        self.assertEqual([s["id"] for s in self.requests[-1]["requested"]], [f"s{ai.BATCH_SIZE}", f"s{ai.BATCH_SIZE + 1}"])
        self.assertTrue(all(s["analysis_version"] == 2 for s in data["segments"]))
        self.assertEqual(json.loads(backup.read_text()), original)

    def test_all_successful_batches_share_one_original_backup(self):
        data = self.package(ai.BATCH_SIZE + 1)
        original = copy.deepcopy(data)
        ai.analyze(self.folder, data, self.job(), self.run_model, upgrade=True)
        backups = list((self.state / "backups" / "job-one").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        self.assertEqual(json.loads((self.folder / "manifest.json").read_text()), data)

    def test_nonadjacent_pending_segments_receive_their_own_context(self):
        data = self.package(10)
        for segment in data["segments"]:
            segment["analysis_version"] = 2
        for i in (1, 8):
            data["segments"][i]["analysis_version"] = 1
        ai.analyze(self.folder, data, self.job(), self.run_model, upgrade=True)
        self.assertEqual([s["id"] for s in self.requests[0]["requested"]], ["s1", "s8"])
        self.assertTrue({"s0", "s1", "s2", "s7", "s8", "s9"}.issubset({s["id"] for s in self.requests[0]["context"]}))

    def test_cancellation_after_model_output_preserves_previous_manifest(self):
        data = self.package(1)
        original = copy.deepcopy(data)
        job = self.job()
        job.check.side_effect = jobs.Cancelled()
        with self.assertRaises(jobs.Cancelled):
            ai.analyze(self.folder, data, job, self.run_model, upgrade=True)
        self.assertEqual(data, original)
        self.assertEqual(json.loads((self.folder / "manifest.json").read_text()), original)

    def test_job_launch_forwards_upgrade_scope(self):
        data = self.package(1)

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch.object(jobs, "STATE", self.state), patch.object(jobs, "JOBS", {}), \
                patch.object(jobs, "package_folder", return_value=self.folder), \
                patch.object(jobs, "read_package", return_value=data), \
                patch.object(jobs.threading, "Thread", ImmediateThread), \
                patch.object(ai, "analyze") as analyze:
            result = jobs.launch("analysis", {"folder": "package", "upgrade": True, "segment_id": "s0"})
            self.assertEqual(result["status"], "complete")
            self.assertEqual(analyze.call_args.kwargs, {"upgrade": True, "segment_id": "s0"})


if __name__ == "__main__":
    unittest.main()
