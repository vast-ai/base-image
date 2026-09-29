#!/bin/bash
# Test: an external image's /venv/main mirrors its declared engine (ADR 0048, L101).
#
# Read-only. uv does not count packages a venv only INHERITS as installed, so an
# inheriting /venv/main let every `uv pip install` lay a second torch under the engine.
# /venv/main is instead a per-file symlink mirror of the engine's site-packages, built into
# the image. This proves the instance got one that works: no directory links, nothing
# dangling, one installed copy per project, the engine importing from its own files through
# the venv, and uv seeing the engine (and torch) as installed.
source "$(dirname "$0")/../lib.sh"

engine="${VAST_ENGINE_PYTHON:-}"
[[ -n "$engine" ]] || test_skip "no VAST_ENGINE_PYTHON: not an external image (base/pytorch own their packages)"
[[ "$engine" != none ]] || test_skip "VAST_ENGINE_PYTHON=none: no Python engine, no mirror"

# Declared but not mirrored is a FAILURE, never a skip: verify reports the missing manifest.
if ! out=$(venv-mirror verify --venv /venv/main --engine-import "${VAST_ENGINE_IMPORT:-}" 2>&1); then
    echo "$out" | sed 's/^/  /'
    test_fail "venv-mirror verify failed on /venv/main"
fi
echo "  $out"

# The engine's launchers run through the venv, so what a user installs there reaches it.
# A user's own activated shell is what matters, so ask that shell.
for cmd in ${VAST_ENGINE_IMPORT:-}; do
    cmd="${cmd%%.*}"
    resolved=$(bash -c ". /venv/main/bin/activate && command -v $cmd" 2>/dev/null || true)
    [[ -n "$resolved" ]] || continue            # a module with no command (torch)
    head=$(head -1 "$resolved" 2>/dev/null || true)
    if [[ "$resolved" == /venv/main/bin/* || "$head" == "#!/venv/main/bin/python"* ]]; then
        echo "  $cmd: runs through /venv/main ($resolved)"
    else
        fail_later "launcher" "$cmd resolves to $resolved ($head), which bypasses /venv/main"
    fi
done
report_failures
test_pass "/venv/main mirrors ${engine}"
