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
  "https://huggingface.co/Comfy-Org/mochi_preview_repackaged/resolve/main/split_files/diffusion_models/mochi_preview_bf16.safetensors
  |$MODELS_DIR/diffusion_models/mochi_preview_bf16.safetensors"
  "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors
  |$MODELS_DIR/text_encoders/t5xxl_fp16.safetensors"
  "https://huggingface.co/Comfy-Org/mochi_preview_repackaged/resolve/main/split_files/vae/mochi_vae.safetensors
  |$MODELS_DIR/vae/mochi_vae.safetensors"
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
    #download_input
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
  echo "Downloading input assets"
  #wget -O "$COMFYUI_DIR/input/input.jpg" https://url-to-input-asset
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
  "id": "170f57ee-ce7c-44a3-9b7d-3ee6b20405d8",
  "revision": 0,
  "last_node_id": 42,
  "last_link_id": 81,
  "nodes": [
    {
      "id": 6,
      "type": "CLIPTextEncode",
      "pos": [
        390,
        150
      ],
      "size": [
        422.8500061035156,
        164.30999755859375
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 74
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            46
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "a fox moving quickly in a beautiful winter scenery nature trees sunset tracking camera"
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 7,
      "type": "CLIPTextEncode",
      "pos": [
        390,
        350
      ],
      "size": [
        425.2799987792969,
        180.61000061035156
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 75
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            52
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        ""
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 37,
      "type": "UNETLoader",
      "pos": [
        40,
        140
      ],
      "size": [
        315,
        82
      ],
      "flags": {},
      "order": 0,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            79
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "mochi_preview_bf16.safetensors",
            "url": "https://huggingface.co/Comfy-Org/mochi_preview_repackaged/resolve/main/split_files/diffusion_models/mochi_preview_bf16.safetensors?download=true",
            "directory": "diffusion_models"
          }
        ]
      },
      "widgets_values": [
        "mochi_preview_bf16.safetensors",
        "default"
      ]
    },
    {
      "id": 38,
      "type": "CLIPLoader",
      "pos": [
        40,
        270
      ],
      "size": [
        315,
        106
      ],
      "flags": {},
      "order": 1,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "CLIP",
          "type": "CLIP",
          "slot_index": 0,
          "links": [
            74,
            75
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPLoader",
        "models": [
          {
            "name": "t5xxl_fp16.safetensors",
            "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors?download=true",
            "directory": "text_encoders"
          }
        ]
      },
      "widgets_values": [
        "t5xxl_fp16.safetensors",
        "mochi",
        "default"
      ]
    },
    {
      "id": 39,
      "type": "VAELoader",
      "pos": [
        40,
        420
      ],
      "size": [
        310,
        60
      ],
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "VAE",
          "type": "VAE",
          "links": [
            76
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "VAELoader",
        "models": [
          {
            "name": "mochi_vae.safetensors",
            "url": "https://huggingface.co/Comfy-Org/mochi_preview_repackaged/resolve/main/split_files/vae/mochi_vae.safetensors?download=true",
            "directory": "vae"
          }
        ]
      },
      "widgets_values": [
        "mochi_vae.safetensors"
      ]
    },
    {
      "id": 21,
      "type": "EmptyMochiLatentVideo",
      "pos": [
        40,
        580
      ],
      "size": [
        315,
        130
      ],
      "flags": {},
      "order": 3,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            38
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "EmptyMochiLatentVideo"
      },
      "widgets_values": [
        848,
        480,
        37,
        1
      ]
    },
    {
      "id": 8,
      "type": "VAEDecode",
      "pos": [
        850,
        410
      ],
      "size": [
        210,
        46
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 35
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 76
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "slot_index": 0,
          "links": [
            80
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "VAEDecode"
      },
      "widgets_values": []
    },
    {
      "id": 41,
      "type": "CreateVideo",
      "pos": [
        850,
        500
      ],
      "size": [
        270,
        78
      ],
      "flags": {},
      "order": 8,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 80
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
            81
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CreateVideo"
      },
      "widgets_values": [
        24
      ]
    },
    {
      "id": 3,
      "type": "KSampler",
      "pos": [
        850,
        100
      ],
      "size": [
        315,
        262
      ],
      "flags": {},
      "order": 6,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 79
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 46
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 52
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 38
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            35
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "KSampler"
      },
      "widgets_values": [
        704883238463297,
        "randomize",
        30,
        4.5,
        "euler",
        "simple",
        1
      ]
    },
    {
      "id": 42,
      "type": "SaveVideo",
      "pos": [
        1190,
        100
      ],
      "size": [
        670,
        600
      ],
      "flags": {},
      "order": 9,
      "mode": 0,
      "inputs": [
        {
          "name": "video",
          "type": "VIDEO",
          "link": 81
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "SaveVideo"
      },
      "widgets_values": [
        "video/ComfyUI",
        "auto",
        "auto"
      ]
    }
  ],
  "links": [
    [
      35,
      3,
      0,
      8,
      0,
      "LATENT"
    ],
    [
      38,
      21,
      0,
      3,
      3,
      "LATENT"
    ],
    [
      46,
      6,
      0,
      3,
      1,
      "CONDITIONING"
    ],
    [
      52,
      7,
      0,
      3,
      2,
      "CONDITIONING"
    ],
    [
      74,
      38,
      0,
      6,
      0,
      "CLIP"
    ],
    [
      75,
      38,
      0,
      7,
      0,
      "CLIP"
    ],
    [
      76,
      39,
      0,
      8,
      1,
      "VAE"
    ],
    [
      79,
      37,
      0,
      3,
      0,
      "MODEL"
    ],
    [
      80,
      8,
      0,
      41,
      0,
      "IMAGE"
    ],
    [
      81,
      41,
      0,
      42,
      0,
      "VIDEO"
    ]
  ],
  "groups": [
    {
      "id": 1,
      "title": "Step3 - Prompt",
      "bounding": [
        380,
        70,
        445.280029296875,
        467.2099914550781
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 2,
      "title": "Step2 - Video size",
      "bounding": [
        30,
        510,
        335,
        213.60000610351562
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 3,
      "title": "Step1 - Load models",
      "bounding": [
        30,
        70,
        335,
        423.6000061035156
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    }
  ],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.6830134553650705,
      "offset": [
        95.57952081646913,
        29.634755086943596
      ]
    },
    "frontendVersion": "1.28.7"
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/mochi-1-preview.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "3": {
    "inputs": {
      "seed": "__RANDOM_INT__",
      "steps": 30,
      "cfg": 4.5,
      "sampler_name": "euler",
      "scheduler": "simple",
      "denoise": 1,
      "model": [
        "37",
        0
      ],
      "positive": [
        "6",
        0
      ],
      "negative": [
        "7",
        0
      ],
      "latent_image": [
        "21",
        0
      ]
    },
    "class_type": "KSampler",
    "_meta": {
      "title": "KSampler"
    }
  },
  "6": {
    "inputs": {
      "text": "a fox moving quickly in a beautiful winter scenery nature trees sunset tracking camera",
      "clip": [
        "38",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "7": {
    "inputs": {
      "text": "",
      "clip": [
        "38",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "8": {
    "inputs": {
      "samples": [
        "3",
        0
      ],
      "vae": [
        "39",
        0
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "21": {
    "inputs": {
      "width": 848,
      "height": 480,
      "length": 37,
      "batch_size": 1
    },
    "class_type": "EmptyMochiLatentVideo",
    "_meta": {
      "title": "EmptyMochiLatentVideo"
    }
  },
  "37": {
    "inputs": {
      "unet_name": "mochi_preview_bf16.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "38": {
    "inputs": {
      "clip_name": "t5xxl_fp16.safetensors",
      "type": "mochi",
      "device": "default"
    },
    "class_type": "CLIPLoader",
    "_meta": {
      "title": "Load CLIP"
    }
  },
  "39": {
    "inputs": {
      "vae_name": "mochi_vae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "41": {
    "inputs": {
      "fps": 24,
      "images": [
        "8",
        0
      ]
    },
    "class_type": "CreateVideo",
    "_meta": {
      "title": "Create Video"
    }
  },
  "42": {
    "inputs": {
      "filename_prefix": "video/ComfyUI",
      "format": "auto",
      "codec": "auto",
      "video": [
        "41",
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
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/mochi-1-preview.json
}

main