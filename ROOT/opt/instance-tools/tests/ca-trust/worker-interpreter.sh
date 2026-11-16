#!/bin/bash
# Sourced by each engine's serverless worker test, after the worker is serving (ADR 0058).
#
# The image installs the Python 3.10 the worker bootstrap asks for (lib/worker-python.sh),
# so the bootstrap should build the worker environment on that interpreter. base/29-ca-trust
# can run before the bootstrap has fetched anything, so it cannot be the check that notices
# a bootstrap that stopped doing so. This one runs when the worker environment exists.
#
# It reports and never fails. If the worker's interpreter appeared after the image was built,
# it prints the same EXEMPT line base/29-ca-trust prints for the `worker-bootstrap` exemption,
# which the QA gate turns into a warning and a Slack message.
report_worker_interpreter() {
    local env_dir home real idir
    env_dir="${ENV_PATH:-${WORKSPACE_DIR:-${WORKSPACE:-/workspace}}/worker-env}"
    echo ""
    echo "  -- worker interpreter --"
    if [[ ! -f "${env_dir}/pyvenv.cfg" ]]; then
        echo "  no ${env_dir}/pyvenv.cfg: the worker environment is not where the bootstrap puts it by default; not checked"
        return 0
    fi
    home=$(sed -n 's/^home = //p' "${env_dir}/pyvenv.cfg" | head -1)
    real=$(readlink -f "${home}" 2>/dev/null || true)
    idir="${real%/bin}"
    echo "  worker environment ${env_dir} runs on ${idir:-unknown} (home = ${home})"
    if [[ -z "$idir" || ! -d "$idir" || ! -e /.env_hash ]]; then
        echo "  cannot compare it with the image build; not checked"
    elif [[ "$idir" -nt /.env_hash ]]; then
        echo "EXEMPT: ${TEST_NAME}: worker-bootstrap interpreter ${idir} [the worker bootstrap installed its own Python at boot instead of using the one this image ships]"
    else
        echo "  it predates the image build: the bootstrap reused the interpreter this image ships"
    fi
    return 0
}
