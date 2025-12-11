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
  "https://huggingface.co/RunDiffusion/Juggernaut-XI-v11/resolve/main/Juggernaut-XI-byRunDiffusion.safetensors
  |$MODELS_DIR/checkpoints/Juggernaut-XI-byRunDiffusion.safetensors"
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
  "id": "7dc75290-2aa6-4dba-9409-a6ec77dde26a",
  "revision": 0,
  "last_node_id": 10,
  "last_link_id": 15,
  "nodes": [
    {
      "id": 4,
      "type": "CLIPTextEncode",
      "pos": [
        624.0100000000001,
        277.1100000000004
      ],
      "size": [
        400,
        200
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 3
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "links": [
            6
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "fake eyes, deformed eyes, bad eyes, cgi, 3D, digital, airbrushed, shield"
      ],
      "color": "#322",
      "bgcolor": "#533"
    },
    {
      "id": 6,
      "type": "EmptyLatentImage",
      "pos": [
        730.3,
        524.78
      ],
      "size": [
        315,
        106
      ],
      "flags": {},
      "order": 0,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            7
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "EmptyLatentImage"
      },
      "widgets_values": [
        1024,
        1024,
        1
      ]
    },
    {
      "id": 5,
      "type": "KSampler",
      "pos": [
        1115.5499999999981,
        33.99
      ],
      "size": [
        315,
        474
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 12
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 5
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 6
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 7
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            13
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "KSampler"
      },
      "widgets_values": [
        789424763092339,
        "randomize",
        30,
        6.5,
        "dpmpp_2m",
        "karras",
        1
      ]
    },
    {
      "id": 2,
      "type": "CLIPSetLastLayer",
      "pos": [
        249.22000000000008,
        283.4900000000001
      ],
      "size": [
        315,
        58
      ],
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 1
        }
      ],
      "outputs": [
        {
          "name": "CLIP",
          "type": "CLIP",
          "links": [
            2,
            3
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPSetLastLayer"
      },
      "widgets_values": [
        -2
      ]
    },
    {
      "id": 10,
      "type": "VAEDecode",
      "pos": [
        1503.7746969532052,
        48.693201030074036
      ],
      "size": [
        140,
        46
      ],
      "flags": {},
      "order": 6,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 13
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 15
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "links": [
            14
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
      "id": 9,
      "type": "SaveImage",
      "pos": [
        1463.7800000000025,
        173.3400000000002
      ],
      "size": [
        315,
        270
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 14
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66"
      },
      "widgets_values": [
        "ComfyUI"
      ]
    },
    {
      "id": 1,
      "type": "CheckpointLoaderSimple",
      "pos": [
        255.85999999999999,
        39
      ],
      "size": [
        315,
        98
      ],
      "flags": {},
      "order": 1,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            12
          ]
        },
        {
          "name": "CLIP",
          "type": "CLIP",
          "links": [
            1
          ]
        },
        {
          "name": "VAE",
          "type": "VAE",
          "links": [
            15
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CheckpointLoaderSimple"
      },
      "widgets_values": [
        "Juggernaut-XI-byRunDiffusion.safetensors"
      ]
    },
    {
      "id": 3,
      "type": "CLIPTextEncode",
      "pos": [
        631.9000000000001,
        10.589999999999998
      ],
      "size": [
        400,
        200
      ],
      "flags": {},
      "order": 3,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 2
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "links": [
            5
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "A Warrior cat with a glowing axe charging into battle wearing armor, fierce expression, dramatic movement, in the style of fantasy realism, high resolution image"
      ],
      "color": "#232",
      "bgcolor": "#353"
    }
  ],
  "links": [
    [
      1,
      1,
      1,
      2,
      0,
      "CLIP"
    ],
    [
      2,
      2,
      0,
      3,
      0,
      "CLIP"
    ],
    [
      3,
      2,
      0,
      4,
      0,
      "CLIP"
    ],
    [
      5,
      3,
      0,
      5,
      1,
      "CONDITIONING"
    ],
    [
      6,
      4,
      0,
      5,
      2,
      "CONDITIONING"
    ],
    [
      7,
      6,
      0,
      5,
      3,
      "LATENT"
    ],
    [
      12,
      1,
      0,
      5,
      0,
      "MODEL"
    ],
    [
      13,
      5,
      0,
      10,
      0,
      "LATENT"
    ],
    [
      14,
      10,
      0,
      9,
      0,
      "IMAGE"
    ],
    [
      15,
      1,
      2,
      10,
      1,
      "VAE"
    ]
  ],
  "groups": [],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.8264462809917354,
      "offset": [
        -180.03469695320308,
        113.44679896992605
      ]
    },
    "frontendVersion": "1.28.7"
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/juggernaut-xi.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "1": {
    "inputs": {
      "ckpt_name": "Juggernaut-XI-byRunDiffusion.safetensors"
    },
    "class_type": "CheckpointLoaderSimple",
    "_meta": {
      "title": "Load Checkpoint"
    }
  },
  "2": {
    "inputs": {
      "stop_at_clip_layer": -2,
      "clip": [
        "1",
        1
      ]
    },
    "class_type": "CLIPSetLastLayer",
    "_meta": {
      "title": "CLIP Set Last Layer"
    }
  },
  "3": {
    "inputs": {
      "text": "A Warrior cat with a glowing axe charging into battle wearing armor, fierce expression, dramatic movement, in the style of fantasy realism, high resolution image",
      "clip": [
        "2",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "4": {
    "inputs": {
      "text": "fake eyes, deformed eyes, bad eyes, cgi, 3D, digital, airbrushed, shield",
      "clip": [
        "2",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "5": {
    "inputs": {
      "seed": 789424763092339,
      "steps": 30,
      "cfg": 6.5,
      "sampler_name": "dpmpp_2m",
      "scheduler": "karras",
      "denoise": 1,
      "model": [
        "1",
        0
      ],
      "positive": [
        "3",
        0
      ],
      "negative": [
        "4",
        0
      ],
      "latent_image": [
        "6",
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
      "width": 1024,
      "height": 1024,
      "batch_size": 1
    },
    "class_type": "EmptyLatentImage",
    "_meta": {
      "title": "Empty Latent Image"
    }
  },
  "9": {
    "inputs": {
      "filename_prefix": "ComfyUI",
      "images": [
        "10",
        0
      ]
    },
    "class_type": "SaveImage",
    "_meta": {
      "title": "Save Image"
    }
  },
  "10": {
    "inputs": {
      "samples": [
        "5",
        0
      ],
      "vae": [
        "1",
        2
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  }
}
WORKFLOW_API_JSON
    payload_json=$(jq -n --argjson workflow "$workflow_json" '{input: {workflow_json: $workflow}}')
    rm /opt/comfyui-api-wrapper/payloads/*.json
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/juggernaut-xi.json
}

main