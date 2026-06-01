#!/bin/bash

# Make the agent-facing capability surface discoverable:
#   1. /etc/vast_capabilities.json  — static snapshot for FS-only/offline agents
#      (live state is always available at the portal's /capabilities endpoint).
#   2. ${WORKSPACE}/{AGENTS.md,CLAUDE.md}  — symlinks to /etc/vast_agents/base.md,
#      so a coding agent (which lands in ${WORKSPACE} on login) finds the guide
#      under whichever filename convention it follows. base.md instructs the
#      agent to read every *.md in /etc/vast_agents/ (base + per-image files).

WORKSPACE="${WORKSPACE:-/workspace}"
PORTAL_PYTHON=/opt/portal-aio/venv/bin/python
CAP_JSON=/etc/vast_capabilities.json
AGENTS_BASE=/etc/vast_agents/base.md

# --- 1. Static capability snapshot (best-effort) ---
SNAPSHOT_PY='import json; from capabilities import assemble_static; print(json.dumps(assemble_static(), indent=2))'
if [[ -x "$PORTAL_PYTHON" ]]; then
    if ( cd /opt/portal-aio && "$PORTAL_PYTHON" -c "$SNAPSHOT_PY" > "$CAP_JSON" 2>/dev/null ); then
        echo "Wrote ${CAP_JSON}"
    else
        echo "WARNING: could not write ${CAP_JSON}"
    fi
fi

# --- 2. Symlink the agent guide into the workspace ---
# Only touch our own symlinks or absent paths — never a user's real AGENTS.md.
if [[ -f "$AGENTS_BASE" && -d "$WORKSPACE" ]]; then
    for name in AGENTS.md CLAUDE.md; do
        target="${WORKSPACE}/${name}"
        if [[ -L "$target" || ! -e "$target" ]]; then
            ln -sfn "$AGENTS_BASE" "$target" && echo "Linked ${target} -> ${AGENTS_BASE}"
        else
            echo "Leaving user-provided ${target} untouched"
        fi
    done
fi
