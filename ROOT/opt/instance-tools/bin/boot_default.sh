#!/bin/bash

umask 002
main() {
    local propagate_user_keys=true
    local export_env=true
    local generate_tls_cert=true
    local activate_python_environment=true
    local sync_environment=false
    local sync_home_to_workspace=false
    local update_portal=true
    local update_vast_cli=true

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-user-keys)
                propagate_user_keys=false
                shift
                ;;
            --no-export-env)
                export_env=false
                shift
                ;;
            --no-cert-gen)
                generate_tls_cert=false
                shift
                ;;
            --no-update-portal)
                update_portal=false
                shift
                ;;
            --no-update-vast)
                update_vast_cli=false
                shift
                ;;
            --no-activate-pyenv)
                activate_python_environment=false
                shift
                ;;
            --no-forward-compat)
                export DISABLE_FORWARD_COMPAT=true
                shift
                ;;
            --sync-environment)
                sync_environment=true
                shift
                ;;
            --sync-home)
                sync_home_to_workspace=true
                shift
                ;;
            --jupyter-override)
                export JUPYTER_OVERRIDE=true
                shift
                ;;
            --no-force-jupyter)
                export FORCE_JUPYTER=false
                shift
                ;;
            --no-agent-banner)
                export ENABLE_AGENT_BANNER=false
                shift
                ;;
            *)
                echo "Warning: Unknown flag: $1" >&2
                shift
                ;;
        esac
    done

    # The serverless update-flag block moved to /etc/vast_boot.d/01-detect-serverless.sh
    # (ADR 0034), so the mode decision and its first consequence live together. Stages are
    # SOURCED below, so that stage sets these same locals through dynamic scope — the
    # mechanism 46-user-propagate-ssh-keys.sh, 10-prep-env.sh and 37-sync-environment.sh
    # already depend on. Changing `.` to execution below silently breaks all four.

    # Source boot scripts
    for script in /etc/vast_boot.d/*.sh; do
        [[ -f "$script" ]] && [[ -r "$script" ]] && . "$script"
    done
}

main "$@"