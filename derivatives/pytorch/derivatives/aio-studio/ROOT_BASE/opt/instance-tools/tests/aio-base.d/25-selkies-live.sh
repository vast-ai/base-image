#!/bin/bash
# Test: the Desktop route streams through Caddy — Selkies on loopback, the WebSocket
# handshake accepted with the token and same Origin, refused without the token or from
# another Origin, and no public TURN relay configured (ADR 0050).
# TEST_TIMEOUT=900
source "$(dirname "$0")/../lib.sh"

# Every check that only asked whether a binary existed passed while the stream itself
# was broken: Caddy rewrote Host and Selkies refused every browser WebSocket. This one
# drives the route a browser drives. It needs the desktop started
# (SUPERVISOR_AUTOSTART=desktop) and the Desktop entry in PORTAL_CONFIG.

# Skips are not passes where it matters: the base QA cell lists this test in
# INSTANCE_TEST_REQUIRE_PASS, so a skip there fails the cell.
portal_has_entry "internal_port: 16100" || test_skip "no Desktop (16100) entry in PORTAL_CONFIG"
[[ -n ${VAST_TCP_PORT_6100:-} ]] || test_skip "port 6100 is not mapped, so Caddy serves no Desktop route"
wait_for_supervisor 60 || test_fail "supervisord did not become reachable"
# First start installs the host-matching NVIDIA display driver before X, KDE and
# Selkies come up, which takes minutes on a slow host.
for _ in $(seq 1 120); do
    [[ $(_service_status desktop) == RUNNING ]] && break
    sleep 1
done
[[ $(_service_status desktop) == RUNNING ]] || test_skip "the desktop program is not running (SUPERVISOR_AUTOSTART=desktop)"
wait_for_port 16100 600 || test_fail "Selkies never listened on 16100 (is the desktop program started?)"

if listener_is_public 16100; then
    fail_later "selkies-bind" "Selkies listens on a public address ($(listener_local_addr 16100 | tr '\n' ' ')); it must be loopback behind Caddy"
else
    echo "  bind: $(listener_local_addr 16100 | tr '\n' ' ')"
fi

. /opt/supervisor-scripts/utils/selkies.sh
proto=http
selkies_https_effective && proto=https
wait_for_caddy 6100 "$proto" || test_fail "Caddy is not serving the Desktop route on ${proto}://127.0.0.1:6100"

handshake() {
    curl -sk -o /dev/null -w '%{http_code}' --http1.1 --max-time 5 \
        -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
        -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Protocol: selkies" \
        "$@" "${proto}://127.0.0.1:6100/api/websockets"
}
origin="${proto}://127.0.0.1:6100"
auth=(-H "Authorization: Bearer ${OPEN_BUTTON_TOKEN}")

code=$(handshake "${auth[@]}" -H "Origin: ${origin}")
[[ $code == 101 ]] && echo "  same-origin with token: 101" \
    || fail_later "ws-same-origin" "a same-origin WebSocket with the token got ${code}, not 101; browsers cannot stream (Host rewritten? CADDY_HOST_PASSTHROUGH missing?)"
code=$(handshake -H "Origin: ${origin}")
[[ $code == 401 ]] && echo "  without token: 401" \
    || fail_later "ws-no-token" "a WebSocket without the token got ${code}, not 401"
code=$(handshake "${auth[@]}" -H "Origin: https://example.invalid")
[[ $code == 403 ]] && echo "  foreign origin with token: 403" \
    || fail_later "ws-foreign-origin" "a WebSocket from another Origin got ${code}, not 403; any site could drive the desktop"

rtc="${XDG_RUNTIME_DIR:-/run/user/1001}/selkies/rtc.json"
if [[ ! -s $rtc ]]; then
    fail_later "rtc-config" "${rtc} is missing; Selkies would fall back to a public TURN relay"
elif grep -qiE "metered|openrelay" "$rtc"; then
    fail_later "rtc-config" "${rtc} names a public TURN relay"
else
    echo "  rtc config: $(tr -d '\n' < "$rtc" | sed -E 's/"credential":"[^"]*"/"credential":"***"/' | cut -c1-160)"
fi

log=$(ls /var/log/portal/desktop.log /var/log/desktop.log 2>/dev/null | head -1)
plan=$(grep -ah "selkies: transport=" "$log" 2>/dev/null | tail -1)
# Selkies probes the encoder at startup: "Render node N encodes H264, ... on nvenc." when
# NVENC opens, "No hardware encoder on render node N (nvenc): ..." when it does not.
enc=$(grep -ahE "encodes H264.* on [a-z]+\.|No hardware encoder" "$log" 2>/dev/null | tail -1)
echo "  plan: ${plan:-not logged}"
if [[ $enc =~ encodes\ H264.*\ on\ nvenc\. ]]; then
    echo "  encoder: ${enc#*] }"
elif has_gpu && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -qvE "A100|H100|H200|B200|GH200"; then
    echo "  WARN: a GPU with a video encoder is present but Selkies did not find NVENC: ${enc:-no probe line logged}"
else
    echo "  encoder: software (${enc:-no probe line logged})"
fi

report_failures
test_pass "the Desktop route streams through Caddy with the token, refuses other Origins, and uses no public relay"
