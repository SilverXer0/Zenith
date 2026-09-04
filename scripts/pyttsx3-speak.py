import os
import sys

import pyttsx3


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: pyttsx3-speak.py <text> <output-wav>")

    engine = pyttsx3.init()
    engine.setProperty("rate", int(os.environ.get("ZENITH_TTS_RATE", "180")))
    engine.save_to_file(sys.argv[1], sys.argv[2])
    engine.runAndWait()


if __name__ == "__main__":
    main()
