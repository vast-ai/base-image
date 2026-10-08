#!/bin/bash

# Print the current environment as /etc/environment lines, one per variable (ADR 0052).
# Bash sources the file at boot and in every supervisor script and login shell, so a
# value must come back exactly as Docker passed it: nothing expanded, nothing run. The
# file is also read by pam_env (non-interactive SSH, sudo) and by linux-desktop's
# export_env.sh, which strip one pair of surrounding quotes and do nothing else. So
# each value gets the quoting every reader agrees on:
#   - no single quote: 'value' (literal to bash, and to the quote-stripping readers);
#   - a single quote but no $ ` " \ : "value" (also literal to all of them);
#   - a single quote with one of those, or a control character: a form only bash
#     reads correctly ('it'\''s' or $'...'). The other readers see the raw text; no
#     form serves both, and bash is what boots the instance.
# Names that are not shell identifiers cannot be sourced, so they are skipped.
_vast_dump_env() {
    local line name value
    # The replacement lives in a variable: in a double-quoted ${//} bash 4.2 and older
    # keep its backslashes, which would reopen the quote.
    local sq="'\\''"
    env -0 | grep -zEv "^(HOME=|SHLVL=)|CONDA" | while IFS= read -r -d '' line; do
        name=${line%%=*}
        value=${line#*=}
        [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [[ "$value" == *[[:cntrl:]]* ]]; then
            printf -v value '%q' "$value"
            printf '%s=%s\n' "$name" "$value"
        elif [[ "$value" != *"'"* ]]; then
            printf "%s='%s'\n" "$name" "$value"
        elif [[ "$value" != *[\$\`\"\\]* ]]; then
            printf '%s="%s"\n' "$name" "$value"
        else
            printf "%s='%s'\n" "$name" "${value//\'/$sq}"
        fi
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
    # $VAR in a template value used to be expanded here by accident; it is now kept as
    # written (ADR 0052). Say so where it would bite, naming the variable only: values
    # can be secrets.
    while IFS= read -r -d '' line; do
        if [[ "${line#*=}" =~ \$\{?[A-Za-z_] ]]; then
            echo "prep-env: ${line%%=*} contains \$NAME text; it is kept literally, not expanded (ADR 0052)"
        fi
    done < <(env -0)
fi

# Source the file at /etc/environment - We can now edit environment variables in a running instance
[[ "${export_env}" = "true" ]] && { set -a; . /etc/environment 2>/dev/null; . "${WORKSPACE}/.env" 2>/dev/null; set +a; }
