# Workflow-to-API Conversion Design

## Problem

ComfyUI workflows in GUI format (the standard export with `nodes`, `links`, etc.) cannot be used directly for programmatic execution via the `/prompt` API endpoint. The API wrapper at `/opt/comfyui-api-wrapper` needs API-format workflow payloads to drive ComfyUI headlessly.

Currently, the `provisioner_comfyui` extension downloads GUI-format workflows and saves them to `${WORKSPACE}/ComfyUI/user/default/workflows/`, but no conversion to API format occurs. Existing provisioning scripts (e.g. `flux.2-dev.sh`) embed hand-crafted API workflows inline — this approach doesn't scale.

## Solution

A standalone conversion script that runs as a pre-start step in the API wrapper's supervisor lifecycle. It waits for ComfyUI to be ready, converts GUI-format workflows to API format via the `/workflow/convert` endpoint (provided by the [comfyui-workflow-to-api-converter-endpoint](https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint) custom node), post-processes seed values, wraps the result in the API wrapper's expected envelope format, and writes the results to the payloads directory.

## Why ComfyUI Must Be Running

The converter uses ComfyUI's live node registry (`NODE_CLASS_MAPPINGS`) to accurately map widget values to API inputs. This registry is only populated when ComfyUI loads its nodes at startup. A standalone conversion without ComfyUI would be inaccurate — accuracy is a hard requirement.

## Architecture

```
provisioner_comfyui          comfyui.sh (supervisor)        api-wrapper.sh (supervisor)
       |                            |                              |
  Downloads workflows,         Starts ComfyUI                Waits for provisioning
  models, custom nodes         on port 18188                       |
  Saves GUI workflows to            |                     Calls convert-workflows.sh
  .../workflows/                    |                              |
       |                            |                     Waits for ComfyUI ready
       v                            v                              |
  /.provisioning removed       ComfyUI listening            POSTs to /workflow/convert
                                                                   |
                                                           Post-processes seeds
                                                                   |
                                                           Wraps in {input: {workflow_json: ...}}
                                                                   |
                                                           Writes to /opt/comfyui-api-wrapper/payloads/
                                                                   |
                                                           Starts uvicorn
```

## Components

### 1. convert-workflows.sh (new file)

**Location:** `/opt/comfyui-api-wrapper/convert-workflows.sh`

**Environment variables:**
- `WORKSPACE` — ComfyUI workspace root (default: `/workspace`)
- `COMFYUI_PORT` — ComfyUI listen port (default: `18188`)
- `COMFYUI_READY_TIMEOUT` — seconds to wait for ComfyUI (default: `300`)

**Algorithm:**

1. Define source directory: `${WORKSPACE}/ComfyUI/user/default/workflows/`
2. Define output directory: `/opt/comfyui-api-wrapper/payloads/`
3. Ensure output directory exists (`mkdir -p`)
4. Scan source directory for `*.json` files (top-level only, no recursion). If none found, log and exit 0.
5. Wait for ComfyUI to be ready:
   - Poll `http://localhost:${COMFYUI_PORT}/api/system_stats` every 2 seconds
   - Timeout after `${COMFYUI_READY_TIMEOUT}` seconds — log error and exit 1
6. For each `.json` file in source directory:
   - Read the file
   - Check if GUI format: presence of `"nodes"` key (use `jq` to test)
   - If not GUI format (already API or unrecognized), skip with log message
   - POST file contents to `http://localhost:${COMFYUI_PORT}/workflow/convert`
     - The endpoint returns the raw API-format JSON directly on success, or `{"error": "..."}` on failure
   - If HTTP error or response contains `"error"` key: log warning, increment failure count, continue
   - Post-process the response JSON: replace integer seed values with `"__RANDOM_INT__"` (see below)
   - Wrap in the API wrapper envelope: `{"input": {"workflow_json": <api_json>}}`
   - Write wrapped payload to `${output_dir}/${filename}`
   - Increment success count
7. Log summary: N converted, N skipped, N failed
8. Exit 0 (individual failures are non-fatal to allow partial conversion)

**Payload envelope format:**

The API wrapper expects payloads in this format (consistent with existing provisioning scripts like `flux.2-dev.sh`):

```json
{
  "input": {
    "workflow_json": {
      "6": {
        "class_type": "KSampler",
        "inputs": { ... }
      }
    }
  }
}
```

The wrapping is done with `jq`:

```bash
jq -n --argjson workflow "$api_json" '{input: {workflow_json: $workflow}}'
```

**Seed replacement logic:**

In the API-format JSON, seed values appear as integer inputs within node definitions. The script replaces any integer value under keys named `seed` or `noise_seed` across all nodes:

```bash
jq 'walk(
  if type == "object" and .inputs then
    .inputs |= (
      if has("seed") and (.seed | type == "number") then .seed = "__RANDOM_INT__" else . end |
      if has("noise_seed") and (.noise_seed | type == "number") then .noise_seed = "__RANDOM_INT__" else . end
    )
  else .
  end
)'
```

Before:
```json
{"3": {"class_type": "KSampler", "inputs": {"seed": 842135}}}
```

After:
```json
{"3": {"class_type": "KSampler", "inputs": {"seed": "__RANDOM_INT__"}}}
```

**Interaction with existing payloads:**

The script does not clear the payloads directory before writing. If a provisioning script has already written a hand-crafted payload with the same filename, the conversion will overwrite it. This is intentional — the dynamically converted version is preferred over stale hand-crafted payloads. Provisioning scripts that write payloads should use distinct filenames if they need to coexist.

### 2. api-wrapper.sh (modified)

Add the conversion step between provisioning wait and uvicorn launch. The conversion script's exit code is intentionally not checked — if it fails (e.g. ComfyUI timeout), the API wrapper still starts, it just won't have converted payloads:

```bash
# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

# Convert GUI workflows to API format (best-effort, non-blocking)
/opt/comfyui-api-wrapper/convert-workflows.sh || true

# Launch ComfyUI API Wrapper
cd /opt/comfyui-api-wrapper
. .venv/bin/activate
pty uvicorn main:app --port 18288 2>&1
```

**Serverless note:** In `SERVERLESS` mode, the conversion still runs. The timeout is the main concern for cold-start latency, but ComfyUI must be loaded anyway for the wrapper to function, so the wait is unavoidable.

### 3. Dockerfile (user-managed)

The Seth Robinson converter custom node must be baked into the image. This is outside the scope of this spec — the user will add the appropriate `git clone` or `COPY` directive to the Dockerfile.

## What Does NOT Change

- **provisioner_comfyui extension** — continues to download workflows, extract models/custom nodes, and save GUI workflows. No modifications needed.
- **comfyui.sh supervisor script** — ComfyUI starts normally via supervisor, unaware of the conversion process.
- **Provisioning flow** — the conversion is entirely post-provisioning.

## Dependencies

- `curl` — HTTP calls to ComfyUI endpoints
- `jq` — JSON parsing, seed replacement, and envelope wrapping
- Seth Robinson's `comfyui-workflow-to-api-converter-endpoint` custom node installed in ComfyUI

## Error Handling

| Scenario | Behavior |
|----------|----------|
| No workflows in source dir | Exit 0, log info |
| ComfyUI doesn't start within timeout | Exit 1, log error. API wrapper still starts (`|| true`). |
| Individual workflow conversion fails | Log warning, continue with remaining workflows |
| `/workflow/convert` returns `{"error": ...}` | Treated as failure for that workflow, logged, continue |
| `/workflow/convert` endpoint not found (node not installed) | All conversions fail with HTTP 404, logged. API wrapper still starts. |
| Malformed JSON in source dir | `jq` check fails, file skipped |

## Testing

- Place a known GUI-format workflow in the source directory
- Start ComfyUI with the converter node installed
- Run `convert-workflows.sh` manually
- Verify output in `/opt/comfyui-api-wrapper/payloads/` has the correct envelope format: `{"input": {"workflow_json": {...}}}`
- Verify seed values are replaced with `"__RANDOM_INT__"` inside the workflow JSON
- Verify API-format or non-JSON files in the source directory are skipped
