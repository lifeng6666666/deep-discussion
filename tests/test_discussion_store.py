import tempfile
import unittest
from unittest.mock import patch

import discussion_store


class DiscussionStorePodcastTests(unittest.TestCase):
    def test_podcast_script_is_persisted_and_reused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(discussion_store, 'DISCUSSIONS_DIR', tmpdir):
                discussion_id = discussion_store.create_discussion('测试问题')
                podcast_script = [
                    {'speaker': 'HOST', 'text': '欢迎收听本期播客'},
                    {'speaker': 'DeepSeek', 'text': '我们来讨论这个问题'}
                ]

                discussion_store.save_podcast_script(discussion_id, podcast_script)

                record = discussion_store.get_discussion(discussion_id)
                self.assertEqual(record['podcast_script'], podcast_script)
                self.assertEqual(discussion_store.get_podcast_script(discussion_id), podcast_script)


if __name__ == '__main__':
    unittest.main()
