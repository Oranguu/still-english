import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai import needs_analysis
from packages import playback_segments, validate
from regroup import regroup_package


def lesson(label):
    return {
        "meaning": f"{label} meaning",
        "paraphrase": f"{label} paraphrase",
        "practice": f"{label} practice",
        "vocabulary": [{"term": label, "meaning": "a useful word", "example": "We can learn together."}],
        "phrases": [{"term": f"{label} phrase", "meaning": "a useful phrase", "example": "Please come with us."}],
        "sentence_parts": [{"chunk": label, "meaning": "one part", "role": "main idea"}],
        "grammar": [{"pattern": f"{label} pattern", "explanation": "Use a subject and a verb."}],
        "speech": [{"text": label, "tip": "Notice the unstressed words."}],
    }


def segment(identifier, start, end, en, label):
    return {"id": identifier, "start": start, "end": end, "en": en, "zh": f"{label} 翻译",
            "analysis": lesson(label), "analysis_version": 1}


def sample_package():
    return {
        "schema_version": 1, "id": "sample", "title": "Sample video", "video": "video.mp4",
        "creator": "Sample creator", "source_kind": "local", "duration": 40,
        "created_at": "2026-01-01T00:00:00Z", "caption_source": "sample captions",
        "custom_metadata": {"labels": ["practice"], "language": "en"}, "status": "ready",
        "segments": [
            segment("old-a", 0, .5, "Yes.", "filler"),
            segment("old-b", 1, 4, "We should bring our books to school.", "books"),
            segment("old-c", 10, 14, "The library will be open tomorrow morning.", "library"),
            segment("old-d", 30, 31, "Thank you.", "thanks"),
        ],
    }


class RegroupTests(unittest.TestCase):
    def test_regroup_preserves_input_raw_captions_and_package_metadata(self):
        original = sample_package()
        snapshot = copy.deepcopy(original)
        updated, mapping, summary = regroup_package(original)
        self.assertEqual(original, snapshot)
        self.assertEqual(updated["subtitle_segments"], [
            {key: row[key] for key in ("id", "start", "end", "en", "zh")} for row in original["segments"]
        ])
        self.assertEqual(playback_segments(updated), updated["subtitle_segments"])
        for field in original.keys() - {"segments", "status"}:
            self.assertEqual(updated[field], original[field])
        self.assertEqual(summary, {"before": 4, "after": 2, "merged_groups": 1, "skipped": 1, "changed": True})
        self.assertEqual(updated["segmentation_summary"], summary)
        self.assertEqual(mapping["old-d"], None)
        self.assertEqual(validate(updated), updated)
        # Nested output data is detached from both the input and raw snapshots.
        updated["custom_metadata"]["labels"].append("changed")
        updated["segments"][1]["analysis"]["vocabulary"][0]["term"] = "changed"
        updated["subtitle_segments"][0]["zh"] = "changed"
        self.assertEqual(original, snapshot)

    def test_merged_ids_are_stable_distinct_and_never_recycle_old_ids(self):
        original = sample_package()
        updated, mapping, _ = regroup_package(original)
        merged = updated["segments"][0]
        self.assertTrue(merged["id"].startswith("g-"))
        self.assertNotIn(merged["id"], {row["id"] for row in original["segments"]})
        self.assertEqual(mapping["old-a"], merged["id"])
        self.assertEqual(mapping["old-b"], merged["id"])
        self.assertEqual(merged["source_ids"], ["old-a", "old-b"])
        self.assertEqual(merged["en"], "Yes. We should bring our books to school.")
        self.assertEqual((merged["start"], merged["end"]), (0, 4))
        self.assertEqual(regroup_package(copy.deepcopy(original))[0], updated)
        for field, value in (("id", "different-id"), ("end", 4.2), ("en", "We should bring our notebooks to school.")):
            changed = copy.deepcopy(original)
            changed["segments"][1][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(regroup_package(changed)[0]["segments"][0]["id"], merged["id"])

    def test_id_collision_fails_without_replacing_an_existing_sentence(self):
        original = sample_package()
        generated_id = regroup_package(original)[0]["segments"][0]["id"]
        original["segments"][2]["id"] = generated_id
        snapshot = copy.deepcopy(original)
        with self.assertRaisesRegex(ValueError, "编号冲突"):
            regroup_package(original)
        self.assertEqual(original, snapshot)

    def test_unchanged_sentence_preserves_id_detailed_lesson_and_metadata(self):
        original = sample_package()
        original["segments"][2].update(analysis_version=2, analysis_origin="original", custom={"flag": True})
        updated, mapping, _ = regroup_package(original)
        expected = copy.deepcopy(original["segments"][2])
        expected["source_ids"] = ["old-c"]
        self.assertEqual(updated["segments"][1], expected)
        self.assertEqual(mapping["old-c"], "old-c")
        self.assertFalse(needs_analysis(updated["segments"][1], upgrade=True))

    def test_merged_lesson_reuses_useful_content_and_excludes_filler_lesson(self):
        original = sample_package()
        updated, _, _ = regroup_package(original)
        merged = updated["segments"][0]
        useful = original["segments"][1]
        for field in ("meaning", "paraphrase", "practice"):
            self.assertEqual(merged["analysis"][field], useful["en"] + "\n" + useful["analysis"][field])
            self.assertNotIn("filler", merged["analysis"][field])
        for field in ("vocabulary", "phrases", "sentence_parts", "grammar", "speech"):
            self.assertEqual(merged["analysis"][field], useful["analysis"][field])
        self.assertEqual(merged["zh"], "filler 翻译 books 翻译")
        self.assertEqual(merged["analysis_origin"], "merged_existing")
        self.assertEqual(merged["analysis_version"], 1)
        self.assertFalse(needs_analysis(merged))
        self.assertTrue(needs_analysis(merged, upgrade=True))
        # A missing filler-only lesson need not block reuse of useful lessons.
        original["segments"][0]["analysis"] = None
        self.assertEqual(regroup_package(original)[0]["segments"][0]["analysis"], merged["analysis"])

    def test_merged_lesson_keeps_substantive_parts_in_order_and_deduplicates(self):
        original = sample_package()
        original["segments"] = [
            segment("short", 0, 1, "Bring your book.", "shared"),
            segment("long", 1.1, 4, "We will read together after school.", "shared"),
        ]
        merged = regroup_package(original)[0]["segments"][0]
        self.assertEqual(merged["analysis"]["meaning"], "Bring your book.\nshared meaning\n\nWe will read together after school.\nshared meaning")
        for field in ("vocabulary", "phrases", "sentence_parts", "grammar", "speech"):
            self.assertEqual(len(merged["analysis"][field]), 1)
        validate(regroup_package(original)[0])

    def test_missing_substantive_analysis_stays_pending(self):
        for incomplete in (None, {}, {"meaning": ""}):
            with self.subTest(incomplete=incomplete):
                original = sample_package()
                original["segments"][1]["analysis"] = incomplete
                updated, _, _ = regroup_package(original)
                merged = updated["segments"][0]
                self.assertIsNone(merged["analysis"])
                self.assertTrue(needs_analysis(merged))
                self.assertEqual(updated["status"], "partial")
                self.assertEqual(updated["subtitle_segments"][1]["zh"], original["segments"][1]["zh"])
                validate(updated)

    def test_missing_translation_does_not_invent_a_complete_merged_translation(self):
        original = sample_package()
        original["segments"][0]["zh"] = ""
        updated, _, _ = regroup_package(original)
        self.assertEqual(updated["segments"][0]["zh"], "")
        self.assertTrue(needs_analysis(updated["segments"][0]))
        self.assertEqual(updated["status"], "partial")
        self.assertEqual(updated["subtitle_segments"][1]["zh"], original["segments"][1]["zh"])
        validate(updated)

    def test_existing_original_subtitles_and_source_references_survive(self):
        original = sample_package()
        original["segmentation_version"] = 1
        original["subtitle_segments"] = [{key: row[key] for key in ("id", "start", "end", "en", "zh")} for row in original["segments"]]
        for index, row in enumerate(original["segments"]):
            row["id"] = f"prior-{index}"
            row["source_ids"] = [original["subtitle_segments"][index]["id"]]
        updated, mapping, _ = regroup_package(original)
        self.assertEqual(updated["subtitle_segments"], original["subtitle_segments"])
        self.assertEqual(updated["segments"][0]["source_ids"], ["old-a", "old-b"])
        self.assertEqual(updated["segments"][1]["source_ids"], ["old-c"])
        self.assertEqual(mapping["prior-3"], None)
        self.assertEqual(playback_segments(updated), original["subtitle_segments"])
        validate(updated)

    def test_repeated_regroup_is_a_noop_with_independent_copies(self):
        first, _, _ = regroup_package(sample_package())
        snapshot = copy.deepcopy(first)
        second, mapping, summary = regroup_package(first)
        self.assertEqual(second, first)
        self.assertEqual(mapping, {row["id"]: row["id"] for row in first["segments"]})
        self.assertEqual(summary, {"before": 2, "after": 2, "merged_groups": 0, "skipped": 0, "changed": False})
        second["segments"][0]["analysis"]["vocabulary"][0]["term"] = "changed"
        self.assertEqual(first, snapshot)

    def test_all_short_exercises_disappear_but_every_caption_is_retained(self):
        original = sample_package()
        original["segments"] = [
            segment("a", 0, 1, "Yes.", "yes"),
            segment("b", 10, 11, "Call the doctor!", "doctor"),
            segment("c", 20, 21, "10,000.", "number"),
        ]
        updated, mapping, summary = regroup_package(original)
        self.assertEqual(updated["segments"], [])
        self.assertEqual(mapping, {"a": None, "b": None, "c": None})
        self.assertEqual(summary, {"before": 3, "after": 0, "merged_groups": 0, "skipped": 3, "changed": True})
        self.assertEqual(len(playback_segments(updated)), 3)
        self.assertEqual(updated["status"], "ready")
        validate(updated)


if __name__ == "__main__":
    unittest.main()
