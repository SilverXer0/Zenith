"""Optional local speech adapters with bounded process and temporary-file boundaries."""

import json
import os
import stat
import subprocess
import tempfile
import threading
from pathlib import Path
from shutil import which

from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response

from .errors import ApiError


MAX_RECORDING_BYTES = 15 * 1024 * 1024
MAX_SPEECH_BYTES = 64 * 1024 * 1024
MAX_PROCESS_OUTPUT_BYTES = 2 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 120
MAX_CONFIG_BYTES = 32 * 1024
MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 4096

AUDIO_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
}
SUPPORTED_SUFFIXES = frozenset(AUDIO_SUFFIXES.values())

RECORDING_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            mime_type: {"schema": {"type": "string", "format": "binary"}}
            for mime_type in ("audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav")
        },
    },
}

SERVER_ONLY_ENVIRONMENT = {
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "GOOGLE_TOKEN_URL", "GOOGLE_CALENDAR_URL", "OLLAMA_URL", "OLLAMA_MODEL",
    "OLLAMA_KEEP_ALIVE", "OLLAMA_NO_CLOUD", "ZENITH_DATA_DIR", "ZENITH_COOKIE_SECURE",
    "ZENITH_STT_COMMAND", "ZENITH_STT_ARGS", "ZENITH_TTS_COMMAND", "ZENITH_TTS_ARGS",
}


class WavResponse(Response):
    media_type = "audio/wav"


class VoiceProcessError(Exception):
    pass


def _adapter_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in SERVER_ONLY_ENVIRONMENT:
        environment.pop(key, None)
    return environment


def _audio_suffix(content_type: str | None) -> str:
    mime_type = (content_type or "").split(";", 1)[0].strip().lower()
    return AUDIO_SUFFIXES.get(mime_type, ".webm")


def _encoded_size(value: str) -> int | None:
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        return None


def _invalid_argument(value) -> bool:
    if not isinstance(value, str) or "\0" in value:
        return True
    size = _encoded_size(value)
    return size is None or size > MAX_ARGUMENT_BYTES


def _run_adapter(command: str, arguments: list[str]) -> bytes:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            result = subprocess.run(
                [command, *arguments], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                timeout=COMMAND_TIMEOUT_SECONDS, check=False, shell=False,
                env=_adapter_environment(),
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            raise VoiceProcessError() from None
        stdout.seek(0, os.SEEK_END)
        stderr.seek(0, os.SEEK_END)
        if (result.returncode != 0 or stdout.tell() > MAX_PROCESS_OUTPUT_BYTES
                or stderr.tell() > MAX_PROCESS_OUTPUT_BYTES):
            raise VoiceProcessError()
        stdout.seek(0)
        return stdout.read(MAX_PROCESS_OUTPUT_BYTES + 1)


class LocalVoice:
    def __init__(self):
        self._stt_lock = threading.Lock()
        self._tts_lock = threading.Lock()

    def status(self) -> dict:
        return {"configured": self._valid_configuration("stt"),
                "ttsConfigured": self._valid_configuration("tts")}

    def _valid_configuration(self, kind: str) -> bool:
        try:
            self._configuration(kind)
            return True
        except ApiError:
            return False

    def _configuration(self, kind: str) -> tuple[str, list[str]]:
        speech_to_text = kind == "stt"
        prefix = "ZENITH_STT" if speech_to_text else "ZENITH_TTS"
        label = "speech-to-text" if speech_to_text else "text-to-speech"
        command = (os.environ.get(f"{prefix}_COMMAND") or "").strip()
        raw_arguments = os.environ.get(f"{prefix}_ARGS") or ""
        if not command or not raw_arguments:
            raise ApiError(409, f"Local {label} is not configured.")
        invalid = ApiError(409, f"Local {label} configuration is invalid.")
        command_size = _encoded_size(command)
        config_size = _encoded_size(raw_arguments)
        if (command_size is None or command_size > MAX_ARGUMENT_BYTES or "\0" in command
                or config_size is None or config_size > MAX_CONFIG_BYTES):
            raise invalid
        try:
            arguments = json.loads(raw_arguments)
        except (json.JSONDecodeError, UnicodeError):
            raise invalid from None
        placeholders = ("{input}",) if speech_to_text else ("{text}", "{output}")
        if (not isinstance(arguments, list) or not arguments or len(arguments) > MAX_ARGUMENTS
                or any(_invalid_argument(argument) for argument in arguments)
                or any(placeholder not in arguments for placeholder in placeholders)):
            raise invalid
        try:
            executable = which(command)
        except (OSError, ValueError):
            raise invalid from None
        if not executable or Path(executable).suffix.lower() in (".bat", ".cmd"):
            raise invalid
        return executable, arguments

    async def read_recording(self, request: Request) -> tuple[bytes, str]:
        declared_size = request.headers.get("content-length")
        try:
            if declared_size is not None and int(declared_size) > MAX_RECORDING_BYTES:
                raise ApiError(413, "Voice recordings must be 15 MB or smaller.")
        except ValueError:
            pass
        chunks = []
        size = 0
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_RECORDING_BYTES:
                    raise ApiError(413, "Voice recordings must be 15 MB or smaller.")
                chunks.append(chunk)
        except ClientDisconnect:
            raise ApiError(400, "Voice recording was interrupted.") from None
        if not size:
            raise ApiError(400, "Voice recording was empty.")
        return b"".join(chunks), _audio_suffix(request.headers.get("content-type"))

    def transcribe(self, recording: bytes, suffix: str) -> str:
        command, arguments = self._configuration("stt")
        if not self._stt_lock.acquire(blocking=False):
            raise ApiError(409, "Local speech-to-text is already busy.")
        try:
            with tempfile.TemporaryDirectory(prefix="zenith-voice-") as directory:
                suffix = suffix if suffix in SUPPORTED_SUFFIXES else ".webm"
                input_path = Path(directory) / f"recording{suffix}"
                input_path.write_bytes(recording)
                output = _run_adapter(command, [argument.replace("{input}", str(input_path))
                                                for argument in arguments])
            try:
                text = output.decode("utf-8").strip()
            except UnicodeDecodeError:
                raise VoiceProcessError() from None
            if not text:
                raise ApiError(400, "Local speech-to-text returned no text.")
            return text[:4000]
        except ApiError:
            raise
        except (OSError, VoiceProcessError):
            raise ApiError(503, "Local speech-to-text could not transcribe that recording.") from None
        finally:
            self._stt_lock.release()

    def synthesize(self, text: str) -> bytes:
        command, arguments = self._configuration("tts")
        if not self._tts_lock.acquire(blocking=False):
            raise ApiError(409, "Local text-to-speech is already busy.")
        try:
            with tempfile.TemporaryDirectory(prefix="zenith-speech-") as directory:
                output_path = Path(directory) / "speech.wav"
                command_arguments = [argument.replace("{text}", text).replace("{output}", str(output_path))
                                     for argument in arguments]
                _run_adapter(command, command_arguments)
                metadata = os.lstat(output_path)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > MAX_SPEECH_BYTES:
                    raise VoiceProcessError()
                with output_path.open("rb") as output:
                    audio = output.read(MAX_SPEECH_BYTES + 1)
                if not audio or len(audio) > MAX_SPEECH_BYTES:
                    raise VoiceProcessError()
                return audio
        except ApiError:
            raise
        except (OSError, VoiceProcessError):
            raise ApiError(503, "Local text-to-speech could not create audio.") from None
        finally:
            self._tts_lock.release()
