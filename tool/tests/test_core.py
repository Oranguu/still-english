import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from captions import clean, read_cues, segment_cues
from packages import validate, safe_path, atomic_json
from jobs import normalize_url, choose_subtitle
from ai import validate_batch, ai_env


class CaptionTests(unittest.TestCase):
    def test_manual_subtitles_and_non_speech(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.vtt"
            path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n(gentle guitar music)\n\n00:00:03.000 --> 00:00:04.000\n- [Speaker] We can\n\n00:00:04.000 --> 00:00:06.000\nbuild a small robot.\n\n00:00:08.000 --> 00:00:09.000\n10,000.\n")
            rows = segment_cues(read_cues(path))
            self.assertEqual([s["en"] for s in rows], ["We can build a small robot.", "10,000."])
            self.assertEqual((rows[0]["start"], rows[0]["end"]), (3, 6))

    def test_rolling_captions_deduplicated_but_real_repetition_preserved(self):
        rows = segment_cues([
            {"start":0,"end":2,"text":"I want to"},
            {"start":1,"end":3,"text":"I want to learn."},
            {"start":4,"end":5,"text":"I want to learn."},
        ])
        self.assertEqual([s["en"] for s in rows], ["I want to learn.", "I want to learn."])

    def test_json3_word_offsets_and_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.json3"
            path.write_text(json.dumps({"events":[{"tStartMs":1000,"dDurationMs":2000,"segs":[{"utf8":"Hello"},{"utf8":" there.","tOffsetMs":500}]},{"tStartMs":6000,"dDurationMs":1000,"segs":[{"utf8":"Again."}]}]}))
            rows = segment_cues(read_cues(path))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]["en"],"Hello there.")
            self.assertEqual(rows[0]["start"],1)
            self.assertLess(rows[0]["end"],6)

    def test_sound_effects_removed(self):
        self.assertEqual(clean('(birds chirping) (group cheering)'),'')
        self.assertEqual(clean('♪ Oh, oh no ♪'),'')
        self.assertEqual(clean('- [Speaker] Let’s go!'),'Let’s go!')


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
