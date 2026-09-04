import os
import sys

from faster_whisper import WhisperModel


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: whisper-transcribe.py <audio-file>")

    model_name = os.environ.get("ZENITH_WHISPER_MODEL", "base.en")
    device = os.environ.get("ZENITH_WHISPER_DEVICE", "cuda")
    compute_type = os.environ.get("ZENITH_WHISPER_COMPUTE_TYPE", "float16")
    language = os.environ.get("ZENITH_WHISPER_LANGUAGE", "en") or None
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _ = model.transcribe(sys.argv[1], beam_size=5, language=language, condition_on_previous_text=False, vad_filter=True)
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    print(transcript)


if __name__ == "__main__":
    main()
