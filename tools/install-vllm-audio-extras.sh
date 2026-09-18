#!/bin/bash
# Install vLLM's optional `audio` extra into the image, then prove it resolves.
#
# vLLM keeps audio decoding behind an extra (upstream PR #8063, prompted by issue #8030:
# librosa pulls soxr, which is LGPL, and that blocked installs at licence-strict sites).
# The upstream vllm/vllm-openai image has never installed it. vLLM registers
# /v1/audio/transcriptions (>= v0.7.3) and /v1/audio/translations (>= v0.9.2)
# regardless, so without the extra both routes answer 400 "Invalid or unsupported audio
# file" for every request, with `ImportError('Please install vllm[audio] ...')` in the
# engine log. Measured on a live instance running v0.29.0.
#
# The extra's MEMBERS change between engine versions -- v0.13 is librosa + soundfile +
# mistral_common[audio], v0.28+ is av + scipy + soundfile + soxr + mistral_common[audio],
# and the decode path changed with them (librosa.load, then soundfile -> torchcodec ->
# PyAV). A hardcoded package list is therefore wrong for some tag this same Dockerfile
# builds: pinning the v0.29 list would leave v0.13 without librosa, i.e. still broken,
# while looking fixed. So the list is read from the installed vLLM's own metadata.
#
# Then every requirement is imported. Installing is not resolving (the lesson L056
# records for llama.cpp's CUDA backend): a wheel that unpacks but cannot load leaves the
# route failing in exactly the way this script exists to prevent, and a build that only
# ran `pip install` would not notice.
set -euo pipefail

PY="${PY:-python3}"

reqs="$("$PY" - <<'PYEOF'
import importlib.metadata as md

out = []
for raw in md.distribution("vllm").metadata.get_all("Requires-Dist") or []:
    if "extra ==" in raw:
        marker = raw.split("extra ==")[1].strip().strip("\"' ;")
        if marker == "audio":
            out.append(raw.split(";")[0].strip())
print(" ".join(out))
PYEOF
)"

if [[ -z "${reqs}" ]]; then
    echo "ERROR: vLLM declares no 'audio' extra -- the metadata layout changed, or vllm" >&2
    echo "       is not installed. Refusing to ship an image whose audio routes 400." >&2
    exit 1
fi

echo "Installing vLLM audio extra: ${reqs}"
# shellcheck disable=SC2086  # reqs is a requirement list, word splitting is intended
uv pip install --system --no-cache-dir ${reqs}

"$PY" - <<'PYEOF'
import importlib
import importlib.metadata as md
import re
import sys

names = []
for raw in md.distribution("vllm").metadata.get_all("Requires-Dist") or []:
    if "extra ==" in raw and raw.split("extra ==")[1].strip().strip("\"' ;") == "audio":
        # "mistral_common[audio]>=1.8.5" -> "mistral_common"
        names.append(re.split(r"[\[<>=!~ ;]", raw.split(";")[0].strip())[0])

failed = []
for name in names:
    module = name.replace("-", "_")
    try:
        importlib.import_module(module)
    except Exception as exc:
        failed.append(f"{module}: {type(exc).__name__}: {exc}")

if failed:
    print("ERROR: vLLM's audio extra installed but does not import:", file=sys.stderr)
    for line in failed:
        print(f"  {line}", file=sys.stderr)
    sys.exit(1)

print("vLLM audio extra resolves: " + ", ".join(sorted(names)))
PYEOF
