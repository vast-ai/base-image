#!/bin/bash

# We operating only on the ComfyUI provided by the image.
# Volume stored installs are to be managed by the user

[[ -d "${WORKSPACE}/ComfyUI" ]] && exec /opt/instance-tools/bin/entrypoint_base.sh "$@"

# Update ComfyUI
COMFYUI_DIR="/opt/workspace-internal/ComfyUI"

if [[ "${COMFYUI_VERSION:-latest}" = "latest" ]]; then
    tag=$(curl -s https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest 2>/dev/null | jq -r '.tag_name' 2>/dev/null)

    if [[ "$tag" == "null" || -z "$tag" ]]; then
        version="master"
    else
        version="$tag"
    fi
else
    version="$COMFYUI_VERSION"
fi

cd "$COMFYUI_DIR" && \
git fetch --tags && \
git checkout "$version" && \
# Do NOT upgrade existing packages because we will probably break something
uv pip install --python /venv/main/bin/python --no-cache-dir -r requirements.txt

# Update the API wrapper
cd /opt/comfyui-api-wrapper && \
. .venv/bin/activate && \
git pull && \
uv pip install --no-cache-dir -r requirements.txt
deactivate

exec /opt/instance-tools/bin/entrypoint_base.sh "$@"