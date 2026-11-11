import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from captions import clean, read_cues, segment_cues, group_learning_segments, is_low_information
from packages import validate, safe_path, atomic_json
from jobs import normalize_url, choose_subtitle
from ai import validate_batch, ai_env


class CaptionTests(unittest.TestCase):
    def test_manual_subtitles_and_non_speech(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.vtt"
            path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n(gentle guitar music)\n\n00:00:03.000 --> 00:00:04.000\n- [Speaker] We can\n\n00:00:04.000 --> 00:00:06.000\nbuild a small robot.\n\n00:00:08.000 --> 00:00:09.000\n10,000.\n")
            rows = segment_cues(read_cues(path), compact=False)
            self.assertEqual([s["en"] for s in rows], ["We can build a small robot.", "10,000."])
            self.assertEqual((rows[0]["start"], rows[0]["end"]), (3, 6))
            self.assertEqual([s["en"] for s in segment_cues(read_cues(path))], ["We can build a small robot."])

    def test_rolling_captions_deduplicated_but_real_repetition_preserved(self):
        cues = [
            {"start":0,"end":2,"text":"I want to"},
            {"start":1,"end":3,"text":"I want to learn."},
            {"start":4,"end":5,"text":"I want to learn."},
        ]
        rows = segment_cues(cues, compact=False)
        self.assertEqual([s["en"] for s in rows], ["I want to learn.", "I want to learn."])
        self.assertEqual(" ".join(s["en"] for s in segment_cues(cues)), "I want to learn. I want to learn.")

    def test_json3_word_offsets_and_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.json3"
            path.write_text(json.dumps({"events":[{"tStartMs":1000,"dDurationMs":2000,"segs":[{"utf8":"Hello"},{"utf8":" there.","tOffsetMs":500}]},{"tStartMs":6000,"dDurationMs":1000,"segs":[{"utf8":"Again."}]}]}))
            rows = segment_cues(read_cues(path), compact=False)
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]["en"],"Hello there.")
            self.assertEqual(rows[0]["start"],1)
            self.assertLess(rows[0]["end"],6)

    def test_sound_effects_removed(self):
        self.assertEqual(clean('(birds chirping) (group cheering)'),'')
        self.assertEqual(clean('♪ Oh, oh no ♪'),'')
        self.assertEqual(clean('- [Speaker] Let’s go!'),'Let’s go!')

    def test_short_replies_join_following_meaningful_speech(self):
        cues = [
            {"start": 0, "end": 1, "text": "Yes."},
            {"start": 1.2, "end": 2, "text": "Thank you."},
            {"start": 2.3, "end": 6, "text": "I would like to borrow this book."},
        ]
        original = copy.deepcopy(cues)
        rows = segment_cues(cues)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["en"], "Yes. Thank you. I would like to borrow this book.")
        self.assertEqual((rows[0]["start"], rows[0]["end"]), (0, 6))
        self.assertEqual(rows[0]["source_ids"], ["s0001", "s0002", "s0003"])
        self.assertEqual(cues, original)
        self.assertEqual(len(segment_cues(cues, compact=False)), 3)

    def test_forward_merge_is_preferred_and_tail_reply_can_merge_backward(self):
        rows = [
            {"start": 0, "end": 3, "en": "We will meet outside the library."},
            {"start": 3.2, "end": 3.8, "en": "Okay."},
            {"start": 4, "end": 7, "en": "Please remember to bring your notebook."},
            {"start": 7.3, "end": 8, "en": "Thanks."},
        ]
        plan = group_learning_segments(rows)
        self.assertEqual(plan, {"groups": [[0], [1, 2, 3]], "skipped": []})

    def test_isolated_fillers_skip_exercises_but_not_complete_subtitles(self):
        cues = [
            {"start": 0, "end": .5, "text": "Um."},
            {"start": 10, "end": 11, "text": "Thank you!"},
            {"start": 20, "end": 23, "text": "We should check the weather before leaving."},
        ]
        raw = segment_cues(cues, compact=False)
        self.assertEqual(group_learning_segments(raw), {"groups": [[2]], "skipped": [0, 1]})
        self.assertEqual(len(raw), 3)
        self.assertEqual(len(segment_cues(cues)), 1)

    def test_short_instructions_questions_and_numbers_stay_in_full_subtitles(self):
        phrases = ["Call the doctor!", "Where are you?", "Don't move.", "10,000.", "Why?"]
        rows = [{"start": i * 20, "end": i * 20 + 1, "en": text} for i, text in enumerate(phrases)]
        self.assertEqual(group_learning_segments(rows), {"groups": [], "skipped": list(range(len(rows)))})
        cues = [{"start": s["start"], "end": s["end"], "text": s["en"]} for s in rows]
        self.assertEqual([s["en"] for s in segment_cues(cues, compact=False)], phrases)
        self.assertEqual(segment_cues(cues), [])
        self.assertFalse(is_low_information("No, call the doctor."))
        self.assertFalse(is_low_information("Thank you for helping my sister."))
        self.assertTrue(is_low_information("Oh, yes. Thank you so much!"))
        self.assertTrue(is_low_information("Thanks very much. I'm so sorry!"))

    def test_merging_respects_gap_duration_and_word_limits(self):
        for second in (
            {"start": 4, "end": 6, "en": "We should take the next train."},
            {"start": 1.2, "end": 14, "en": "We should take the next train."},
            {"start": 1.2, "end": 6, "en": " ".join(["word"] * 40) + "."},
        ):
            with self.subTest(second=second):
                rows = [{"start": 0, "end": 1, "en": "Yes."}, second]
                self.assertEqual(group_learning_segments(rows), {"groups": [[1]], "skipped": [0]})

    def test_grouping_is_idempotent_including_after_skipping_blocking_fillers(self):
        rows = [
            {"start": 0, "end": .2, "en": "Go home."},
            {"start": .3, "end": .5, "en": " ".join(["Yes."] * 40)},
            {"start": .6, "end": 3, "en": "There is heavy rain coming tonight."},
            {"start": 3.2, "end": 4, "en": "Okay."},
            {"start": 20, "end": 21, "en": "Why?"},
        ]
        original = copy.deepcopy(rows)
        plan = group_learning_segments(rows)
        self.assertEqual(plan, {"groups": [[2, 3]], "skipped": [0, 1, 4]})
        regrouped = [{"start": rows[members[0]]["start"], "end": rows[members[-1]]["end"],
                      "en": " ".join(rows[i]["en"] for i in members)} for members in plan["groups"]]
        self.assertEqual(group_learning_segments(regrouped), {"groups": [[0]], "skipped": []})
        self.assertEqual(rows, original)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.data={"schema_version":1,"id":"test-id","title":"Example","video":"video.mp4","segments":[{"id":"s1","start":1,"end":2,"en":"Hello.","zh":"你好。","analysis":None}]}

    def test_invalid_timing_rejected(self):
        for start,end in ((-1,1),(1,1),(1,float('nan')),(True,2)):
            data=copy.deepcopy(self.data);data['segments'][0].update(start=start,end=end)
            with self.assertRaises(ValueError):validate(data)
        data=copy.deepcopy(self.data);data['segments'].append({"id":"s2","start":1.5,"end":3,"en":"Overlap"})
        with self.assertRaises(ValueError):validate(data)

    def test_traversal_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for value in ('../escape','/etc/passwd','nested/../../escape','.secret','nested/../escape','a\\b'):
                with self.assertRaises(ValueError):safe_path(root,value)
            (root/'alias').symlink_to('/etc')
            with self.assertRaises(ValueError):safe_path(root,'alias/passwd')
            self.assertEqual(safe_path(root,'nested/video.mp4'),root/'nested/video.mp4')

    def test_missing_video_rejected_and_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.assertRaises(ValueError):validate(self.data,root)
            (root/'video.mp4').write_bytes(b'video')
            validate(self.data,root)
            atomic_json(root/'manifest.json',self.data)
            self.assertEqual(json.loads((root/'manifest.json').read_text()),self.data)


class PipelineTests(unittest.TestCase):
    def test_url_selection(self):
        self.assertEqual(normalize_url('https://youtu.be/TEST_VIDEO1?t=30'), 'https://www.youtube.com/watch?v=TEST_VIDEO1')
        for url in ('file:///tmp/a','http://localhost/video','https://youtube.com.evil.example/watch?v=TEST_VIDEO1','https://youtube.com/playlist?list=abc'):
            with self.assertRaises(ValueError):normalize_url(url)

    def test_manual_regional_captions_prioritized(self):
        lang,label=choose_subtitle({'subtitles':{'en-CA':[{}],'zh':[{}]},'automatic_captions':{'en':[{}]}})
        self.assertEqual(lang,'en-CA');self.assertIn('人工',label)
        self.assertEqual(choose_subtitle({'subtitles':{'danmaku':[],'zh':[]}})[0],None)

    def test_ai_cannot_drop_or_reorder_ids(self):
        with self.assertRaises(ValueError):validate_batch({'segments':[]},[{'id':'s1'}])
        with self.assertRaises(ValueError):validate_batch({'segments':[{'id':'s2'}]},[{'id':'s1'}])
        self.assertNotIn('OPENAI_API_KEY',ai_env())
        self.assertNotIn('CODEX_API_KEY',ai_env())


if __name__=='__main__':unittest.main()
