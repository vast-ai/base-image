#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Llama.cpp"

echo "Starting Llama.cpp"

cd "${WORKSPACE}/"
if [[ -n "${LLAMA_MODEL:-}" ]]; then
  # Pin the listen address OUTSIDE the ${LLAMA_ARGS:-...} default. As a default it
  # applied only while LLAMA_ARGS was entirely UNSET, so a template that set it for any
  # unrelated reason (-ngl 99, --ctx-size 8192) silently dropped the port and
  # llama-server fell back to its own default of 8080 (llama.cpp common.h:
  # `int32_t port = 8080`). That broke two things at once: the portal's API entry, which
  # PORTAL_CONFIG fronts at 18000, and the serverless worker, which proxies to a
  # HARDCODED http://127.0.0.1:18000 — MODEL_SERVER_URL/MODEL_SERVER_PORT are module
  # constants in pyworker's workers/openai/core.py, the only values in that file that
  # are not env reads, so no variable can move the worker to meet the engine.
  #
  # Each flag is added only when LLAMA_ARGS does not already carry it, so a template
  # that pins its own address still wins. Gated by L078.
  llama_args="${LLAMA_ARGS:-}"
  pinned=()
  if [[ ! "${llama_args}" =~ (^|[[:space:]])--host([=[:space:]]|$) ]]; then
      llama_args="--host 127.0.0.1 ${llama_args}"
      pinned+=("--host 127.0.0.1")
  fi
  if [[ ! "${llama_args}" =~ (^|[[:space:]])--port([=[:space:]]|$) ]]; then
      llama_args="--port 18000 ${llama_args}"
      pinned+=("--port 18000")
  fi
  # Report only what THIS script added, never LLAMA_ARGS itself. That variable is
  # operator-supplied and llama-server accepts `--api-key` in it; stdout here is tee'd
  # to /var/log/portal/llama.log, which the Instance Portal serves and which the QA
  # gate collects as evidence — so echoing the args verbatim would publish a
  # credential that nothing published before.
  if [[ ${#pinned[@]} -gt 0 ]]; then
      echo "llama.sh pinned the listen address: ${pinned[*]}"
  else
      echo "LLAMA_ARGS pins its own listen address; llama.sh added nothing"
  fi
  pty llama-server -hf "$LLAMA_MODEL" ${llama_args} 2>&1
else
  echo "Model not specified.  Exiting"
  sleep 6
fi
