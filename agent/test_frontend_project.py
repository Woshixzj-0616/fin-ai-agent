"""运行真实前端模块的异步交互回归，不依赖 npm 或浏览器驱动。"""
import subprocess
import unittest
from pathlib import Path


class FrontendProjectTests(unittest.TestCase):
    def test_delayed_responses_and_run_switches(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test", "tests/frontend-project.test.mjs"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
