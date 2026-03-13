# Instance Test Framework

Automated test suite that validates a Vast.ai instance is correctly configured after boot and provisioning. Tests cover the base image infrastructure; derivative images can add their own tests.

## Architecture

```
tests/
├── runner.sh              # Test runner — discovery, execution, results, post-test actions
├── lib.sh                 # Shared helpers — sourced by every test script
├── base/                  # Base image tests (always present)
│   ├── 10-supervisor.sh
│   ├── 11-instance-metadata.sh
│   ├── 12-provisioning.sh
│   ├── ...
│   └── 85-serverless-negative.sh
└── *.d/                   # Derivative test directories (e.g. pytorch.d/, comfyui.d/)
```

### Runner (`runner.sh`)

Discovers and executes test scripts in sort order. Writes JSON results to `/var/log/test-results.json` after each test, enabling real-time monitoring.

**Modes:**
- **Automated** — launched by boot script (`/etc/vast_boot.d/85-instance-test.sh`) when `INSTANCE_TEST=true`. Starts an HTTP/SSE results server on port 10199, waits for a client to connect, posts results to optional webhook on completion. The test *client* (`run_test.py`) handles instance stop/destroy.
- **Manual** — auto-detected when run from a TTY (interactive shell). No HTTP server, no webhook, no instance stop. Can also be forced with `--manual` / `--auto` flags.

**Per-test timeout:** Each test gets 3600s (1 hour) by default, configurable via `INSTANCE_TEST_DEFAULT_TIMEOUT`. Override per-test with a `# TEST_TIMEOUT=N` comment in the script header (see `12-provisioning.sh`).

**Results JSON format:**
```json
{
  "state": "running|passed|failed",
  "started_at": "2026-03-12T10:00:00Z",
  "elapsed_s": 42,
  "tests": [
    {"name": "base/10-supervisor", "state": "passed|failed|skipped|running|pending", "duration_s": 1}
  ]
}
```

**Results endpoints (automated mode):**
- `GET :10199/test-status` — JSON snapshot of current results
- `GET :10199/test-stream` — SSE stream of test output lines
- `GET :10199/test-stream?log=1` — SSE stream with system log lines interleaved
- `POST :10199/test-start` — Signal client connected (triggers test start)

SSE events: `output` (test lines), `log` (system log lines with `src` and optional `overwrite` for progress bars), `result` (final JSON). Heartbeat comments (`: heartbeat`) sent every 5s to keep connections alive.

### Library (`lib.sh`)

Sourced by every test. Provides:

| Function | Type | Description |
|----------|------|-------------|
| `test_pass "msg"` | Exit | Report success, exit 0 |
| `test_fail "msg"` | Exit | Report failure, exit 1 |
| `test_fatal "msg"` | Exit | Report failure and abort suite, exit 2 |
| `test_skip "msg"` | Exit | Report skip, exit 77 |
| `fail_later "label" "msg"` | Deferred | Record failure without exiting |
| `report_failures` | Deferred | Exit with failure if any `fail_later` calls were made |
| `has_gpu` | Predicate | `nvidia-smi` succeeds |
| `is_serverless` | Predicate | `$SERVERLESS` is "true" |
| `is_vast_image` | Predicate | `$IMAGE_TYPE` is "vast" |
| `portal_has_entry "term"` | Predicate | grep for term in `/etc/portal.yaml` |
| `version_gt A B` | Predicate | Dotted version comparison (e.g. `12.10` > `12.9`) |
| `instance_field "key"` | Query | Read field from cached API metadata JSON |
| `wait_for_url URL [timeout]` | Wait | Poll for HTTP 200 |
| `wait_for_port PORT [timeout]` | Wait | Poll for TCP listener |
| `wait_for_caddy [port] [proto]` | Wait | Poll for Caddy (accepts 401 as responsive) |
| `assert_file_exists PATH` | Assert | |
| `assert_dir_exists PATH` | Assert | |
| `assert_file_mode PATH OCTAL` | Assert | `stat -c '%a'` comparison (use `440` not `0440`) |
| `assert_command_exists CMD` | Assert | |
| `assert_service_running NAME` | Assert | supervisorctl RUNNING |
| `assert_service_stopped NAME` | Assert | supervisorctl STOPPED/EXITED/FATAL |
| `assert_env_set VARNAME` | Assert | Non-empty env var |
| `assert_user_exists USER [UID]` | Assert | `id -u` check |

**Instance metadata:** Test 11 queries the Vast API and caches the full instance JSON at `/tmp/instance-test-metadata.json`. Use `instance_field "gpu_name"` etc. from any test that runs after 11.

## Test ordering

Tests execute in filename sort order. The numbering creates two phases:

| Range | Phase | Description |
|-------|-------|-------------|
| 10–11 | Pre-provisioning | Supervisor alive, instance identity/API, basic infrastructure |
| 12 | **Provisioning gate** | Monitors provisioning for activity; blocks until done or hung |
| 15–60 | Post-provisioning | Boot markers, portal, caddy, networking, filesystem, users, python, binaries, env, GPU/CUDA |
| 65–85 | Service validation | Supervisor service states, functional HTTP checks, logging, cron, serverless negative checks |

**Why ordering matters:** Provisioning (step 12) can register new supervisor services, install packages, and download models. Tests that check service states or installed software must run after it.

## Writing a new test

### Template

```bash
#!/bin/bash
# Test: brief description of what this validates.
source "$(dirname "$0")/../lib.sh"

# Skip entire test if precondition not met
has_gpu || test_skip "no GPU detected"

# Do checks...
assert_command_exists something
some_output=$(some_command 2>&1) || test_fail "some_command failed"

# Informational output (shown in runner log, not in JSON)
echo "  detail: ${some_output}"

# End with exactly one test_pass
test_pass "description of what passed"
```

### Key rules

1. **Source `lib.sh`** — always the first non-comment line.

2. **Exit codes** — `test_pass` (0), `test_fail` (1), `test_fatal` (2, aborts entire suite), `test_skip` (77). The runner interprets these. Never `exit` directly.

3. **Skip, don't fail on absent features** — if a feature may not exist on all images (e.g. `/venv/main`, `/opt/nvm`, specific binaries), check for its presence and skip that check gracefully. Only fail if something that *should* be there is broken.

4. **One `test_pass` at the end** — a test must reach exactly one `test_pass` call. If you need to check multiple things that can each fail independently, use the `fail_later` pattern (see `65-conditional-services.sh`) to collect failures and report them all at the end.

5. **Use `echo "  ..."` for details** — indented with two spaces. These appear in the runner's stdout log. Keep them concise.

6. **No `local` at top level** — test scripts run at the top level (not inside a function), so `local` is invalid. Use it inside functions you define within the test.

7. **Custom timeout** — for long-running tests, add `# TEST_TIMEOUT=N` as a comment near the top of the script. The runner scans the entire file with `grep ^# TEST_TIMEOUT=` so it can appear on any line (convention: in the header block, after the shebang and description).

8. **Boot scripts are sourced** — if adding a boot script to `/etc/vast_boot.d/`, never use `exit` (it kills the boot sequence). Use `return` instead.

### Derivative tests

Derivative images (pytorch, comfyui, etc.) add tests by creating a directory named `<derivative>.d/` alongside `base/`:

```
tests/
├── base/           # Base image tests
├── pytorch.d/      # Added by pytorch derivative
│   ├── 10-torch.sh
│   └── 20-cuda-runtime.sh
└── comfyui.d/      # Added by comfyui derivative
    └── 10-comfyui.sh
```

The runner discovers `*.d/` directories automatically. Tests within each directory are sorted and run after all `base/` tests. Derivative tests have access to everything in `lib.sh` including `instance_field`.

## Environment variables

### Runner configuration
| Variable | Default | Description |
|----------|---------|-------------|
| `INSTANCE_TEST` | — | Set to "true" to launch runner from boot script |
| `INSTANCE_TEST_RESULTS` | `/var/log/test-results.json` | Results file path |
| `INSTANCE_TEST_LOG` | `/var/log/test-output.log` | Test output log path |
| `INSTANCE_TEST_PORT` | `10199` | HTTP results server port |
| `INSTANCE_TEST_DEFAULT_TIMEOUT` | `3600` | Per-test timeout in seconds (overridable per-test via `# TEST_TIMEOUT=N`) |
| `INSTANCE_TEST_SYSTEM_LOG` | — | Comma-separated log file paths to stream to client (e.g. `/var/log/portal/vllm.log`) |
| `INSTANCE_TEST_WEBHOOK` | — | URL to POST results JSON to on completion |

### Provisioning test configuration
| Variable | Default | Description |
|----------|---------|-------------|
| `PROV_STALL_TIMEOUT` | `180` | Seconds with no activity before declaring hung |
| `PROV_TIMEOUT` | `3600` | Maximum total provisioning time |

### Instance variables (set by platform)
| Variable | Description |
|----------|-------------|
| `CONTAINER_ID` | Instance ID (required) |
| `CONTAINER_API_KEY` | API key for this instance (required) |
| `SERVERLESS` | "true" if serverless mode |
| `WORKSPACE` | Workspace directory path |
| `PYTHON_VERSION` | Expected Python version for venv |
| `PROVISIONING_MANIFEST` | URL/path to provisioning manifest |
| `PROVISIONING_SCRIPT` | URL to legacy provisioning script |

## Debugging

```bash
# Run all tests interactively (auto-detects TTY → manual mode)
/opt/instance-tools/tests/runner.sh

# Run a single test
cd /opt/instance-tools/tests
TEST_NAME=base/60-gpu-cuda bash base/60-gpu-cuda.sh

# Run with simulated serverless mode
SERVERLESS=true /opt/instance-tools/tests/runner.sh

# Check cached instance metadata
cat /tmp/instance-test-metadata.json | python3 -m json.tool

# Read results
cat /var/log/test-results.json | python3 -m json.tool
```
