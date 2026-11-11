import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobs
import local_media
import packages
import transcribe


class LocalMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'tool' / '.state'
        patcher = patch.object(packages, 'ROOT', self.root)
        patcher.start(); self.addCleanup(patcher.stop)
        (self.root / 'source').mkdir()
        self.probe = {'duration': 12.5, 'video_index': 0, 'video_codec': 'h264', 'browser_video': True, 'audio_index': 1, 'audio_codec': 'aac', 'audio_tracks': 1}
        self.job = Mock()
        self.content = b'original synthetic video bytes'

    def uploaded(self, name='Example_movie.mp4', analyze=False):
        result = local_media.start_import({'name': name, 'size': len(self.content), 'analyze': analyze}, self.state)
        local_media.upload(result['id'], io.BytesIO(self.content), len(self.content), self.state)
        return result['id']

    def prepare(self, identifier, probe=None):
        with patch.object(local_media, 'probe', return_value=probe or self.probe):
            return local_media.prepare(identifier, self.state, self.job, Mock(side_effect=RuntimeError('no preview')), 'ffmpeg')

    def test_invalid_files_and_sizes_create_no_import(self):
        for name in ('../secret.mp4', '/tmp/video.mp4', 'a\\video.mp4', '.hidden.mp4', 'movie.srt', 'a\n.mp4'):
            with self.assertRaises(ValueError):local_media.start_import({'name': name, 'size': 12}, self.state)
        for size in (0, -1, True, '10', local_media.MAX_BYTES + 1):
            with self.assertRaises(ValueError):local_media.start_import({'name': 'movie.mp4', 'size': size}, self.state)
        self.assertFalse((self.state / 'local-imports').exists())

    def test_stream_copy_hash_and_interrupted_retry(self):
        identifier = local_media.start_import({'name': 'movie.mp4', 'size': len(self.content)}, self.state)['id']
        with self.assertRaises(ValueError):local_media.upload(identifier, io.BytesIO(b'partial'), len(self.content), self.state)
        folder, data = local_media.load_import(identifier, self.state)
        self.assertFalse(data['uploaded']);self.assertFalse((folder / 'video.part').exists())
        local_media.upload(identifier, io.BytesIO(self.content), len(self.content), self.state)
        folder, data = local_media.load_import(identifier, self.state)
        self.assertEqual(data['sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual((folder / 'video.mp4').read_bytes(), self.content)
        with self.assertRaises(ValueError):local_media.upload(identifier, io.BytesIO(b'changed'), len(self.content), self.state)
        self.assertEqual((folder / 'video.mp4').read_bytes(), self.content)

    def test_build_from_video_only_and_duplicate_preserves_existing_lesson(self):
        identifier = self.uploaded()
        work, _ = local_media.load_import(identifier, self.state)
        (work / 'untrusted.srt').write_text('Do not use this inaccurate subtitle.')
        folder, data = self.prepare(identifier)
        self.assertEqual((folder / 'video.mp4').read_bytes(), self.content)
        self.assertEqual(data['segments'], [])
        self.assertEqual(data['source_kind'], 'local')
        self.assertNotIn('source_url', data)
        self.assertEqual(list(folder.glob('*.srt')), [])
        data['segments'] = [{'id': 's1', 'start': 1, 'end': 2, 'en': 'Original lesson.', 'zh': '保留。', 'analysis_version': 2, 'analysis': {'meaning': 'Keep this.'}}]
        packages.save_package(folder, data)
        before = (folder / 'manifest.json').read_bytes()
        duplicate = self.uploaded('Different_name.mp4')
        other, restored = self.prepare(duplicate)
        self.assertEqual(other, folder)
        self.assertEqual(restored['segments'], data['segments'])
        self.assertEqual((folder / 'manifest.json').read_bytes(), before)

    def test_conversion_selects_audio_and_retains_original(self):
        identifier = self.uploaded('Example.mkv')
        details = self.probe | {'video_codec': 'hevc', 'browser_video': False, 'audio_codec': 'ac3', 'audio_index': 3, 'audio_tracks': 2}
        commands = []
        def run(command, *args, **kwargs):
            commands.append(command);Path(command[-1]).write_bytes(b'converted')
        with patch.object(local_media, 'probe', return_value=details):
            folder, data = local_media.prepare(identifier, self.state, self.job, run, 'ffmpeg')
        self.assertEqual((folder / 'original.mkv').read_bytes(), self.content)
        self.assertEqual((folder / 'video.mp4').read_bytes(), b'converted')
        self.assertIn('0:3', commands[0]);self.assertIn('libx264', commands[0])
        self.assertIn('yuv420p', commands[0])

    def test_failed_conversion_can_retry_without_losing_uploaded_video(self):
        identifier = self.uploaded('Example.webm')
        with patch.object(local_media, 'probe', return_value=self.probe | {'video_codec': 'vp9'}):
            with self.assertRaises(RuntimeError):
                local_media.prepare(identifier, self.state, self.job, Mock(side_effect=RuntimeError('interrupted')), 'ffmpeg')
        work, _ = local_media.load_import(identifier, self.state)
        self.assertEqual((work / 'video.webm').read_bytes(), self.content)
        self.assertEqual(list((self.root / 'source').glob('*/manifest.json')), [])

    def test_symlinks_and_import_ids_rejected(self):
        for value in ('../escape', 'bad', None):
            with self.assertRaises(ValueError):local_media.load_import(value, self.state)
        identifier = local_media.start_import({'name': 'movie.mp4', 'size': 1}, self.state)['id']
        folder, _ = local_media.load_import(identifier, self.state)
        outside = self.root / 'private.txt';outside.write_bytes(b'untouched')
        (folder / 'video.part').symlink_to(outside)
        with self.assertRaises(ValueError):local_media.upload(identifier, io.BytesIO(b'x'), 1, self.state)
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_probe_prefers_tagged_english_and_rejects_missing_audio(self):
        text = 'Duration: 00:18:42.50, start: 0.000\n Stream #0:0(und): Video: h264 (High), yuv420p(progressive)\n Stream #0:1(fra): Audio: aac\n Stream #0:3(eng): Audio: ac3\n'
        with patch.object(local_media.subprocess, 'run', return_value=Mock(stderr=text)):
            result = local_media.probe('movie', 'ffmpeg')
        self.assertEqual(result['audio_index'], 3);self.assertAlmostEqual(result['duration'], 1122.5)
        self.assertTrue(result['browser_video'])
        with patch.object(local_media.subprocess, 'run', return_value=Mock(stderr=text.replace('yuv420p', 'yuv420p10le'))):
            self.assertFalse(local_media.probe('movie', 'ffmpeg')['browser_video'])
        with patch.object(local_media.subprocess, 'run', return_value=Mock(stderr='Duration: 00:00:12.00\nStream #0:0: Video: h264')):
            with self.assertRaises(ValueError):local_media.probe('movie', 'ffmpeg')

    def test_old_transcripts_do_not_break_job_listing(self):
        directory = self.state / 'jobs';directory.mkdir(parents=True)
        (directory / 'old-transcript.json').write_text('[{"start":1,"text":"speech"}]')
        (directory / 'task.json').write_text(json.dumps({'id': 'task', 'kind': 'local', 'status': 'running'}))
        with patch.object(jobs, 'STATE', self.state), patch.object(jobs, 'JOBS', {}):
            result = jobs.snapshots()
        self.assertEqual(len(result), 1);self.assertEqual(result[0]['status'], 'interrupted')


class TranscriptionBoundaryTests(unittest.TestCase):
    def test_voice_detector_keeps_context_for_quiet_short_dialogue(self):
        spans=[{'start':94*16000,'end':98*16000},{'start':120*16000,'end':125*16000}]
        self.assertEqual(transcribe.context_clips(spans,150),[64,150])
        self.assertEqual(transcribe.context_clips([],150),[0,150])

    def test_padded_windows_cover_movie_once(self):
        self.assertEqual(list(transcribe.windows(620)), [(0, 300, 0, 305), (300, 600, 295, 605), (600, 620, 595, 620)])

    def test_word_ownership_at_chunk_boundary_and_silence_filter(self):
        first = [{'start': 299, 'end': 301, 'text': 'boundary', 'words': [{'start': 299, 'end': 301, 'word': 'Hello.'}]}]
        second = [{'start': 4, 'end': 6, 'text': 'boundary', 'words': [{'start': 4, 'end': 6, 'word': 'Hello.'}]}]
        rows = transcribe.word_rows(first, 0, 0, 300, 620) + transcribe.word_rows(second, 295, 300, 600, 620)
        self.assertEqual(rows, [{'start': 299, 'end': 301, 'text': 'Hello.'}])
        silence = [{'start': 0, 'end': 1, 'text': 'fake', 'no_speech_prob': .9, 'avg_logprob': -2}]
        self.assertEqual(transcribe.word_rows(silence, 0, 0, 300, 620), [])


if __name__ == '__main__':
    unittest.main()
