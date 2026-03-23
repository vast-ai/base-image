#!/bin/bash
# convert-workflows.sh — Convert GUI-format ComfyUI workflows to API format
#
# Called by api-wrapper.sh before launching uvicorn. Waits for ComfyUI to be
# ready, POSTs each GUI workflow to /workflow/convert, replaces seed values
# with __RANDOM_INT__, wraps in the API wrapper envelope, and writes to the
# payloads directory.

WORKSPACE="${WORKSPACE:-/workspace}"
COMFYUI_PORT="${COMFYUI_PORT:-18188}"
COMFYUI_READY_TIMEOUT="${COMFYUI_READY_TIMEOUT:-300}"
COMFYUI_URL="http://localhost:${COMFYUI_PORT}"

SOURCE_DIR="${WORKSPACE}/ComfyUI/user/default/workflows"
OUTPUT_DIR="/opt/comfyui-api-wrapper/payloads"

converted=0
skipped=0
failed=0

log() { echo "[convert-workflows] $*"; }

mkdir -p "${OUTPUT_DIR}"

# Check for workflows to convert
shopt -s nullglob
json_files=("${SOURCE_DIR}"/*.json)
shopt -u nullglob

if [[ ${#json_files[@]} -eq 0 ]]; then
    log "No workflow files found in ${SOURCE_DIR}, nothing to convert"
    exit 0
fi

log "Found ${#json_files[@]} workflow file(s) in ${SOURCE_DIR}"

# Wait for ComfyUI to be ready
log "Waiting for ComfyUI at ${COMFYUI_URL} (timeout: ${COMFYUI_READY_TIMEOUT}s)..."
elapsed=0
while [[ $elapsed -lt $COMFYUI_READY_TIMEOUT ]]; do
    if curl -sf "${COMFYUI_URL}/api/system_stats" > /dev/null 2>&1; then
        log "ComfyUI is ready (waited ${elapsed}s)"
        break
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done

if [[ $elapsed -ge $COMFYUI_READY_TIMEOUT ]]; then
    log "ERROR: ComfyUI did not become ready within ${COMFYUI_READY_TIMEOUT}s"
    exit 1
fi

# Convert each GUI-format workflow
for filepath in "${json_files[@]}"; do
    filename=$(basename "$filepath")
    log "Processing ${filename}..."

    # Skip if output already exists (don't clobber user modifications)
    if [[ -f "${OUTPUT_DIR}/${filename}" ]]; then
        log "  Skipping ${filename} (output already exists)"
        skipped=$((skipped + 1))
        continue
    fi

    # Check if GUI format (has "nodes" key)
    if ! jq -e '.nodes' "$filepath" > /dev/null 2>&1; then
        log "  Skipping ${filename} (not GUI format)"
        skipped=$((skipped + 1))
        continue
    fi

    # POST to /workflow/convert
    response=$(curl -sf -X POST \
        -H "Content-Type: application/json" \
        -d @"$filepath" \
        "${COMFYUI_URL}/workflow/convert" 2>&1)

    if [[ $? -ne 0 ]]; then
        log "  WARNING: Failed to convert ${filename}: ${response}"
        failed=$((failed + 1))
        continue
    fi

    # Check for error in response
    if echo "$response" | jq -e '.error' > /dev/null 2>&1; then
        error_msg=$(echo "$response" | jq -r '.error')
        log "  WARNING: Conversion error for ${filename}: ${error_msg}"
        failed=$((failed + 1))
        continue
    fi

    # Replace seed values with __RANDOM_INT__
    api_json=$(echo "$response" | jq 'walk(
      if type == "object" and .inputs then
        .inputs |= (
          if has("seed") and (.seed | type == "number") then .seed = "__RANDOM_INT__" else . end |
          if has("noise_seed") and (.noise_seed | type == "number") then .noise_seed = "__RANDOM_INT__" else . end
        )
      else .
      end
    )')

    # Wrap in API wrapper envelope and write
    jq -n --argjson workflow "$api_json" '{input: {workflow_json: $workflow}}' \
        > "${OUTPUT_DIR}/${filename}"

    log "  Converted ${filename} -> ${OUTPUT_DIR}/${filename}"
    converted=$((converted + 1))
done

log "Done: ${converted} converted, ${skipped} skipped, ${failed} failed"
exit 0
