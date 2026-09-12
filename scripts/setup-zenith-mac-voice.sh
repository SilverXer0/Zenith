#!/bin/zsh

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "This helper is for the Apple-Silicon Mac host."
  exit 1
fi

root="${0:A:h:h}"
voice_dir="$root/backend/.venv-voice"
voice_python="$voice_dir/bin/python"
python_bin="${PYTHON_BIN:-$(command -v python3 || true)}"

if [[ -z "$python_bin" || ! -x "$python_bin" ]]; then
  print -u2 "Python 3 was not found. Install Python 3.12 or newer, then run this helper again."
  exit 1
fi

if [[ ! -x "$voice_python" ]]; then
  print "Creating the isolated Zenith voice environment..."
  "$python_bin" -m venv "$voice_dir"
fi

if ! "$voice_python" -c 'import mlx_whisper' >/dev/null 2>&1; then
  print "Installing the local MLX Whisper adapter..."
  "$voice_python" -m pip install mlx-whisper
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  print -u2 "ffmpeg is still missing. Install it with: brew install ffmpeg"
  exit 1
fi

print "Local voice input is ready."
print "Restart Zenith with: zsh ./scripts/start-zenith-mac.sh"
print "The launcher will detect this environment automatically."
