#!/bin/bash
# Test: a short GRPO run trains and writes results.json: Unsloth loads the model with vLLM
# generation on this GPU, TRL takes optimizer steps, and the LoRA saves. Import checks
# alone pass on stacks that fail at the first generate (a vLLM / driver mismatch does).
# TEST_TIMEOUT=1800
source "$(dirname "$0")/../lib.sh"

GRPO_DIR="${WORKSPACE:-/workspace}/grpo"
OUT="$(mktemp -d /tmp/grpo-qa.XXXXXX)"

[[ -f "${GRPO_DIR}/train_grpo.py" ]] || test_fail "${GRPO_DIR}/train_grpo.py missing (workspace sync did not run?)"
[[ -f "${GRPO_DIR}/grpo_quickstart.ipynb" ]] || test_fail "${GRPO_DIR}/grpo_quickstart.ipynb missing"

# The notebook's kernel and the script both run on /venv/main.
/venv/main/bin/python -c "import unsloth, vllm, trl" 2>&1 | tail -5
[[ ${PIPESTATUS[0]} -eq 0 ]] || test_fail "import unsloth, vllm, trl failed in /venv/main"

# Two steps, eight eval questions: enough to exercise generate + train + save, minutes not
# the default run's quarter hour. The model is the template's default.
(cd "${GRPO_DIR}" && /venv/main/bin/python train_grpo.py \
    --max-steps 2 --eval-samples 8 --output-dir "${OUT}") > "${OUT}.log" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
    tail -40 "${OUT}.log"
    test_fail "train_grpo.py exited ${rc}"
fi

[[ -f "${OUT}/results.json" ]] || { tail -40 "${OUT}.log"; test_fail "no results.json after a successful exit"; }
[[ -d "${OUT}/lora" ]] || test_fail "no lora/ saved"
/venv/main/bin/python - "${OUT}/results.json" <<'PY' || test_fail "results.json is missing the before/after accuracy"
import json, sys
r = json.load(open(sys.argv[1]))
print("  results:", {k: r[k] for k in r if k != "settings"})
assert "accuracy_before" in r and "accuracy_after" in r
PY

rm -rf "${OUT}" "${OUT}.log"
test_pass "GRPO trained 2 steps and saved a LoRA"
