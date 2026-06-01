#!/bin/bash

# Make the agent-facing capability surface discoverable:
#   1. /etc/vast_capabilities.json  — static snapshot for FS-only/offline agents
#      (live state is always available at the portal's /capabilities endpoint).
#   2. {AGENTS.md,CLAUDE.md} symlinks to /etc/vast_agents/base.md, dropped where
#      an agent lands: ${WORKSPACE} (interactive shells cd here) and the home
#      dirs (a one-shot `ssh host cmd` lands in $HOME). It's found under whichever
#      filename/cwd an agent uses. base.md instructs reading every *.md in
#      /etc/vast_agents/ (base + per-image files).
#   3. A pointer line on the SSH banner — shown on every connection, so even a
#      non-interactive agent that never reads the landing dir sees it.

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

# --- 2. Symlink the agent guide where agents land ---
# ${WORKSPACE} (interactive shells cd here) plus the home dirs (a one-shot
# `ssh host cmd` lands in $HOME). Create only when absent/dangling, or refresh a
# link that is already ours — never replace a user's real file or their own symlink.
if [[ -f "$AGENTS_BASE" ]]; then
    for dir in "$WORKSPACE" /root /home/user; do
        [[ -d "$dir" ]] || continue
        for name in AGENTS.md CLAUDE.md; do
            target="${dir}/${name}"
            if [[ ! -e "$target" ]] || [[ "$(readlink "$target" 2>/dev/null)" == "$AGENTS_BASE" ]]; then
                ln -sfn "$AGENTS_BASE" "$target" && echo "Linked ${target} -> ${AGENTS_BASE}"
            else
                echo "Leaving existing ${target} untouched"
            fi
        done
    done
fi

# --- 3. Point agents at the surface via the SSH banner ---
# The Vast control plane installs /etc/banner before our entrypoint runs, and
# sshd shows it on every connection (interactive or one-shot) — the one channel a
# non-interactive agent can't miss. Append our pointer at boot (idempotent); we
# can't bake it in because the control plane owns the file.
BANNER=/etc/banner
if [[ -f "$BANNER" ]] && ! grep -qF "vast-capabilities" "$BANNER"; then
    printf '%s\n' "AI agents: run 'vast-capabilities' or read ./AGENTS.md to understand this instance." >> "$BANNER" \
        && echo "Appended agent notice to ${BANNER}"
fi
