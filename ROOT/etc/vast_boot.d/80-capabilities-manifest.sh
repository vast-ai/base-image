#!/bin/bash

# Make the agent-facing capability surface discoverable:
#   1. /etc/vast_capabilities.json  — static snapshot for FS-only/offline agents
#      (live state is always available at the portal's /capabilities endpoint).
#   2. A combined agent guide at /etc/vast-agents-guide.md — a front index naming
#      every /etc/vast_agents/*.md present on this image plus their full
#      concatenated text — with {AGENTS.md,CLAUDE.md} symlinks to it, dropped where
#      an agent lands: ${WORKSPACE} (interactive shells cd here) and the home dirs
#      (a one-shot `ssh host cmd` lands in $HOME). One artifact is the complete
#      picture, so an agent that reads it can't treat base.md as the whole story
#      and miss the per-image files.
#   3. A pointer line on the SSH banner — shown on every connection, so even a
#      non-interactive agent that never reads the landing dir sees it.

WORKSPACE="${WORKSPACE:-/workspace}"
PORTAL_PYTHON=/opt/portal-aio/venv/bin/python
CAP_JSON=/etc/vast_capabilities.json
AGENTS_DIR=/etc/vast_agents
AGENTS_BASE=${AGENTS_DIR}/base.md
AGENTS_GUIDE=/etc/vast-agents-guide.md

# --- 1. Static capability snapshot (best-effort) ---
SNAPSHOT_PY='import json; from capabilities import assemble_static; print(json.dumps(assemble_static(), indent=2))'
if [[ -x "$PORTAL_PYTHON" ]]; then
    if ( cd /opt/portal-aio && "$PORTAL_PYTHON" -c "$SNAPSHOT_PY" > "$CAP_JSON" 2>/dev/null ); then
        echo "Wrote ${CAP_JSON}"
    else
        echo "WARNING: could not write ${CAP_JSON}"
    fi
fi

# --- 2a. Assemble the combined agent guide ---
# One file = the complete picture: a front index of every base/per-image guide,
# then their full text. Prevents the failure where an agent reads a base-only
# AGENTS.md and never discovers the per-image files (pytorch.md, comfyui.md, …).
if [[ -f "$AGENTS_BASE" ]]; then
    # Order as the actual build chain — base (foundation), then the framework
    # layer (pytorch/tensorflow), then the app guides alphabetically — so each
    # section's references only point at material already read above it.
    guides=("$AGENTS_BASE")
    for fw in pytorch tensorflow; do
        [[ -f "$AGENTS_DIR/$fw.md" ]] && guides+=("$AGENTS_DIR/$fw.md")
    done
    while IFS= read -r g; do
        case "$g" in
            "$AGENTS_BASE"|"$AGENTS_DIR/pytorch.md"|"$AGENTS_DIR/tensorflow.md") ;;
            *) guides+=("$g") ;;
        esac
    done < <(ls "$AGENTS_DIR"/*.md 2>/dev/null | sort)
    {
        printf '# Agent guide for this instance\n\n'
        printf 'This single file IS the complete agent guide — the full text of all %d guide(s)\n' "${#guides[@]}"
        printf 'is concatenated below (the same files also live individually under %s/, but you\n' "$AGENTS_DIR"
        printf 'do not need to open them; everything is here). Read this whole file before acting\n'
        printf 'on this instance. The sections are cumulative:\n\n'
        i=1
        for g in "${guides[@]}"; do
            label=$(grep -m1 -E '^#{1,6} ' "$g" 2>/dev/null | sed -E 's/^#{1,6} *//') || true
            printf '  %d. %s — %s\n' "$i" "$(basename "$g")" "${label:-$(basename "$g")}"
            i=$((i+1))
        done
        printf '\nKeep reading past the base section: the per-image sections below document services\n'
        printf 'and APIs (endpoints, wrappers, helpers) you would otherwise miss — read them before\n'
        printf 'calling an API, exposing a service, or setting up a model.\n\n'
        for g in "${guides[@]}"; do
            printf '=================== %s ===================\n\n' "$(basename "$g")"
            cat "$g"
            printf '\n'
        done
    } > "$AGENTS_GUIDE" 2>/dev/null && echo "Wrote ${AGENTS_GUIDE} (${#guides[@]} guide(s))"
fi

# --- 2b. Link the combined guide where agents land ---
# ${WORKSPACE} (interactive shells cd here) plus the home dirs (a one-shot
# `ssh host cmd` lands in $HOME). Create when absent, or refresh a link that is
# already ours — including the legacy base.md target, so existing instances
# upgrade on reboot — never replace a user's real file or their own symlink.
if [[ -f "$AGENTS_GUIDE" ]]; then
    for dir in "$WORKSPACE" /root /home/user; do
        [[ -d "$dir" ]] || continue
        for name in AGENTS.md CLAUDE.md; do
            target="${dir}/${name}"
            cur="$(readlink "$target" 2>/dev/null)"
            if [[ ! -e "$target" ]] || [[ "$cur" == "$AGENTS_GUIDE" ]] || [[ "$cur" == "$AGENTS_BASE" ]]; then
                ln -sfn "$AGENTS_GUIDE" "$target" && echo "Linked ${target} -> ${AGENTS_GUIDE}"
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
if [[ -f "$BANNER" ]]; then
    # Imperative, single directive — no "or" (which let agents treat the guide and
    # vast-capabilities as interchangeable and skip the guide). Reading the guide is
    # the instruction; vast-capabilities is called out as live state, not a substitute.
    # Refresh on every boot (drop any prior "AI agents:" line we added, then re-add) so
    # existing instances upgrade the wording. The control plane owns the rest of the file.
    sed -i '/^AI agents:/d' "$BANNER" 2>/dev/null || true
    printf '%s\n' "AI agents: READ /etc/vast-agents-guide.md (also linked as ./AGENTS.md) before acting on or describing this instance: it is the operating guide. ('vast-capabilities' shows live state only, not the guide.)" >> "$BANNER" \
        && echo "Refreshed agent notice in ${BANNER}"
fi
