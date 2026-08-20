#!/bin/bash
# Test: --sync-home / --sync-environment actually worked.
#
# WHY THIS EXISTS. Boot stages 35 and 37 move /root, /home/* and /venv/* onto
# $WORKSPACE and symlink them back, so that co-located instances sharing a
# volume see the same home and environment. Nothing exercised them: no template
# in this repo set the flags, so a change to that code first executed on a
# customer. That is how a bounded-wait fix shipped with a `return 1` sitting
# between the `.ssh` MOVE and the symlink back, which would have left an
# instance booting green with key-based SSH dead (ADR 0029 audit, 2026-08-20).
#
# It does NOT need a volume. $WORKSPACE defaults to a container directory, and
# the lock, the completion markers, the wait, the relinking and the .ssh
# round-trip are all identical — only the multi-instance race needs a shared
# volume, and that is not what this test is for. Measured cost on base: ~17s.
source "$(dirname "$0")/../lib.sh"

workspace="${WORKSPACE:-/workspace}"

# Predicate, not a self-skip: this is only meaningful when the flags are on, and
# base-qa names this test in INSTANCE_TEST_REQUIRE_PASS so it cannot skip its
# way to green there (ADR 0019).
if [[ ! -L /root && ! -L /venv/main ]]; then
    test_skip "neither --sync-home nor --sync-environment is enabled"
fi

# ── --sync-home ──────────────────────────────────────────────────────
if [[ -L /root ]]; then
    target=$(readlink -f /root)
    [[ "$target" == "${workspace}/home/root" ]] \
        || fail_later "root-target" "expected ${workspace}/home/root, got ${target}"
    [[ -d "$target" ]] || fail_later "root-dir" "/root symlink does not resolve"

    # The completion marker, not merely the absence of the in-progress one.
    # Absence cannot distinguish "not started" from "finished", which is the
    # defect the marker was added for.
    assert_file_exists "${workspace}/home/.synced"
    [[ -f "${workspace}/home/.syncing" ]] \
        && fail_later "home-syncing" "in-progress marker left behind after a completed sync"

    # THE regression: .ssh is moved out to /home_ssh before the sync and only
    # symlinked back afterwards. Anything that exits in between strands it, and
    # 46-user-propagate-ssh-keys then dies on `realpath` under set -euo pipefail
    # rather than recreating it — so the instance boots healthy with no keys.
    if [[ -e /root/.ssh ]]; then
        ssh_target=$(readlink -f /root/.ssh)
        [[ -d "$ssh_target" ]] || fail_later "root-ssh" "/root/.ssh does not resolve"
        [[ "$ssh_target" == /home_ssh/* ]] \
            || echo "  note: /root/.ssh resolves outside /home_ssh (${ssh_target})"
        echo "  sync-home: /root -> ${target}, .ssh -> ${ssh_target}"
    else
        fail_later "root-ssh-missing" "/root/.ssh is absent — SSH keys are stranded"
        echo "  sync-home: /root -> ${target}, but .ssh was NOT relinked"
    fi
fi

# ── --sync-environment ───────────────────────────────────────────────
if [[ -L /venv/main ]]; then
    venv_target=$(readlink -f /venv/main)
    [[ -d "$venv_target" ]] || fail_later "venv-target" "/venv/main does not resolve"
    [[ "$venv_target" == "${workspace}/.environment_sync/"* ]] \
        || fail_later "venv-path" "expected a ${workspace}/.environment_sync target, got ${venv_target}"

    env_dir=$(dirname "$(dirname "$venv_target")")
    assert_file_exists "${env_dir}/.synced"
    [[ -f "${env_dir}/.syncing" ]] \
        && fail_later "env-syncing" "in-progress marker left behind after a completed sync"

    # The relink is worthless if the interpreter it points at cannot run — which
    # is exactly what a half-copied tree looks like.
    "$venv_target/bin/python" -c "import sys; sys.exit(0)" 2>/dev/null \
        || fail_later "venv-python" "python in the synced venv does not execute"
    echo "  sync-environment: /venv/main -> ${venv_target}, python runs"
fi

report_failures
test_pass "home/environment sync completed and relinked correctly"
