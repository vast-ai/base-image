#!/bin/bash
# Stage 01 — decide the runtime mode BEFORE anything depends on it (ADR 0034).
#
# EXPIRES: 2026-11-25
#
# This is a BRIDGE. Vast's autoscaler injects MASTER_TOKEN into every worker but does
# not yet inject SERVERLESS; the backend doing so at instance-create, gated on
# endpoint_id, is the real fix. When that lands this file is DELETED, not kept — see
# the retraction note at the bottom, which is not optional.
#
# Why stage 01 and not 05: seven derivative and external images ship 05-<name>-env.sh
# into this same directory, and this must run before all of them.
#
# Why this stage owns the update-flag block that used to live in boot_default.sh:
# stages are SOURCED inside main() (boot_default.sh: `. "$script"`), so main()'s locals
# are in dynamic scope here. The repo already relies on that at
# 46-user-propagate-ssh-keys.sh:4 (propagate_user_keys), 10-prep-env.sh:48 (export_env)
# and 37-sync-environment.sh:5 (sync_environment). If anyone ever changes that loop from
# sourcing to execution, those three break too — and so does this.
#
# THIS STAGE EXPORTS. IT MUST NEVER WRITE /etc/environment.
# That is an ownership boundary, not a style preference: the platform seeds the
# environment at first boot and the user owns the container thereafter, so an edit to
# /etc/environment prevails by design (10-prep-env.sh:47). Stage 10 sources that file
# AFTER this one, so a user's edit overrides this decision for every consumer that
# matters — exit_serverless.sh, pyworker.sh, and supervisor units authored from a
# provisioning manifest. Writing the file here would take that away.

_sd_marker=/run/vast-serverless-detect
_sd_verdict=none
_sd_reason=""

# Off switch, read from template env at launch. Without it, backing out a wrong
# inference is a rebuild and re-promote of base plus every derivative — the same
# argument that made the readiness budgets and EXPOSURE_ENFORCE overridable.
if [[ "${VAST_SERVERLESS_DETECT,,}" == "false" || "${VAST_SERVERLESS_DETECT,,}" == "off" ]]; then
    _sd_verdict=disabled
    _sd_reason="VAST_SERVERLESS_DETECT=${VAST_SERVERLESS_DETECT}"
# An explicit declaration always wins. This is an inference from a proxy; it must not
# overrule a human who typed the value, and this branch is also what makes the whole
# mechanism inert the day the backend injects SERVERLESS itself — no coordination
# between the two needed. Empty is NOT a declaration: `-e SERVERLESS=` states nothing.
elif [[ -n "${SERVERLESS:-}" ]]; then
    _sd_verdict=declared
    _sd_reason="SERVERLESS was already set"
# TWO SIGNALS, BOTH REQUIRED. Presence only — NEVER echo, log or record either value,
# MASTER_TOKEN is a credential.
#
# Why AND rather than MASTER_TOKEN alone: the two error directions are wildly asymmetric.
# A false POSITIVE darkens the instance permanently — exit_serverless.sh exits 0 and its
# units are autorestart=unexpected + exitcodes=0, so supervisord never restarts caddy, the
# portal, jupyter, the tunnel manager, syncthing, tensorboard, or any supervisor unit
# authored from a provisioning manifest. A false NEGATIVE costs almost nothing, because
# this is a SAFETY NET rather than the primary path: 9 of 10 published autoscaler
# templates declare SERVERLESS themselves, and an explicit declaration always wins above.
# So requiring corroboration trades a cheap failure for an expensive one, in the right
# direction. It also bounds the blast radius if the platform ever starts injecting
# MASTER_TOKEN more widely than the autoscaler does today.
#
# REPORT_ADDR is set on the same autoscaler path but CONDITIONALLY, so requiring it will
# miss some genuine workers. That is the accepted cost above — and the near-miss branches
# below make each miss visible instead of silent.
#
# NOT included, and this was checked rather than assumed: VAST_TCP_PORT_3000. All four
# serverless-capable images (vllm, sglang, llama-cpp, comfyui) carry `EXPOSE 3000`
# UNCONDITIONALLY, so Vast maps it on every instance of those images regardless of mode —
# their Dockerfiles say so directly ("unbound and harmless on-demand"). Base does not
# expose it at all, so the variable would mean opposite things on different images. A
# signal that does not discriminate on the four images that matter is worse than none.
elif [[ -n "${MASTER_TOKEN:-}" && -n "${REPORT_ADDR:-}" ]]; then
    export SERVERLESS=true
    _sd_verdict=detected
    _sd_reason="MASTER_TOKEN and REPORT_ADDR both present"
elif [[ -n "${MASTER_TOKEN:-}" ]]; then
    # The expected shape of a false negative. Loud, because otherwise a worker that
    # should have been detected looks identical to an ordinary on-demand rental, and we
    # would learn the real frequency of a conditional REPORT_ADDR from a support ticket
    # rather than from the first occurrence.
    _sd_reason="MASTER_TOKEN present but REPORT_ADDR absent — NOT activating (corroboration required)"
elif [[ -n "${REPORT_ADDR:-}" ]]; then
    # The rename signature: autoscaler env on the box without the key we sniff. Corroboration
    # can raise an alarm; it must never enable the mode on its own.
    _sd_reason="REPORT_ADDR present but MASTER_TOKEN absent — NOT activating; if this is a real worker the primary signal may have been renamed"
else
    _sd_reason="no autoscaler signals and no explicit SERVERLESS"
fi

# Written on EVERY outcome, including the negative one. "Detection ran and declined" and
# "this image predates detection" are different facts and only a marker separates them —
# which is the whole reason base/60-gpu-cuda can trust /run/vast-cuda-config-failed.
# TRUNCATE, never append: /run persists across boots here (05-configure-cuda.sh:74), so
# a stale breadcrumb would outlive the boot that wrote it.
mkdir -p "$(dirname "$_sd_marker")" 2>/dev/null
if ! printf 'verdict=%s\nserverless=%s\nreason=%s\n' \
        "$_sd_verdict" "${SERVERLESS:-unset}" "$_sd_reason" > "$_sd_marker" 2>/dev/null; then
    echo "Warning: could not write ${_sd_marker} — the mode decision will not be visible"
fi

# stdout, because on a serverless worker the portal never starts and docker logs is the
# only surface there is.
echo "Serverless mode: ${_sd_verdict} (SERVERLESS=${SERVERLESS:-unset}) — ${_sd_reason}"

# Moved here from boot_default.sh so the mode and its first consequence are one edit
# rather than two files. Cold start is the product on a worker; these two fetches are
# pure latency there.
if [[ "${SERVERLESS,,}" == "true" ]]; then
    update_portal=false
    update_vast_cli=false
fi

unset _sd_marker _sd_verdict _sd_reason

# RETRACTION OBLIGATION, for whoever deletes this file on expiry.
# Deleting the stage does NOT unset SERVERLESS on instances that already ran it: stage 10
# snapshotted it into /etc/environment on their first boot, and that file is the user's
# from then on. A previously-detected instance therefore stays serverless with nothing
# setting it — the same trap ADR 0025 hit, whose fix was an explicit `migrate-unset-xet`.
# docs/invariants.md states the rule: a variable removed from the managed set must be
# UNSET, not merely stopped being written. Ship the retraction with the deletion.
