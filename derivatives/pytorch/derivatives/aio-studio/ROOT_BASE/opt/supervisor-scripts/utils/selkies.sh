#!/bin/bash
# Selkies 2.0 launch plan, shared byte-for-byte by every desktop image (ADR 0050).
# Sourced by the Selkies launcher and by coturn's; both run as the desktop user.
#
#   selkies_wait_caddyfile  wait for the Caddyfile written this boot
#   selkies_plan            decide HTTPS, TURN and transport; exports SELKIES_PLAN_*
#   selkies_write_rtc       write the RTC config Selkies reads (never the public relay)
#   selkies_exec            exec Selkies on loopback with the GPU-node shim preloaded
#   selkies_coturn          run coturn when the plan uses the in-image TURN server
#   selkies_session_preload LD_PRELOAD for the desktop session's applications

SELKIES_INTERNAL_PORT=16100
SELKIES_RUN_DIR="${XDG_RUNTIME_DIR:-/tmp}/selkies"
SELKIES_NVREACH=/usr/local/lib/selkies/nvreach.so
# The client's camera and gamepads reach the desktop's applications only through these
# interposers (no kernel device in a container). `$LIB` is the dynamic loader's token, so
# 32-bit applications get the 32-bit build; the shell must leave it unexpanded.
SELKIES_INPUT_INTERPOSER='/usr/$LIB/selkies_input_interposer.so'
SELKIES_V4L2_INTERPOSER='/usr/$LIB/selkies_v4l2_interposer.so'
SELKIES_INTERPOSER_DIRS=/usr/lib
SELKIES_PORTAL_VERSION_FILE=/opt/portal-aio/VERSION
SELKIES_PORTAL_MIN=v3.1.7

# /etc persists across stop/start, so last boot's Caddyfile is there before this boot's
# generator rewrites it. Only a file written since PID 1 started describes this boot.
selkies_wait_caddyfile() {
    local caddyfile=${1:-/etc/Caddyfile} timeout=${2:-90} boot i
    boot=$(stat -c %Y /proc/1 2>/dev/null || echo 0)
    for (( i = 0; i < timeout; i++ )); do
        if [[ -s $caddyfile ]] && (( $(stat -c %Y "$caddyfile") >= boot )); then
            return 0
        fi
        sleep 1
    done
    echo "selkies: no Caddyfile written this boot after ${timeout}s; assuming plain HTTP"
    return 1
}

# Caddy serves TLS on a site only when ENABLE_HTTPS is true AND a usable certificate
# exists; the environment alone cannot tell, so read Caddy's own decision: the site
# that proxies to Selkies carries a `tls` line. No Caddyfile, or no such site, is HTTP.
# Site headers may be indented: the generator's first site is, until `caddy fmt` runs.
selkies_https_effective() {
    local caddyfile=${1:-/etc/Caddyfile}
    [[ -r $caddyfile ]] || return 1
    awk -v port="localhost:${SELKIES_INTERNAL_PORT}" '
        /^[[:space:]]*:[0-9]+[[:space:]]*\{/ { tls = 0; inside = 1 }
        inside && /^[[:space:]]*tls[[:space:]]/ { tls = 1 }
        inside && index($0, "reverse_proxy " port) && tls { found = 1 }
        END { exit !found }
    ' "$caddyfile"
}

# TURN, in order: a server the user configured (SELKIES_TURN_HOST or legacy TURN_HOST),
# else the in-image coturn when Vast mapped its directives, else none. TCP and UDP use
# separate directives, 73478/tcp and 73479/udp: Vast refuses one port number mapped for
# both protocols. A bare legacy TURN_SERVER only ever meant "do not
# start coturn", so on its own it disables TURN rather than naming a server.
selkies_turn_plan() {
    SELKIES_PLAN_TURN=none
    SELKIES_PLAN_TURN_HOST="" SELKIES_PLAN_TURN_UDP="" SELKIES_PLAN_TURN_TCP=""
    SELKIES_PLAN_TURN_USER="" SELKIES_PLAN_TURN_PASS=""
    local user_host=${SELKIES_TURN_HOST:-${TURN_HOST:-}}
    if [[ -n $user_host ]]; then
        SELKIES_PLAN_TURN=user
        SELKIES_PLAN_TURN_HOST=$user_host
        local port=${SELKIES_TURN_PORT:-${TURN_PORT:-3478}}
        if [[ ${SELKIES_TURN_PROTOCOL:-${TURN_PROTOCOL:-udp}} == tcp ]]; then
            SELKIES_PLAN_TURN_TCP=$port
        else
            SELKIES_PLAN_TURN_UDP=$port
        fi
        SELKIES_PLAN_TURN_USER=${SELKIES_TURN_USERNAME:-${TURN_USERNAME:-}}
        SELKIES_PLAN_TURN_PASS=${SELKIES_TURN_PASSWORD:-${TURN_PASSWORD:-}}
    elif [[ -n ${TURN_SERVER:-} ]]; then
        echo "selkies: TURN_SERVER is set without TURN_HOST; the in-image TURN server stays off"
    elif [[ -n ${PUBLIC_IPADDR:-} && ( -n ${VAST_UDP_PORT_73479:-} || -n ${VAST_TCP_PORT_73478:-} ) ]]; then
        SELKIES_PLAN_TURN=self
        SELKIES_PLAN_TURN_HOST=$PUBLIC_IPADDR
        SELKIES_PLAN_TURN_UDP=${VAST_UDP_PORT_73479:-}
        SELKIES_PLAN_TURN_TCP=${VAST_TCP_PORT_73478:-}
        SELKIES_PLAN_TURN_USER=selkies
        SELKIES_PLAN_TURN_PASS=$(selkies_turn_secret)
    fi
    return 0
}

# One credential per container start, shared by Selkies and coturn through a 0600 file;
# it never appears on a command line. /run/user is on the overlay and survives
# stop/start, so the file is keyed by PID 1's start time. `ln` publishes it atomically
# and refuses to replace an existing file, so two first callers agree on one value.
selkies_turn_secret() {
    local start f tmp
    start=$(stat -c %Y /proc/1 2>/dev/null || echo 0)
    f="${SELKIES_RUN_DIR}/turn-secret.${start}"
    mkdir -p "$SELKIES_RUN_DIR" && chmod 700 "$SELKIES_RUN_DIR"
    if [[ ! -s $f ]]; then
        find "$SELKIES_RUN_DIR" -maxdepth 1 -name 'turn-secret.*' ! -name "turn-secret.${start}*" -delete 2>/dev/null
        tmp="$f.$$"
        (umask 077; head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$tmp")
        ln "$tmp" "$f" 2>/dev/null
        rm -f "$tmp"
    fi
    cat "$f"
}

# The WebSocket client needs a secure context (WebCodecs); the WebRTC client loads on
# plain HTTP. So: an explicit SELKIES_MODE wins; else HTTPS -> websockets; else TURN
# available -> webrtc; else websockets, which shows the browser's HTTPS error.
selkies_plan() {
    if selkies_https_effective "${1:-}"; then SELKIES_PLAN_HTTPS=true; else SELKIES_PLAN_HTTPS=false; fi
    selkies_turn_plan
    if [[ -n ${SELKIES_MODE:-} ]]; then
        SELKIES_PLAN_MODE=$SELKIES_MODE
    elif [[ $SELKIES_PLAN_HTTPS == true ]]; then
        SELKIES_PLAN_MODE=websockets
    elif [[ $SELKIES_PLAN_TURN != none ]]; then
        SELKIES_PLAN_MODE=webrtc
    else
        SELKIES_PLAN_MODE=websockets
    fi
    # The in-page switch is offered only where both transports can work.
    if [[ $SELKIES_PLAN_HTTPS == true && $SELKIES_PLAN_TURN != none ]]; then
        SELKIES_PLAN_DUAL=true
    else
        SELKIES_PLAN_DUAL=false
    fi
    export SELKIES_PLAN_HTTPS SELKIES_PLAN_TURN SELKIES_PLAN_MODE SELKIES_PLAN_DUAL
}

# Always written, and given to Selkies with --rtc-config-json, which outranks every other
# TURN source. Selkies treats an unparseable file as absent and falls back to the public
# relay, so the file is built with a JSON encoder and re-read before it is used.
selkies_write_rtc() {
    local out="${SELKIES_RUN_DIR}/rtc.json"
    mkdir -p "$SELKIES_RUN_DIR" && chmod 700 "$SELKIES_RUN_DIR"
    if (
        umask 077
        RTC_TURN=$SELKIES_PLAN_TURN RTC_HOST=$SELKIES_PLAN_TURN_HOST \
        RTC_UDP=$SELKIES_PLAN_TURN_UDP RTC_TCP=$SELKIES_PLAN_TURN_TCP \
        RTC_USER=$SELKIES_PLAN_TURN_USER RTC_PASS=$SELKIES_PLAN_TURN_PASS \
        python3 -c '
import json, os, sys
e = os.environ
servers = []
if e["RTC_TURN"] != "none":
    h = e["RTC_HOST"]
    if ":" in h and not h.startswith("["):
        h = "[" + h + "]"
    if e["RTC_UDP"]:
        servers.append({"urls": ["stun:%s:%s" % (h, e["RTC_UDP"])]})
    turn = ["turn:%s:%s?transport=%s" % (h, e["RTC_" + p.upper()], p)
            for p in ("udp", "tcp") if e["RTC_" + p.upper()]]
    if turn:
        servers.append({"urls": turn, "username": e["RTC_USER"], "credential": e["RTC_PASS"]})
json.dump({"iceServers": servers}, sys.stdout)
' > "$out.tmp"
    ) && python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$out.tmp"; then
        mv -f "$out.tmp" "$out"
    else
        echo "selkies: could not write a valid ${out}; refusing to start on the public relay"
        rm -f "$out.tmp"
        return 1
    fi
    SELKIES_PLAN_RTC=$out
}

# Caddy forwards the browser's Host to Selkies only from portal v3.1.7
# (CADDY_HOST_PASSTHROUGH). An image built on an older base gets it from the first-boot
# portal update, which can be skipped (no network, PORTAL_VERSION pinned, serverless); then
# Selkies refuses every browser WebSocket with nothing else saying why.
selkies_check_portal() {
    local v
    v=$(tr -d '[:space:]' < "$SELKIES_PORTAL_VERSION_FILE" 2>/dev/null)
    [[ -n $v ]] || return 0
    [[ $v == v* ]] || v="v$v"
    if [[ $(printf '%s\n%s\n' "$SELKIES_PORTAL_MIN" "$v" | sort -V | head -n1) != "$SELKIES_PORTAL_MIN" ]]; then
        echo "selkies: WARNING: portal ${v} predates ${SELKIES_PORTAL_MIN} and rewrites the Host header, so"
        echo "selkies: browsers will be refused (\"disallowed Origin\"). Unset PORTAL_VERSION or let the"
        echo "selkies: first-boot portal update run."
        return 1
    fi
    return 0
}

selkies_exec() {
    selkies_write_rtc || exit 1
    selkies_check_portal
    echo "selkies: transport=${SELKIES_PLAN_MODE} https=${SELKIES_PLAN_HTTPS} turn=${SELKIES_PLAN_TURN} switch=${SELKIES_PLAN_DUAL}"
    if [[ $SELKIES_PLAN_MODE == websockets && $SELKIES_PLAN_HTTPS != true ]]; then
        echo "selkies: this page is served over plain HTTP, where browsers withhold the video decoder the"
        echo "selkies: stream needs. Install the console certificate and set ENABLE_HTTPS=true, or map the"
        echo "selkies: TURN ports 73478 and 73479/udp so the desktop can stream over WebRTC instead."
    elif [[ $SELKIES_PLAN_MODE == webrtc && $SELKIES_PLAN_TURN == none ]]; then
        echo "selkies: WebRTC is forced but no TURN server is available; browsers outside this host's"
        echo "selkies: network will not connect. Map 73478 and 73479/udp, or set TURN_HOST."
    fi
    # Selkies reads these fallback names too; a value inherited from the template would
    # silently move its routes, port, or auth.
    unset SUBFOLDER CUSTOM_WS_PORT PASSWORD PASSWD DRI_NODE AUTO_GPU
    # Microphone and camera are on, and asked of the browser only while an application in
    # the desktop records from them (released ten seconds after it stops). Upstream's
    # defaults leave both off, and the side-panel toggle alone delivered silence.
    export SELKIES_MICROPHONE_ENABLED="${SELKIES_MICROPHONE_ENABLED:-true}"
    export SELKIES_MICROPHONE_ON_START="${SELKIES_MICROPHONE_ON_START:-demand}"
    export SELKIES_WEBCAM_ENABLED="${SELKIES_WEBCAM_ENABLED:-true}"
    export SELKIES_WEBCAM_ON_START="${SELKIES_WEBCAM_ON_START:-demand}"
    # Selkies serves gamepads and the camera to applications through the interposers, and
    # must know it; but their process-wide hooks would block its own event loop, so they
    # are never preloaded into Selkies itself.
    export SELKIES_INTERPOSER="$SELKIES_INPUT_INTERPOSER"
    export SELKIES_WEBCAM_INTERPOSER="$SELKIES_V4L2_INTERPOSER"
    LD_PRELOAD=$(selkies_drop_interposers "${LD_PRELOAD:-}")
    # 1.x encoder names; h264enc is hardware-first with a software fallback in 2.0.
    case ${SELKIES_ENCODER:-} in
        x264enc|nvh264enc|vah264enc) export SELKIES_ENCODER=h264enc ;;
    esac
    if [[ -r $SELKIES_NVREACH ]]; then
        export LD_PRELOAD="${SELKIES_NVREACH}${LD_PRELOAD:+:${LD_PRELOAD}}"
    fi
    exec selkies \
        --addr=127.0.0.1 \
        --port="${SELKIES_INTERNAL_PORT}" \
        --enable-https=false \
        --enable-basic-auth=false \
        --mode="${SELKIES_PLAN_MODE}" \
        --enable-dual-mode="${SELKIES_PLAN_DUAL}" \
        --rtc-config-json="${SELKIES_PLAN_RTC}" \
        "$@"
}

# Drop the interposers from a preload list, keeping anything else an operator set.
selkies_drop_interposers() {
    local entry kept="" IFS=:
    for entry in $1; do
        case $entry in
            "$SELKIES_INPUT_INTERPOSER"|"$SELKIES_V4L2_INTERPOSER"|*/selkies_input_interposer.so|*/selkies_v4l2_interposer.so) ;;
            "") ;;
            *) kept="${kept:+$kept:}$entry" ;;
        esac
    done
    printf '%s' "$kept"
}

# LD_PRELOAD for the desktop session: the interposers that are installed, then whatever
# was already preloaded. Applications inherit it from the session, so a camera appears as
# /dev/video0 and gamepads as /dev/input nodes with no kernel device.
selkies_session_preload() {
    local libs=() name existing
    for name in selkies_input_interposer selkies_v4l2_interposer; do
        if compgen -G "${SELKIES_INTERPOSER_DIRS}/*/${name}.so" >/dev/null; then
            [[ $name == selkies_input_interposer ]] && libs+=("$SELKIES_INPUT_INTERPOSER") || libs+=("$SELKIES_V4L2_INTERPOSER")
        fi
    done
    existing=$(selkies_drop_interposers "${LD_PRELOAD:-}")
    [[ -n $existing ]] && libs+=("$existing")
    local IFS=:
    printf '%s' "${libs[*]}"
}

# coturn relays for the browser only when the plan runs it, one listener per mapped
# protocol (each has its own 1:1 port). Relay addresses stay on this container's own
# interfaces, where Selkies' media endpoint is, so no public relay port is needed. It
# never relays to loopback (no allow-loopback-peers) or to private ranges other than
# this container's own addresses.
selkies_coturn() {
    if [[ $SELKIES_PLAN_TURN != self ]]; then
        echo "coturn: not needed (turn=${SELKIES_PLAN_TURN})"
        return 0
    fi
    local proto port conf ip pids=()
    for proto in udp tcp; do
        if [[ $proto == udp ]]; then port=$SELKIES_PLAN_TURN_UDP; else port=$SELKIES_PLAN_TURN_TCP; fi
        [[ -n $port ]] || continue
        conf="${SELKIES_RUN_DIR}/turnserver-${proto}.conf"
        (
            umask 077
            {
                echo "listening-port=${port}"
                if [[ $proto == udp ]]; then echo "no-tcp"; else echo "no-udp"; fi
                echo "realm=vast.ai"
                echo "lt-cred-mech"
                echo "user=${SELKIES_PLAN_TURN_USER}:${SELKIES_PLAN_TURN_PASS}"
                echo "fingerprint"
                echo "no-cli"
                echo "no-tls"
                echo "no-dtls"
                echo "no-multicast-peers"
                # coturn sizes its relay pool per CPU and high-core hosts cap a container's
                # pids; one desktop user needs no relay pool (0 = single-threaded relay).
                # Its auth pool, 1 + cpus/2 with cpus capped at 128, has no setting.
                echo "relay-threads=0"
                echo "log-file=stdout"
                echo "simple-log"
                # Its defaults (/var/lib/turn/turndb, /var/tmp/turnserver.pid) are root's, and
                # the two listeners would share one pid file.
                echo "userdb=${SELKIES_RUN_DIR}/turndb-${proto}"
                echo "pidfile=${SELKIES_RUN_DIR}/turnserver-${proto}.pid"
                for range in 0.0.0.0-0.255.255.255 10.0.0.0-10.255.255.255 100.64.0.0-100.127.255.255 \
                             127.0.0.0-127.255.255.255 169.254.0.0-169.254.255.255 172.16.0.0-172.31.255.255 \
                             192.168.0.0-192.168.255.255 ::1 fc00::-fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff \
                             fe80::-febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff; do
                    echo "denied-peer-ip=${range}"
                done
                for ip in $(hostname -I 2>/dev/null); do
                    [[ $ip == 127.* || $ip == ::1 ]] || echo "allowed-peer-ip=${ip}"
                done
            } > "$conf"
        )
        turnserver -c "$conf" &
        pids+=($!)
    done
    trap 'kill "${pids[@]}" 2>/dev/null' EXIT TERM INT
    wait -n
}
