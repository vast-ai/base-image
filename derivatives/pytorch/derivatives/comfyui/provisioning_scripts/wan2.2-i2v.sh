#!/bin/bash

set -euo pipefail

### Configuration ###
WORKSPACE_DIR="${WORKSPACE:-/workspace}"
COMFYUI_DIR="${WORKSPACE_DIR}/ComfyUI"
MODELS_DIR="${COMFYUI_DIR}/models"
WORKFLOW_DIR="${COMFYUI_DIR}/user/default/workflows"
HF_SEMAPHORE_DIR="${WORKSPACE_DIR}/hf_download_sem_$$"
HF_MAX_PARALLEL=3

# Model declarations: "URL|OUTPUT_PATH"
HF_MODELS=(
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
  |$MODELS_DIR/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors
  |$MODELS_DIR/vae/wan_2.1_vae.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors
  |$MODELS_DIR/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors
  |$MODELS_DIR/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
)
### End Configuration ###

script_cleanup() {
   rm -rf "$HF_SEMAPHORE_DIR"
}

# If this script fails we cannot let a serverless worker be marked as ready.
script_error() {
    local exit_code=$?
    local line_number=$1
    echo "[ERROR] Provisioning Script failed at line $line_number with exit code $exit_code" | tee -a "${MODEL_LOG:-/var/log/portal/comfyui.log}"
}

trap script_cleanup EXIT
trap 'script_error $LINENO' ERR

main() {
    . /venv/main/bin/activate
    mkdir -p "$HF_SEMAPHORE_DIR"
    write_workflow
    write_api_workflow
    download_input
    pids=()
    # Download all models in parallel
    for model in "${HF_MODELS[@]}"; do
        url="${model%%|*}"
        output_path="${model##*|}"
        download_hf_file "$url" "$output_path" &
        pids+=($!)
    done
    
    # Wait for each job and check exit status
    for pid in "${pids[@]}"; do
        wait "$pid" || exit 1
    done
}

download_input() {
  wget -O "$COMFYUI_DIR/input/input.jpg" https://raw.githubusercontent.com/Comfy-Org/example_workflows/refs/heads/main/video/wan/2.2/input.jpg
}

# HuggingFace download helper
download_hf_file() {
  local url="$1"
  local output_path="$2"
  local lockfile="${output_path}.lock"
  local max_retries=5
  local retry_delay=2
  
  # Acquire slot for parallel download limiting
  local slot=$(acquire_slot)
  
  # Acquire lock for this specific file
  while ! mkdir "$lockfile" 2>/dev/null; do
    echo "Another process is downloading to $output_path (waiting...)"
    sleep 1
  done
  
  # Check if file already exists
  if [ -f "$output_path" ]; then
    echo "File already exists: $output_path (skipping)"
    rmdir "$lockfile"
    release_slot "$slot"
    return 0
  fi
  
  # Extract repo and file path
  local repo=$(echo "$url" | sed -n 's|https://huggingface.co/\([^/]*/[^/]*\)/resolve/.*|\1|p')
  local file_path=$(echo "$url" | sed -n 's|https://huggingface.co/[^/]*/[^/]*/resolve/[^/]*/\(.*\)|\1|p')
  
  if [ -z "$repo" ] || [ -z "$file_path" ]; then
    echo "ERROR: Invalid HuggingFace URL: $url"
    rmdir "$lockfile"
    release_slot "$slot"
    return 1
  fi
  
  local temp_dir=$(mktemp -d)
  local attempt=1
  
  # Retry loop for rate limits and transient failures
  while [ $attempt -le $max_retries ]; do
    echo "Downloading $file_path (attempt $attempt/$max_retries)..."
    
    if hf download "$repo" \
      "$file_path" \
      --local-dir "$temp_dir" \
      --cache-dir "$temp_dir/.cache" 2>&1; then
      
      # Success - move file and clean up
      mkdir -p "$(dirname "$output_path")"
      mv "$temp_dir/$file_path" "$output_path"
      rm -rf "$temp_dir"
      rmdir "$lockfile"
      release_slot "$slot"
      echo "✓ Successfully downloaded: $output_path"
      return 0
    else
      echo "✗ Download failed (attempt $attempt/$max_retries), retrying in ${retry_delay}s..."
      sleep $retry_delay
      retry_delay=$((retry_delay * 2))  # Exponential backoff
      attempt=$((attempt + 1))
    fi
  done
  
  # All retries failed
  echo "ERROR: Failed to download $output_path after $max_retries attempts"
  rm -rf "$temp_dir"
  rmdir "$lockfile"
  release_slot "$slot"
  return 1
}

acquire_slot() {
  while true; do
    local count=$(find "$HF_SEMAPHORE_DIR" -name "slot_*" 2>/dev/null | wc -l)
    if [ $count -lt $HF_MAX_PARALLEL ]; then
      local slot="$HF_SEMAPHORE_DIR/slot_$$_$RANDOM"
      touch "$slot"
      echo "$slot"
      return 0
    fi
    sleep 0.5
  done
}

release_slot() {
  rm -f "$1"
}

# This workflow is as provided by ComfyUI template browser
write_workflow() {
    mkdir -p "${WORKFLOW_DIR}"
    local workflow_json
    read -r -d '' workflow_json << 'WORKFLOW_JSON' || true
{
  "id": "ec7da562-7e21-4dac-a0d2-f4441e1efd3b",
  "revision": 0,
  "last_node_id": 115,
  "last_link_id": 217,
  "nodes": [
    {
      "id": 84,
      "type": "CLIPLoader",
      "pos": [
        60,
        30
      ],
      "size": [
        346.391845703125,
        106
      ],
      "flags": {},
      "order": 0,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "CLIP",
          "type": "CLIP",
          "slot_index": 0,
          "links": [
            178,
            181
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "CLIPLoader",
        "models": [
          {
            "name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "directory": "text_encoders"
          }
        ],
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "wan",
        "default"
      ]
    },
    {
      "id": 90,
      "type": "VAELoader",
      "pos": [
        60,
        190
      ],
      "size": [
        344.731689453125,
        59.98149108886719
      ],
      "flags": {},
      "order": 1,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "VAE",
          "type": "VAE",
          "slot_index": 0,
          "links": [
            176,
            185
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "VAELoader",
        "models": [
          {
            "name": "wan_2.1_vae.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors",
            "directory": "vae"
          }
        ],
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "wan_2.1_vae.safetensors"
      ]
    },
    {
      "id": 95,
      "type": "UNETLoader",
      "pos": [
        50,
        -230
      ],
      "size": [
        346.7470703125,
        82
      ],
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            194
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "directory": "diffusion_models"
          }
        ],
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "default"
      ]
    },
    {
      "id": 96,
      "type": "UNETLoader",
      "pos": [
        50,
        -100
      ],
      "size": [
        346.7470703125,
        82
      ],
      "flags": {},
      "order": 3,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            196
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            "directory": "diffusion_models"
          }
        ],
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "default"
      ]
    },
    {
      "id": 103,
      "type": "ModelSamplingSD3",
      "pos": [
        740,
        -100
      ],
      "size": [
        210,
        58
      ],
      "flags": {
        "collapsed": false
      },
      "order": 14,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 189
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            192
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "ModelSamplingSD3",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        5.000000000000001
      ]
    },
    {
      "id": 93,
      "type": "CLIPTextEncode",
      "pos": [
        440,
        90
      ],
      "size": [
        510,
        160
      ],
      "flags": {},
      "order": 9,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 181
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            183
          ]
        }
      ],
      "title": "CLIP Text Encode (Positive Prompt)",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "CLIPTextEncode",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "The white dragon warrior stands still, eyes full of determination and strength. The camera slowly moves closer or circles around the warrior, highlighting the powerful presence and heroic spirit of the character."
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 89,
      "type": "CLIPTextEncode",
      "pos": [
        440,
        290
      ],
      "size": [
        510,
        130
      ],
      "flags": {},
      "order": 8,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 178
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            184
          ]
        }
      ],
      "title": "CLIP Text Encode (Negative Prompt)",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "CLIPTextEncode",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
      ],
      "color": "#322",
      "bgcolor": "#533"
    },
    {
      "id": 101,
      "type": "LoraLoaderModelOnly",
      "pos": [
        450,
        -230
      ],
      "size": [
        280,
        82
      ],
      "flags": {},
      "order": 10,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 194
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            190
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.49",
        "Node name for S&R": "LoraLoaderModelOnly",
        "models": [
          {
            "name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            "directory": "loras"
          }
        ],
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        1.0000000000000002
      ]
    },
    {
      "id": 102,
      "type": "LoraLoaderModelOnly",
      "pos": [
        450,
        -100
      ],
      "size": [
        280,
        82
      ],
      "flags": {},
      "order": 11,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 196
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            189
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.49",
        "Node name for S&R": "LoraLoaderModelOnly",
        "models": [
          {
            "name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            "directory": "loras"
          }
        ],
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        1.0000000000000002
      ]
    },
    {
      "id": 105,
      "type": "MarkdownNote",
      "pos": [
        -470,
        280
      ],
      "size": [
        480,
        180
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "VRAM Usage",
      "properties": {
        "ue_properties": {
          "version": "7.1",
          "widget_ue_connectable": {},
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "## GPU:RTX4090D 24GB\n\n| Model            | Size |VRAM Usage | 1st Generation | 2nd Generation |\n|---------------------|-------|-----------|---------------|-----------------|\n| fp8_scaled               |640*640| 84%               | ≈  536s              | ≈ 513s                   |\n| fp8_scaled +  4steps LoRA  | 640*640  | 83%                | ≈ 97s               | ≈ 71s                   |"
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 104,
      "type": "ModelSamplingSD3",
      "pos": [
        740,
        -230
      ],
      "size": [
        210,
        60
      ],
      "flags": {},
      "order": 13,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 190
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            195
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "ModelSamplingSD3",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        5.000000000000001
      ]
    },
    {
      "id": 98,
      "type": "WanImageToVideo",
      "pos": [
        530,
        530
      ],
      "size": [
        342.5999755859375,
        210
      ],
      "flags": {},
      "order": 12,
      "mode": 0,
      "inputs": [
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 183
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 184
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 185
        },
        {
          "name": "clip_vision_output",
          "shape": 7,
          "type": "CLIP_VISION_OUTPUT",
          "link": null
        },
        {
          "name": "start_image",
          "shape": 7,
          "type": "IMAGE",
          "link": 186
        }
      ],
      "outputs": [
        {
          "name": "positive",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            168,
            172
          ]
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "slot_index": 1,
          "links": [
            169,
            173
          ]
        },
        {
          "name": "latent",
          "type": "LATENT",
          "slot_index": 2,
          "links": [
            174
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "WanImageToVideo",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        640,
        640,
        81,
        1
      ]
    },
    {
      "id": 94,
      "type": "CreateVideo",
      "pos": [
        1350,
        460
      ],
      "size": [
        270,
        78
      ],
      "flags": {},
      "order": 18,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 182
        },
        {
          "name": "audio",
          "shape": 7,
          "type": "AUDIO",
          "link": null
        }
      ],
      "outputs": [
        {
          "name": "VIDEO",
          "type": "VIDEO",
          "links": [
            197
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "CreateVideo",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        16
      ]
    },
    {
      "id": 87,
      "type": "VAEDecode",
      "pos": [
        1060,
        480
      ],
      "size": [
        210,
        46
      ],
      "flags": {},
      "order": 17,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 175
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 176
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "slot_index": 0,
          "links": [
            182
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "VAEDecode",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": []
    },
    {
      "id": 108,
      "type": "SaveVideo",
      "pos": [
        1690,
        -250
      ],
      "size": [
        890,
        988
      ],
      "flags": {},
      "order": 19,
      "mode": 0,
      "inputs": [
        {
          "name": "video",
          "type": "VIDEO",
          "link": 197
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.49",
        "Node name for S&R": "SaveVideo",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "video/ComfyUI",
        "auto",
        "auto"
      ]
    },
    {
      "id": 115,
      "type": "Note",
      "pos": [
        30,
        -470
      ],
      "size": [
        360,
        100
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "About 4 Steps LoRA",
      "properties": {
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "Using the Wan2.2 Lighting LoRA will result in the loss of video dynamics, but it will reduce the generation time. This template provides two workflows, and you can enable one as needed."
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 86,
      "type": "KSamplerAdvanced",
      "pos": [
        990,
        -250
      ],
      "size": [
        304.748046875,
        546
      ],
      "flags": {},
      "order": 15,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 195
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 172
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 173
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 174
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            170
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "KSamplerAdvanced",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "enable",
        138073435077572,
        "randomize",
        4,
        1,
        "euler",
        "simple",
        0,
        2,
        "enable"
      ]
    },
    {
      "id": 85,
      "type": "KSamplerAdvanced",
      "pos": [
        1336.748046875,
        -250
      ],
      "size": [
        304.748046875,
        546
      ],
      "flags": {},
      "order": 16,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 192
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 168
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 169
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 170
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            175
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "KSamplerAdvanced",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "disable",
        0,
        "fixed",
        4,
        1,
        "euler",
        "simple",
        2,
        4,
        "disable"
      ]
    },
    {
      "id": 66,
      "type": "MarkdownNote",
      "pos": [
        -470,
        -320
      ],
      "size": [
        480,
        530
      ],
      "flags": {},
      "order": 6,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Model Links",
      "properties": {
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "[Tutorial](https://docs.comfy.org/tutorials/video/wan/wan2_2\n)\n\n**Diffusion Model**\n- [wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors)\n- [wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors)\n\n**LoRA**\n- [wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors)\n- [wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors)\n\n**VAE**\n- [wan_2.1_vae.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors)\n\n**Text Encoder**   \n- [umt5_xxl_fp8_e4m3fn_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors)\n\n\nFile save location\n\n```\nComfyUI/\n├───📂 models/\n│   ├───📂 diffusion_models/\n│   │   ├─── wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors\n│   │   └─── wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors\n│   ├───📂 loras/\n│   │   ├─── wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors\n│   │   └─── wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors\n│   ├───📂 text_encoders/\n│   │   └─── umt5_xxl_fp8_e4m3fn_scaled.safetensors \n│   └───📂 vae/\n│       └── wan_2.1_vae.safetensors\n```\n"
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 97,
      "type": "LoadImage",
      "pos": [
        70,
        400
      ],
      "size": [
        315,
        314.0001220703125
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "slot_index": 0,
          "links": [
            186
          ]
        },
        {
          "name": "MASK",
          "type": "MASK",
          "slot_index": 1,
          "links": null
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "LoadImage",
        "ue_properties": {
          "widget_ue_connectable": {},
          "version": "7.1",
          "input_ue_unconnectable": {}
        }
      },
      "widgets_values": [
        "input.jpg",
        "image"
      ]
    }
  ],
  "links": [
    [
      168,
      98,
      0,
      85,
      1,
      "CONDITIONING"
    ],
    [
      169,
      98,
      1,
      85,
      2,
      "CONDITIONING"
    ],
    [
      170,
      86,
      0,
      85,
      3,
      "LATENT"
    ],
    [
      172,
      98,
      0,
      86,
      1,
      "CONDITIONING"
    ],
    [
      173,
      98,
      1,
      86,
      2,
      "CONDITIONING"
    ],
    [
      174,
      98,
      2,
      86,
      3,
      "LATENT"
    ],
    [
      175,
      85,
      0,
      87,
      0,
      "LATENT"
    ],
    [
      176,
      90,
      0,
      87,
      1,
      "VAE"
    ],
    [
      178,
      84,
      0,
      89,
      0,
      "CLIP"
    ],
    [
      181,
      84,
      0,
      93,
      0,
      "CLIP"
    ],
    [
      182,
      87,
      0,
      94,
      0,
      "IMAGE"
    ],
    [
      183,
      93,
      0,
      98,
      0,
      "CONDITIONING"
    ],
    [
      184,
      89,
      0,
      98,
      1,
      "CONDITIONING"
    ],
    [
      185,
      90,
      0,
      98,
      2,
      "VAE"
    ],
    [
      186,
      97,
      0,
      98,
      4,
      "IMAGE"
    ],
    [
      189,
      102,
      0,
      103,
      0,
      "MODEL"
    ],
    [
      190,
      101,
      0,
      104,
      0,
      "MODEL"
    ],
    [
      192,
      103,
      0,
      85,
      0,
      "MODEL"
    ],
    [
      194,
      95,
      0,
      101,
      0,
      "MODEL"
    ],
    [
      195,
      104,
      0,
      86,
      0,
      "MODEL"
    ],
    [
      196,
      96,
      0,
      102,
      0,
      "MODEL"
    ],
    [
      197,
      94,
      0,
      108,
      0,
      "VIDEO"
    ]
  ],
  "groups": [
    {
      "id": 15,
      "title": "fp8_scaled +  4steps LoRA",
      "bounding": [
        30,
        -350,
        2580,
        1120
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 11,
      "title": "Step1 - Load models",
      "bounding": [
        40,
        -310,
        371.0310363769531,
        571.3974609375
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 12,
      "title": "Step2 - Upload start_image",
      "bounding": [
        40,
        280,
        370,
        470
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 13,
      "title": "Step4 -  Prompt",
      "bounding": [
        430,
        20,
        530,
        420
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 14,
      "title": "Step3 - Video size & length",
      "bounding": [
        430,
        460,
        530,
        290
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 16,
      "title": "Lightx2v 4steps LoRA",
      "bounding": [
        430,
        -310,
        530,
        310
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    }
  ],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.658845000000001,
      "offset": [
        571.0362391332789,
        439.08728925161927
      ]
    },
    "frontendVersion": "1.28.7",
    "VHS_latentpreview": false,
    "VHS_latentpreviewrate": 0,
    "VHS_MetadataImage": true,
    "VHS_KeepIntermediate": true,
    "ue_links": []
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/wan-2.2-Image-to-Video.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "84": {
    "inputs": {
      "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
      "type": "wan",
      "device": "default"
    },
    "class_type": "CLIPLoader",
    "_meta": {
      "title": "Load CLIP"
    }
  },
  "85": {
    "inputs": {
      "add_noise": "disable",
      "noise_seed": 0,
      "steps": 4,
      "cfg": 1,
      "sampler_name": "euler",
      "scheduler": "simple",
      "start_at_step": 2,
      "end_at_step": 4,
      "return_with_leftover_noise": "disable",
      "model": [
        "103",
        0
      ],
      "positive": [
        "98",
        0
      ],
      "negative": [
        "98",
        1
      ],
      "latent_image": [
        "86",
        0
      ]
    },
    "class_type": "KSamplerAdvanced",
    "_meta": {
      "title": "KSampler (Advanced)"
    }
  },
  "86": {
    "inputs": {
      "add_noise": "enable",
      "noise_seed": "__RANDOM_INT__",
      "steps": 4,
      "cfg": 1,
      "sampler_name": "euler",
      "scheduler": "simple",
      "start_at_step": 0,
      "end_at_step": 2,
      "return_with_leftover_noise": "enable",
      "model": [
        "104",
        0
      ],
      "positive": [
        "98",
        0
      ],
      "negative": [
        "98",
        1
      ],
      "latent_image": [
        "98",
        2
      ]
    },
    "class_type": "KSamplerAdvanced",
    "_meta": {
      "title": "KSampler (Advanced)"
    }
  },
  "87": {
    "inputs": {
      "samples": [
        "85",
        0
      ],
      "vae": [
        "90",
        0
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "89": {
    "inputs": {
      "text": "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走",
      "clip": [
        "84",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Negative Prompt)"
    }
  },
  "90": {
    "inputs": {
      "vae_name": "wan_2.1_vae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "93": {
    "inputs": {
      "text": "The white dragon warrior stands still, eyes full of determination and strength. The camera slowly moves closer or circles around the warrior, highlighting the powerful presence and heroic spirit of the character.",
      "clip": [
        "84",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Positive Prompt)"
    }
  },
  "94": {
    "inputs": {
      "fps": 16,
      "images": [
        "87",
        0
      ]
    },
    "class_type": "CreateVideo",
    "_meta": {
      "title": "Create Video"
    }
  },
  "95": {
    "inputs": {
      "unet_name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "96": {
    "inputs": {
      "unet_name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "97": {
    "inputs": {
      "image": "https://raw.githubusercontent.com/Comfy-Org/example_workflows/refs/heads/main/video/wan/2.2/input.jpg"
    },
    "class_type": "LoadImage",
    "_meta": {
      "title": "Load Image"
    }
  },
  "98": {
    "inputs": {
      "width": 640,
      "height": 640,
      "length": 81,
      "batch_size": 1,
      "positive": [
        "93",
        0
      ],
      "negative": [
        "89",
        0
      ],
      "vae": [
        "90",
        0
      ],
      "start_image": [
        "97",
        0
      ]
    },
    "class_type": "WanImageToVideo",
    "_meta": {
      "title": "WanImageToVideo"
    }
  },
  "101": {
    "inputs": {
      "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
      "strength_model": 1.0000000000000002,
      "model": [
        "95",
        0
      ]
    },
    "class_type": "LoraLoaderModelOnly",
    "_meta": {
      "title": "LoraLoaderModelOnly"
    }
  },
  "102": {
    "inputs": {
      "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
      "strength_model": 1.0000000000000002,
      "model": [
        "96",
        0
      ]
    },
    "class_type": "LoraLoaderModelOnly",
    "_meta": {
      "title": "LoraLoaderModelOnly"
    }
  },
  "103": {
    "inputs": {
      "shift": 5.000000000000001,
      "model": [
        "102",
        0
      ]
    },
    "class_type": "ModelSamplingSD3",
    "_meta": {
      "title": "ModelSamplingSD3"
    }
  },
  "104": {
    "inputs": {
      "shift": 5.000000000000001,
      "model": [
        "101",
        0
      ]
    },
    "class_type": "ModelSamplingSD3",
    "_meta": {
      "title": "ModelSamplingSD3"
    }
  },
  "108": {
    "inputs": {
      "filename_prefix": "video/ComfyUI",
      "format": "auto",
      "codec": "auto",
      "video": [
        "94",
        0
      ]
    },
    "class_type": "SaveVideo",
    "_meta": {
      "title": "Save Video"
    }
  }
}
WORKFLOW_API_JSON
    payload_json=$(jq -n --argjson workflow "$workflow_json" '{input: {workflow_json: $workflow}}')
    rm /opt/comfyui-api-wrapper/payloads/*.json
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/Wan-2.2-Image-to-Video.json
}

main