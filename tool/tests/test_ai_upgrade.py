import copy
import json
import sys
import tempfile
import threading
import time
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


def simple_lesson(identifier):
    row = lesson(identifier)
    row["analysis"].pop("phrases")
    row["analysis"].pop("sentence_parts")
    row["analysis"]["grammar"] = []
    return row


class LessonValidationTests(unittest.TestCase):
    def test_simple_lesson_accepts_brief_sections_without_sentence_breakdown(self):
        row = simple_lesson("s0")
        row["analysis"]["vocabulary"] = []
        self.assertEqual(ai.validate_batch({"segments": [row]}, [{"id": "s0"}], detailed=False), [row])
        with self.assertRaises(ValueError):
            ai.validate_batch({"segments": [row]}, [{"id": "s0"}], detailed=True)

    def test_simple_lesson_still_validates_content_and_limits_length(self):
        for field, value in (("meaning", " "), ("practice", None), ("grammar", [None]),
                             ("speech", "invalid"), ("sentence_parts", "invalid"),
                             ("vocabulary", [lesson("s0")["analysis"]["vocabulary"][0]] * 4)):
            row = simple_lesson("s0")
            row["analysis"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                ai.validate_batch({"segments": [row]}, [{"id": "s0"}], detailed=False)

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
        self.detailed_requests = []
        self.schemas = []
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
        detailed = kwargs["input_text"].startswith(ai.INSTRUCTIONS)
        instructions = ai.INSTRUCTIONS if detailed else ai.SIMPLE_INSTRUCTIONS
        self.assertTrue(kwargs["input_text"].startswith(instructions))
        request = json.loads(kwargs["input_text"][len(instructions):].strip())
        self.requests.append(request)
        self.detailed_requests.append(detailed)
        schema = Path(command[command.index("--output-schema") + 1])
        self.schemas.append(json.loads(schema.read_text()))
        rows = [(lesson if detailed else simple_lesson)(segment["id"]) for segment in request["requested"]]
        if len(self.requests) == self.fail_batch:
            rows[-1]["analysis"]["meaning"] = ""
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
        self.assertEqual(data["segments"][1]["analysis_version"], 1)
        self.assertEqual(self.detailed_requests, [False])
        self.assertEqual(self.schemas, [ai.SIMPLE_SCHEMA])
        backup = self.state / "backups" / "job-one" / "manifest.json"
        self.assertEqual(json.loads(backup.read_text()), original)

    def test_new_material_uses_simple_batches_and_resumes_without_regenerating(self):
        data = self.package(ai.SIMPLE_BATCH_SIZE + 2)
        for segment in data["segments"]:
            segment.update(analysis=None, zh="")
        save_package(self.folder, data)
        self.fail_batch = 2
        job = self.job()
        with self.assertRaises(ValueError):
            ai.analyze(self.folder, data, job, self.run_model)
        self.assertEqual(sum(s.get("analysis_version") == 1 for s in data["segments"]), ai.SIMPLE_BATCH_SIZE)
        self.assertTrue(all(not detailed for detailed in self.detailed_requests))
        self.assertIn("简单讲解", job.update.call_args.kwargs["message"])
        completed = copy.deepcopy(data["segments"][:ai.SIMPLE_BATCH_SIZE])
        self.fail_batch = None
        ai.analyze(self.folder, data, self.job("retry"), self.run_model)
        self.assertEqual(len(self.requests[-1]["requested"]), 2)
        self.assertEqual(data["segments"][:ai.SIMPLE_BATCH_SIZE], completed)
        self.assertTrue(all(s["analysis_version"] == 1 for s in data["segments"]))
        self.assertTrue(all("sentence_parts" not in s["analysis"] for s in data["segments"]))

    def test_fill_translation_preserves_existing_detailed_and_simple_lessons(self):
        data = self.package(3)
        data["segments"][0].update(analysis=lesson("s0")["analysis"], analysis_version=2)
        data["segments"][1]["analysis_version"] = 1
        for segment in data["segments"]:
            segment["zh"] = ""
        original = copy.deepcopy(data)
        save_package(self.folder, data)
        ai.analyze(self.folder, data, self.job(), self.run_model)
        for actual, previous in zip(data["segments"], original["segments"]):
            self.assertEqual(actual["analysis"], previous["analysis"])
            self.assertEqual(actual.get("analysis_version"), previous.get("analysis_version"))
            self.assertTrue(actual["zh"])
        self.assertEqual(self.detailed_requests, [False])
        ai.analyze(self.folder, data, self.job("already-complete"), self.run_model)
        self.assertEqual(len(self.requests), 1)

    def test_simple_lesson_upgrades_only_selected_sentence_then_stays_detailed(self):
        data = self.package(2)
        for segment in data["segments"]:
            segment.update(analysis=None, zh="")
        ai.analyze(self.folder, data, self.job(), self.run_model)
        untouched = copy.deepcopy(data["segments"][0])
        ai.analyze(self.folder, data, self.job("upgrade"), self.run_model, upgrade=True, segment_id="s1")
        self.assertEqual(self.detailed_requests, [False, True])
        self.assertEqual(self.schemas[-1], ai.SCHEMA)
        self.assertEqual([s["id"] for s in self.requests[-1]["requested"]], ["s1"])
        self.assertEqual(data["segments"][0], untouched)
        self.assertEqual(data["segments"][1]["analysis_version"], 2)
        self.assertIn("sentence_parts", data["segments"][1]["analysis"])
        ai.analyze(self.folder, data, self.job("fill"), self.run_model)
        ai.analyze(self.folder, data, self.job("repeat-upgrade"), self.run_model, upgrade=True, segment_id="s1")
        self.assertEqual(len(self.requests), 2)

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

    def test_parallel_batches_save_out_of_order_without_losing_lessons(self):
        data = self.package(ai.SIMPLE_BATCH_SIZE * 5 + 2)
        for segment in data["segments"]:
            segment.update(analysis=None, zh="")
        save_package(self.folder, data)
        original = copy.deepcopy(data)
        ready = threading.Barrier(4)
        second_saved = threading.Event()
        save_order = []
        real_save = save_package

        def run(command, job, **kwargs):
            request = json.loads(kwargs["input_text"][len(ai.SIMPLE_INSTRUCTIONS):].strip())
            first = int(request["requested"][0]["id"][1:])
            if first < ai.SIMPLE_BATCH_SIZE * 4:
                ready.wait(timeout=5)
            if first == 0:
                self.assertTrue(second_saved.wait(timeout=5))
            self.run_model(command, job, **kwargs)

        def save(folder, updated):
            real_save(folder, updated)
            save_order.append(sum(bool(s.get("analysis")) for s in updated["segments"]))
            if updated["segments"][ai.SIMPLE_BATCH_SIZE].get("analysis"):
                second_saved.set()

        with patch.object(ai, "SIMPLE_PARALLEL_THRESHOLD", 1), patch.object(ai, "save_package", side_effect=save):
            ai.analyze(self.folder, data, self.job(), run)
        self.assertEqual(save_order, sorted(save_order))
        self.assertTrue(all(s.get("analysis_version") == 1 and s.get("zh") for s in data["segments"]))
        self.assertEqual([s["id"] for s in data["segments"]], [s["id"] for s in original["segments"]])
        self.assertEqual(json.loads((self.folder / "manifest.json").read_text()), data)
        self.assertEqual(json.loads((self.state / "backups/job-one/manifest.json").read_text()), original)
        self.assertEqual(len({s["id"] for r in self.requests for s in r["requested"]}), len(data["segments"]))

    def test_parallel_failure_saves_other_finished_batches_and_retry_only_fills_missing(self):
        data = self.package(ai.SIMPLE_BATCH_SIZE * 4)
        for segment in data["segments"]:
            segment.update(analysis=None, zh="")
        save_package(self.folder, data)
        ready = threading.Barrier(4)

        def run(command, job, **kwargs):
            ready.wait(timeout=5)
            self.run_model(command, job, **kwargs)
            output = Path(command[command.index("--output-last-message") + 1])
            result = json.loads(output.read_text())
            if result["segments"][0]["id"] == "s0":
                result["segments"][0]["zh"] = ""
                output.write_text(json.dumps(result))

        with patch.object(ai, "SIMPLE_PARALLEL_THRESHOLD", 1), self.assertRaises(ValueError):
            ai.analyze(self.folder, data, self.job(), run)
        completed = copy.deepcopy(data["segments"][ai.SIMPLE_BATCH_SIZE:])
        self.assertTrue(all(s.get("analysis") for s in completed))
        self.assertTrue(all(not s.get("analysis") for s in data["segments"][:ai.SIMPLE_BATCH_SIZE]))
        self.requests.clear()
        ai.analyze(self.folder, data, self.job("retry"), self.run_model)
        self.assertEqual([s["id"] for r in self.requests for s in r["requested"]], [f"s{i}" for i in range(ai.SIMPLE_BATCH_SIZE)])
        self.assertEqual(data["segments"][ai.SIMPLE_BATCH_SIZE:], completed)

    def test_cancellation_stops_every_parallel_child_process(self):
        job = jobs.Job("analysis")
        errors = []

        def run(index):
            try:
                jobs.run([sys.executable, "-c", "import time; time.sleep(20)"], job,
                         logfile=self.state / f"child-{index}.log", quiet=True)
            except jobs.Cancelled:
                errors.append(index)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        with patch.dict(jobs.JOBS, {job.id: job}):
            try:
                for thread in threads:
                    thread.start()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with jobs.LOCK:
                        if len(job.processes) == 2:
                            break
                    time.sleep(.01)
                with jobs.LOCK:
                    processes = list(job.processes)
                self.assertEqual(len(processes), 2)
            finally:
                jobs.cancel(job.id)
                for thread in threads:
                    thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertEqual(sorted(errors), [0, 1])
        self.assertFalse(job.processes)

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
