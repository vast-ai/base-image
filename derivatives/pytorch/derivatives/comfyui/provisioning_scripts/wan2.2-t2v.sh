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
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors
  |$MODELS_DIR/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors
  |$MODELS_DIR/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"
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
  "last_node_id": 113,
  "last_link_id": 188,
  "nodes": [
    {
      "id": 71,
      "type": "CLIPLoader",
      "pos": [
        50,
        50
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
            141,
            160
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
        ]
      },
      "widgets_values": [
        "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "wan",
        "default"
      ]
    },
    {
      "id": 73,
      "type": "VAELoader",
      "pos": [
        50,
        210
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
            158
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
        ]
      },
      "widgets_values": [
        "wan_2.1_vae.safetensors"
      ]
    },
    {
      "id": 76,
      "type": "UNETLoader",
      "pos": [
        50,
        -80
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
            155
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
            "directory": "diffusion_models"
          }
        ]
      },
      "widgets_values": [
        "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "default"
      ]
    },
    {
      "id": 75,
      "type": "UNETLoader",
      "pos": [
        50,
        -210
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
            153
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            "directory": "diffusion_models"
          }
        ]
      },
      "widgets_values": [
        "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "default"
      ]
    },
    {
      "id": 83,
      "type": "LoraLoaderModelOnly",
      "pos": [
        450,
        -200
      ],
      "size": [
        280,
        82
      ],
      "flags": {},
      "order": 9,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 153
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            152
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.49",
        "Node name for S&R": "LoraLoaderModelOnly",
        "models": [
          {
            "name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
            "directory": "loras"
          }
        ]
      },
      "widgets_values": [
        "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        1.0000000000000002
      ]
    },
    {
      "id": 85,
      "type": "LoraLoaderModelOnly",
      "pos": [
        450,
        -60
      ],
      "size": [
        280,
        82
      ],
      "flags": {},
      "order": 8,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 155
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            156
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.49",
        "Node name for S&R": "LoraLoaderModelOnly",
        "models": [
          {
            "name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
            "directory": "loras"
          }
        ]
      },
      "widgets_values": [
        "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        1.0000000000000002
      ]
    },
    {
      "id": 86,
      "type": "ModelSamplingSD3",
      "pos": [
        740,
        -60
      ],
      "size": [
        210,
        58
      ],
      "flags": {
        "collapsed": false
      },
      "order": 10,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 156
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            183
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "ModelSamplingSD3"
      },
      "widgets_values": [
        5.000000000000001
      ]
    },
    {
      "id": 82,
      "type": "ModelSamplingSD3",
      "pos": [
        740,
        -200
      ],
      "size": [
        210,
        60
      ],
      "flags": {},
      "order": 11,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 152
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            181
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "ModelSamplingSD3"
      },
      "widgets_values": [
        5.000000000000001
      ]
    },
    {
      "id": 62,
      "type": "MarkdownNote",
      "pos": [
        -470,
        -290
      ],
      "size": [
        480,
        550
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Model Links",
      "properties": {},
      "widgets_values": [
        "[Tutorial](https://docs.comfy.org/tutorials/video/wan/wan2_2\n) | [教程](https://docs.comfy.org/zh-CN/tutorials/video/wan/wan2_2\n)\n\n**Diffusion Model**       \n- [wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors)\n- [wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors)\n\n**LoRA**\n\n- [wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors)\n- [wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors)\n\n**VAE**\n- [wan_2.1_vae.safetensors](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors)\n\n**Text Encoder**   \n- [umt5_xxl_fp8_e4m3fn_scaled.safetensors](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors)\n\n\nFile save location\n\n```\nComfyUI/\n├───📂 models/\n│   ├───📂 diffusion_models/\n│   │   ├─── wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors\n│   │   └─── wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors\n│   ├───📂 loras/\n│   │   ├───wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors\n│   │   └───wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors\n│   ├───📂 text_encoders/\n│   │   └─── umt5_xxl_fp8_e4m3fn_scaled.safetensors \n│   └───📂 vae/\n│       └── wan_2.1_vae.safetensors\n```\n"
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 89,
      "type": "CLIPTextEncode",
      "pos": [
        440,
        130
      ],
      "size": [
        510,
        160
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 160
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            143,
            149
          ]
        }
      ],
      "title": "CLIP Text Encode (Positive Prompt)",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "Beautiful young European woman with honey blonde hair gracefully turning her head back over shoulder, gentle smile, bright eyes looking at camera. Hair flowing in slow motion as she turns. Soft natural lighting, clean background, cinematic slow-motion portrait."
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 81,
      "type": "KSamplerAdvanced",
      "pos": [
        990,
        -250
      ],
      "size": [
        300,
        546
      ],
      "flags": {},
      "order": 12,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 181
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 149
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 150
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 151
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            145
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "KSamplerAdvanced"
      },
      "widgets_values": [
        "enable",
        392459563371087,
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
      "id": 88,
      "type": "CreateVideo",
      "pos": [
        1320,
        460
      ],
      "size": [
        270,
        78
      ],
      "flags": {},
      "order": 15,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 159
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
            147
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "CreateVideo"
      },
      "widgets_values": [
        16
      ]
    },
    {
      "id": 80,
      "type": "SaveVideo",
      "pos": [
        1660,
        -240
      ],
      "size": [
        704,
        802
      ],
      "flags": {},
      "order": 16,
      "mode": 0,
      "inputs": [
        {
          "name": "video",
          "type": "VIDEO",
          "link": 147
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "SaveVideo"
      },
      "widgets_values": [
        "video/ComfyUI",
        "auto",
        "auto"
      ]
    },
    {
      "id": 87,
      "type": "VAEDecode",
      "pos": [
        1020,
        470
      ],
      "size": [
        210,
        46
      ],
      "flags": {},
      "order": 14,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 157
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 158
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "slot_index": 0,
          "links": [
            159
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "VAEDecode"
      },
      "widgets_values": []
    },
    {
      "id": 72,
      "type": "CLIPTextEncode",
      "pos": [
        440,
        330
      ],
      "size": [
        510,
        180
      ],
      "flags": {},
      "order": 6,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 141
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            144,
            150
          ]
        }
      ],
      "title": "CLIP Text Encode (Negative Prompt)",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走，裸露，NSFW"
      ],
      "color": "#322",
      "bgcolor": "#533"
    },
    {
      "id": 74,
      "type": "EmptyHunyuanLatentVideo",
      "pos": [
        70,
        380
      ],
      "size": [
        315,
        130
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            151
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "EmptyHunyuanLatentVideo"
      },
      "widgets_values": [
        640,
        640,
        81,
        1
      ]
    },
    {
      "id": 78,
      "type": "KSamplerAdvanced",
      "pos": [
        1310,
        -250
      ],
      "size": [
        304.748046875,
        546
      ],
      "flags": {},
      "order": 13,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 183
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 143
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 144
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 145
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            157
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.45",
        "Node name for S&R": "KSamplerAdvanced"
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
    }
  ],
  "links": [
    [
      141,
      71,
      0,
      72,
      0,
      "CLIP"
    ],
    [
      143,
      89,
      0,
      78,
      1,
      "CONDITIONING"
    ],
    [
      144,
      72,
      0,
      78,
      2,
      "CONDITIONING"
    ],
    [
      145,
      81,
      0,
      78,
      3,
      "LATENT"
    ],
    [
      147,
      88,
      0,
      80,
      0,
      "VIDEO"
    ],
    [
      149,
      89,
      0,
      81,
      1,
      "CONDITIONING"
    ],
    [
      150,
      72,
      0,
      81,
      2,
      "CONDITIONING"
    ],
    [
      151,
      74,
      0,
      81,
      3,
      "LATENT"
    ],
    [
      152,
      83,
      0,
      82,
      0,
      "MODEL"
    ],
    [
      153,
      75,
      0,
      83,
      0,
      "MODEL"
    ],
    [
      155,
      76,
      0,
      85,
      0,
      "MODEL"
    ],
    [
      156,
      85,
      0,
      86,
      0,
      "MODEL"
    ],
    [
      157,
      78,
      0,
      87,
      0,
      "LATENT"
    ],
    [
      158,
      73,
      0,
      87,
      1,
      "VAE"
    ],
    [
      159,
      87,
      0,
      88,
      0,
      "IMAGE"
    ],
    [
      160,
      71,
      0,
      89,
      0,
      "CLIP"
    ],
    [
      181,
      82,
      0,
      81,
      0,
      "MODEL"
    ],
    [
      183,
      86,
      0,
      78,
      0,
      "MODEL"
    ]
  ],
  "groups": [
    {
      "id": 6,
      "title": "Step3 Prompt",
      "bounding": [
        430,
        60,
        530,
        460
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 7,
      "title": "Lightx2v 4steps LoRA",
      "bounding": [
        430,
        -280,
        530,
        320
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 13,
      "title": "Wan2.2 T2V fp8_scaled +  4 steps LoRA",
      "bounding": [
        30,
        -320,
        2366.705474768146,
        901.1041650801983
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 11,
      "title": "Step 1 - Load models",
      "bounding": [
        40,
        -280,
        366.7470703125,
        563.5814208984375
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 12,
      "title": "Step 2 - Video size",
      "bounding": [
        40,
        300,
        370,
        230
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    }
  ],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.630394086312964,
      "offset": [
        493.23738310808494,
        355.3790712530013
      ]
    },
    "frontendVersion": "1.28.7",
    "VHS_latentpreview": false,
    "VHS_latentpreviewrate": 0,
    "VHS_MetadataImage": true,
    "VHS_KeepIntermediate": true
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/wan-2.2-Text-to-Video.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "71": {
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
  "72": {
    "inputs": {
      "text": "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走，裸露，NSFW",
      "clip": [
        "71",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Negative Prompt)"
    }
  },
  "73": {
    "inputs": {
      "vae_name": "wan_2.1_vae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "74": {
    "inputs": {
      "width": 640,
      "height": 640,
      "length": 81,
      "batch_size": 1
    },
    "class_type": "EmptyHunyuanLatentVideo",
    "_meta": {
      "title": "EmptyHunyuanLatentVideo"
    }
  },
  "75": {
    "inputs": {
      "unet_name": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "76": {
    "inputs": {
      "unet_name": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "78": {
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
        "86",
        0
      ],
      "positive": [
        "89",
        0
      ],
      "negative": [
        "72",
        0
      ],
      "latent_image": [
        "81",
        0
      ]
    },
    "class_type": "KSamplerAdvanced",
    "_meta": {
      "title": "KSampler (Advanced)"
    }
  },
  "80": {
    "inputs": {
      "filename_prefix": "video/ComfyUI",
      "format": "auto",
      "codec": "auto",
      "video": [
        "88",
        0
      ]
    },
    "class_type": "SaveVideo",
    "_meta": {
      "title": "Save Video"
    }
  },
  "81": {
    "inputs": {
      "add_noise": "enable",
      "noise_seed": 392459563371087,
      "steps": 4,
      "cfg": 1,
      "sampler_name": "euler",
      "scheduler": "simple",
      "start_at_step": 0,
      "end_at_step": 2,
      "return_with_leftover_noise": "enable",
      "model": [
        "82",
        0
      ],
      "positive": [
        "89",
        0
      ],
      "negative": [
        "72",
        0
      ],
      "latent_image": [
        "74",
        0
      ]
    },
    "class_type": "KSamplerAdvanced",
    "_meta": {
      "title": "KSampler (Advanced)"
    }
  },
  "82": {
    "inputs": {
      "shift": 5.000000000000001,
      "model": [
        "83",
        0
      ]
    },
    "class_type": "ModelSamplingSD3",
    "_meta": {
      "title": "ModelSamplingSD3"
    }
  },
  "83": {
    "inputs": {
      "lora_name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
      "strength_model": 1.0000000000000002,
      "model": [
        "75",
        0
      ]
    },
    "class_type": "LoraLoaderModelOnly",
    "_meta": {
      "title": "LoraLoaderModelOnly"
    }
  },
  "85": {
    "inputs": {
      "lora_name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
      "strength_model": 1.0000000000000002,
      "model": [
        "76",
        0
      ]
    },
    "class_type": "LoraLoaderModelOnly",
    "_meta": {
      "title": "LoraLoaderModelOnly"
    }
  },
  "86": {
    "inputs": {
      "shift": 5.000000000000001,
      "model": [
        "85",
        0
      ]
    },
    "class_type": "ModelSamplingSD3",
    "_meta": {
      "title": "ModelSamplingSD3"
    }
  },
  "87": {
    "inputs": {
      "samples": [
        "78",
        0
      ],
      "vae": [
        "73",
        0
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "88": {
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
  "89": {
    "inputs": {
      "text": "Beautiful young European woman with honey blonde hair gracefully turning her head back over shoulder, gentle smile, bright eyes looking at camera. Hair flowing in slow motion as she turns. Soft natural lighting, clean background, cinematic slow-motion portrait.",
      "clip": [
        "71",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Positive Prompt)"
    }
  }
}
WORKFLOW_API_JSON
    payload_json=$(jq -n --argjson workflow "$workflow_json" '{input: {workflow_json: $workflow}}')
    rm /opt/comfyui-api-wrapper/payloads/*.json
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/Wan-2.2-Text-to-Video.json
}

main