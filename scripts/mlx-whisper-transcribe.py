"""Print a local Whisper transcript using Apple's MLX backend on Apple Silicon."""

import os
import sys


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        import mlx_whisper
    except ImportError:
        return 1

    model = (os.environ.get("ZENITH_WHISPER_MODEL") or "mlx-community/whisper-base-mlx").strip()
    language = (os.environ.get("ZENITH_WHISPER_LANGUAGE") or "").strip() or None
    try:
        result = mlx_whisper.transcribe(
            sys.argv[1],
            path_or_hf_repo=model,
            language=language,
            # None suppresses progress and diagnostic output so stdout stays a transcript-only channel.
            verbose=None,
        )
    except Exception:
        return 1
    text = result.get("text", "") if isinstance(result, dict) else ""
    if not isinstance(text, str) or not text.strip():
        return 1
    sys.stdout.write(text.strip()[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
