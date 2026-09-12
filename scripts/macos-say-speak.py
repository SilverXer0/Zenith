"""Create a WAV file from text using macOS's built-in local speech tools."""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    text, output_name = sys.argv[1:]
    output = Path(output_name)
    aiff = output.with_suffix(".aiff")
    try:
        subprocess.run(
            ["/usr/bin/say", "-o", str(aiff), text],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@22050", "-c", "1", str(aiff), str(output)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    finally:
        try:
            aiff.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
