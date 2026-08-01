import unittest
from unittest.mock import patch

from deep_discussion import generate_podcast_script


class PodcastScriptTests(unittest.TestCase):
    def test_generate_podcast_script_returns_none_when_all_models_fail(self):
        events = [
            {
                "type": "speech",
                "data": {
                    "model": "deepseek-ai/deepseek-v4-pro",
                    "role": "proposer",
                    "display_text": "这是一个测试方案"
                }
            },
            {
                "type": "final_solution",
                "data": {
                    "solution": "最终方案应该聚焦于可执行步骤。"
                }
            }
        ]

        with patch("deep_discussion.call_model", return_value="模型响应失败"):
            script = generate_podcast_script("如何更有效学习", events)

        self.assertIsNone(script)


if __name__ == "__main__":
    unittest.main()
