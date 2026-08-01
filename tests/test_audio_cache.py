import hashlib
import os
import tempfile
import types
import unittest
from unittest.mock import patch

import app


class AudioCacheTests(unittest.TestCase):
    def test_zero_byte_file_is_not_considered_cached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, 'sample.mp3')
            with open(audio_path, 'wb') as f:
                f.write(b'')
            self.assertFalse(app.is_valid_audio_cache(audio_path))

    def test_non_empty_file_is_considered_cached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, 'sample.mp3')
            with open(audio_path, 'wb') as f:
                f.write(b'abc')
            self.assertTrue(app.is_valid_audio_cache(audio_path))

    def test_tts_regenerates_zero_byte_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            text = '测试语音缓存'
            voice = 'zh-CN-XiaoxiaoNeural'
            cache_key = hashlib.md5(f'{text}_{voice}'.encode('utf-8')).hexdigest()
            audio_path = os.path.join(tmpdir, f'{cache_key}.mp3')
            with open(audio_path, 'wb') as f:
                f.write(b'')

            class FakeCommunicate:
                def __init__(self, text, voice):
                    self.text = text
                    self.voice = voice

                async def save(self, path):
                    with open(path, 'wb') as f:
                        f.write(b'ID3')

            fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
            with patch.object(app, 'AUDIO_DIR', tmpdir):
                with patch.dict('sys.modules', {'edge_tts': fake_edge_tts}):
                    with app.app.test_client() as client:
                        response = client.post('/api/tts', json={'text': text, 'voice': voice})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['cached'], False)
            self.assertTrue(app.is_valid_audio_cache(audio_path))


if __name__ == '__main__':
    unittest.main()
