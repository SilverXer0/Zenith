import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen


class RuntimeTests(unittest.TestCase):
    def test_documented_uvicorn_factory_starts_without_ollama(self):
        with tempfile.TemporaryDirectory(prefix="zenith-uvicorn-test-") as data_dir:
            env = {**os.environ, "ZENITH_DATA_DIR": data_dir, "OLLAMA_URL": "http://127.0.0.1:1"}
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "backend.app:create_app", "--factory",
                "--host", "127.0.0.1", "--port", "0", "--no-access-log",
            ], cwd=Path(__file__).resolve().parents[2], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            lines = queue.Queue()
            def read_output():
                for line in process.stderr:
                    lines.put(line)
                lines.put(None)
            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            try:
                for _ in range(20):
                    line = lines.get(timeout=10)
                    self.assertIsNotNone(line, "Uvicorn exited before startup")
                    match = re.search(r"http://127\.0\.0\.1:(\d+)", line)
                    if match:
                        break
                else:
                    self.fail("Uvicorn did not report its listening address")
                with urlopen(f"http://127.0.0.1:{match[1]}/api/health", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(json.load(response), {"ok": True, "service": "zenith", "storage": "sqlite"})
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                reader.join(timeout=5)
                process.stderr.close()
