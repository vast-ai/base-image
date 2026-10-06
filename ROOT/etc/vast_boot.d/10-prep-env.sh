#!/bin/bash

# Double-quote VALUE (of variable NAME) for /etc/environment so that sourcing
# expands only plain references to other variables, `$OTHER` and `${OTHER}`, and
# keeps everything else literal (ADR 0052): `;`, quotes, backticks, `$(...)` and
# every other `$` form. `\$` gives a literal `$`. A reference to NAME itself
# stays literal, because Docker has already replaced the value it would extend.
_vast_env_quote() {
    local name="$1" v="$2" out="" tok
    local plain='^[^\\"`$]+' ref='^\$(\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))'
    while [[ -n "$v" ]]; do
        if [[ "$v" =~ $plain ]]; then
            tok=${BASH_REMATCH[0]}; out+=$tok
        elif [[ "$v" == '\$'* ]]; then
            tok='\$'; out+=$tok
        elif [[ "$v" =~ $ref ]] && [[ "${BASH_REMATCH[2]}${BASH_REMATCH[3]}" != "$name" ]]; then
            tok=${BASH_REMATCH[0]}; out+=$tok
        else
            tok=${v:0:1}; out+="\\$tok"
        fi
        v=${v:${#tok}}
    done
    printf '"%s"' "$out"
}

# Print the current environment as /etc/environment lines, one per variable.
# A value with a control character (newline, tab) is written fully literal in
# bash's $'...' form, so it stays on one line. Names that are not shell
# identifiers cannot be sourced, so they are skipped.
_vast_dump_env() {
    local line name value
    env -0 | grep -zEv "^(HOME=|SHLVL=)|CONDA" | while IFS= read -r -d '' line; do
        name=${line%%=*}
        value=${line#*=}
        [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [[ "$value" == *[[:cntrl:]]* ]]; then
            printf -v value '%q' "$value"
        else
            value=$(_vast_env_quote "$name" "$value")
        fi
        printf '%s=%s\n' "$name" "$value"
    done
}

# Load the function only (the test sources the shipped file this way).
[[ -n "${_VAST_PREP_ENV_LIB_ONLY:-}" ]] && return 0

mkdir -p "${WORKSPACE}"
cd "${WORKSPACE}"

# Remove python/pip from the sys-venv shim so the image's own interpreters
# take over now that Vast bootstrapping is complete.
rm -f /opt/sys-venv/shim/python /opt/sys-venv/shim/python3*
rm -f /opt/sys-venv/shim/pip /opt/sys-venv/shim/pip3*

# Remove Jupyter from the portal config if no port or running in SSH only mode
if [[ -z "${VAST_TCP_PORT_8080}" ]] || { [[ -f /.launch ]] && ! grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; }; then
    PORTAL_CONFIG=$(echo "$PORTAL_CONFIG" | tr '|' '\n' | grep -vi jupyter | tr '\n' '|' | sed 's/|$//')
fi

# In entrypoint mode (no /.launch) the Vast controller strips Jupyter from
# PORTAL_CONFIG. Jupyter is a core service of our images and was never meant to
# be removed, so re-add it unless explicitly disabled (--no-force-jupyter) or
# there is no port 8080 to serve it on.
if [[ "${FORCE_JUPYTER,,}" != "false" ]] && [[ ! -f /.launch ]] && [[ -n "${VAST_TCP_PORT_8080}" ]] && ! grep -qi jupyter <<< "$PORTAL_CONFIG"; then
    PORTAL_CONFIG="${PORTAL_CONFIG:+${PORTAL_CONFIG}|}localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi

# Ensure correct port mappings for Jupyter when running in Jupyter launch mode
if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
    PORTAL_CONFIG="$(echo "$PORTAL_CONFIG" | sed 's#localhost:8080:18080#localhost:8080:8080#g')"
fi

# Set HuggingFace home
export HF_HOME=${HF_HOME:-${WORKSPACE}/.hf_home}
mkdir -p "$HF_HOME"

# Ensure environment contains instance ID (snapshot aware)
instance_identifier=$(echo "${CONTAINER_ID:-${VAST_CONTAINERLABEL:-${CONTAINER_LABEL:-}}}")
message="# Template controlled environment for C.${instance_identifier}"
if [[ -z "${instance_identifier:-}" ]] || ! grep -q "$message" /etc/environment; then
    echo "$message" > /etc/environment
    echo 'PATH="/opt/instance-tools/bin:/opt/sys-venv/shim:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' \
        >> /etc/environment
    _vast_dump_env >> /etc/environment
fi

# Source the file at /etc/environment - We can now edit environment variables in a running instance
[[ "${export_env}" = "true" ]] && { set -a; . /etc/environment 2>/dev/null; . "${WORKSPACE}/.env" 2>/dev/null; set +a; }
