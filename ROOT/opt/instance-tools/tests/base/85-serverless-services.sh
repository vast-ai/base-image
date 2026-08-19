#!/bin/bash
# Test: serverless mode — verify non-serverless services are stopped and ports closed.
source "$(dirname "$0")/../lib.sh"

is_serverless || test_skip "not in serverless mode"

# Services that should be stopped in serverless mode
for name in caddy instance_portal jupyter tensorboard tunnel_manager syncthing; do
    if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        assert_service_stopped "$name"
        echo "  ${name}: correctly stopped"
    fi
done

# caddy should not be running
! pidof caddy &>/dev/null || test_fail "caddy process still running in serverless mode"

# Key ports should NOT be listening
for port in 11111 11112 18080; do
    if ss -tln | grep -q ":${port} "; then
        test_fail "port ${port} is listening in serverless mode"
    fi
done

# supervisord itself must still be serving. Presence is not the question: this
# file has just asserted six programs are STOPPED, and every one of those answers
# came from the RPC socket. Asking `pgrep` here would accept a supervisord that
# forked and never served — under which those six assertions proved nothing.
#
# A FRESH probe, deliberately not wait_for_supervisor: the loop above has already
# called assert_service_stopped for every shipped conf, each of which set the
# readiness memo, so a wait here would short-circuit and could never fail. The
# `pgrep` it replaced could. "Still serving" is a new question, so it gets a new
# answer rather than a cached one.
supervisorctl status &>/dev/null; _sup_rc=$?
(( _sup_rc == 0 || _sup_rc == 3 )) \
    || test_fail "supervisord stopped serving its RPC socket during the test (exit ${_sup_rc})"

test_pass "serverless service checks passed"
