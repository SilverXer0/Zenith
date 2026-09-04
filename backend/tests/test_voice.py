import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.errors import ApiError
from backend.voice import LocalVoice, _audio_suffix


CREDENTIALS = {"displayName": "Voice user", "password": "Voice passphrase 2026!"}


class VoiceTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {})
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("ZENITH_STT_COMMAND", "ZENITH_STT_ARGS", "ZENITH_TTS_COMMAND", "ZENITH_TTS_ARGS",
                    "GOOGLE_CLIENT_SECRET"):
            os.environ.pop(key, None)
        temporary = tempfile.TemporaryDirectory(prefix="zenith-voice-test-")
        self.addCleanup(temporary.cleanup)
        self.app = create_app(Path(temporary.name))
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        setup = self.client.post("/api/auth/setup", json=CREDENTIALS)
        self.assertEqual(setup.status_code, 201)

    def configure_stt(self, code):
        os.environ["ZENITH_STT_COMMAND"] = sys.executable
        os.environ["ZENITH_STT_ARGS"] = json.dumps(["-c", code, "{input}"])

    def configure_tts(self, code):
        os.environ["ZENITH_TTS_COMMAND"] = sys.executable
        os.environ["ZENITH_TTS_ARGS"] = json.dumps(["-c", code, "{text}", "{output}"])

    def test_voice_is_authenticated_optional_and_independent_from_core(self):
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.get("/api/voice/status").status_code, 401)
        self.assertEqual(anonymous.post("/api/voice/transcribe", content=b"audio").status_code, 401)
        self.assertEqual(anonymous.post("/api/voice/speak", json={"text": "hello"}).status_code, 401)
        self.assertEqual(self.client.get("/api/voice/status").json(),
                         {"configured": False, "ttsConfigured": False})
        unconfigured = self.client.post("/api/voice/transcribe", content=b"audio",
                                        headers={"Content-Type": "audio/webm"})
        self.assertEqual(unconfigured.status_code, 409)
        self.assertEqual(unconfigured.json(), {"error": "Local speech-to-text is not configured."})
        self.assertEqual(self.client.post("/api/voice/speak", json={"text": "hello"}).status_code, 409)
        task = self.client.post("/api/tasks", json={"title": "Core remains available"})
        self.assertEqual(task.status_code, 201)

    def test_transcription_uses_a_bounded_temporary_file_and_sanitized_environment(self):
        os.environ["GOOGLE_CLIENT_SECRET"] = "must-not-reach-adapter"
        code = ("import os,pathlib,sys;"
                "p=pathlib.Path(sys.argv[1]);"
                "assert p.suffix=='.ogg';"
                "assert p.read_bytes()==b'test audio';"
                "assert 'GOOGLE_CLIENT_SECRET' not in os.environ;"
                "print('local transcript')")
        self.configure_stt(code)
        self.assertEqual(self.client.get("/api/voice/status").json(),
                         {"configured": True, "ttsConfigured": False})
        response = self.client.post("/api/voice/transcribe", content=b"test audio",
                                    headers={"Content-Type": "audio/ogg; codecs=opus"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"text": "local transcript"})

        self.configure_stt("print('x'*5000)")
        capped = self.client.post("/api/voice/transcribe", content=b"audio")
        self.assertEqual(len(capped.json()["text"]), 4000)
        self.configure_stt("pass")
        empty = self.client.post("/api/voice/transcribe", content=b"audio")
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json(), {"error": "Local speech-to-text returned no text."})

    def test_transcription_rejects_bad_inputs_configuration_and_process_failures(self):
        self.assertEqual(self.client.post("/api/voice/transcribe", content=b"").status_code, 400)
        with patch("backend.voice.MAX_RECORDING_BYTES", 5):
            too_large = self.client.post("/api/voice/transcribe", content=b"123456")
        self.assertEqual(too_large.status_code, 413)
        self.assertEqual(too_large.json(), {"error": "Voice recordings must be 15 MB or smaller."})

        for arguments in ("not-json", json.dumps([]), json.dumps(["no placeholder"]),
                          json.dumps(["{input}", 4])):
            os.environ["ZENITH_STT_COMMAND"] = sys.executable
            os.environ["ZENITH_STT_ARGS"] = arguments
            self.assertFalse(self.client.get("/api/voice/status").json()["configured"])
            self.assertEqual(self.client.post("/api/voice/transcribe", content=b"audio").status_code, 409)
        os.environ["ZENITH_STT_COMMAND"] = "zenith-command-that-does-not-exist"
        os.environ["ZENITH_STT_ARGS"] = json.dumps(["{input}"])
        self.assertFalse(self.client.get("/api/voice/status").json()["configured"])
        self.assertEqual(self.client.post("/api/voice/transcribe", content=b"audio").status_code, 409)

        self.configure_stt("raise SystemExit('private failure detail')")
        failed = self.client.post("/api/voice/transcribe", content=b"audio")
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json(), {"error": "Local speech-to-text could not transcribe that recording."})
        self.assertNotIn("private failure detail", failed.text)
        self.configure_stt("import sys;sys.stdout.buffer.write(b'12345')")
        with patch("backend.voice.MAX_PROCESS_OUTPUT_BYTES", 4):
            oversized = self.client.post("/api/voice/transcribe", content=b"audio")
        self.assertEqual(oversized.status_code, 503)
        self.configure_stt("import time;time.sleep(1)")
        with patch("backend.voice.COMMAND_TIMEOUT_SECONDS", .05):
            timed_out = self.client.post("/api/voice/transcribe", content=b"audio")
        self.assertEqual(timed_out.status_code, 503)

    def test_speech_returns_wav_and_validates_input_and_adapter_output(self):
        os.environ["GOOGLE_CLIENT_SECRET"] = "must-not-reach-adapter"
        code = ("import os,pathlib,sys;"
                "assert 'GOOGLE_CLIENT_SECRET' not in os.environ;"
                "pathlib.Path(sys.argv[2]).write_bytes(b'RIFF'+sys.argv[1].encode()+b'WAVE')")
        self.configure_tts(code)
        self.assertEqual(self.client.get("/api/voice/status").json(),
                         {"configured": False, "ttsConfigured": True})
        response = self.client.post("/api/voice/speak", json={"text": "  Hello from Zenith  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(int(response.headers["content-length"]), len(response.content))
        self.assertEqual(response.content, b"RIFFHello from ZenithWAVE")
        self.assertEqual(response.headers["cache-control"], "no-store")
        openapi = self.client.get("/openapi.json").json()
        self.assertIn("audio/ogg", openapi["paths"]["/api/voice/transcribe"]["post"]["requestBody"]["content"])
        self.assertIn("audio/wav", openapi["paths"]["/api/voice/speak"]["post"]["responses"]["200"]["content"])

        for body in ({}, {"text": " "}, {"text": "x" * 4001}, {"text": 4},
                     {"text": "hello", "extra": True}, {"text": "bad\0text"}):
            self.assertEqual(self.client.post("/api/voice/speak", json=body).status_code, 400)
        self.configure_tts("import pathlib,sys;pathlib.Path(sys.argv[2]).write_bytes(b'')")
        self.assertEqual(self.client.post("/api/voice/speak", json={"text": "hello"}).status_code, 503)
        self.configure_tts("import pathlib,sys;pathlib.Path(sys.argv[2]).write_bytes(b'12345')")
        with patch("backend.voice.MAX_SPEECH_BYTES", 4):
            oversized = self.client.post("/api/voice/speak", json={"text": "hello"})
        self.assertEqual(oversized.status_code, 503)

    def test_busy_guards_and_audio_suffixes(self):
        self.configure_stt("print('text')")
        voice = LocalVoice()
        voice._stt_lock.acquire()
        self.addCleanup(voice._stt_lock.release)
        with self.assertRaises(ApiError) as busy:
            voice.transcribe(b"audio", ".webm")
        self.assertEqual((busy.exception.status, busy.exception.message),
                         (409, "Local speech-to-text is already busy."))
        self.configure_tts("import pathlib,sys;pathlib.Path(sys.argv[2]).write_bytes(b'audio')")
        speech = LocalVoice()
        speech._tts_lock.acquire()
        self.addCleanup(speech._tts_lock.release)
        with self.assertRaises(ApiError) as tts_busy:
            speech.synthesize("hello")
        self.assertEqual((tts_busy.exception.status, tts_busy.exception.message),
                         (409, "Local text-to-speech is already busy."))
        self.assertEqual(_audio_suffix("audio/mp4"), ".m4a")
        self.assertEqual(_audio_suffix("application/octet-stream"), ".webm")


if __name__ == "__main__":
    unittest.main()
