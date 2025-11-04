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
  "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/diffusion_models/hidream_i1_full_fp16.safetensors
  |$MODELS_DIR/diffusion_models/hidream_i1_full_fp16.safetensors"
  "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/clip_l_hidream.safetensors
  |$MODELS_DIR/text_encoders/clip_l_hidream.safetensors"
  "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/clip_g_hidream.safetensors
  |$MODELS_DIR/text_encoders/clip_g_hidream.safetensors"
  "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors
  |$MODELS_DIR/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/llama_3.1_8b_instruct_fp8_scaled.safetensors
  |$MODELS_DIR/text_encoders/llama_3.1_8b_instruct_fp8_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/vae/ae.safetensors
  |$MODELS_DIR/vae/ae.safetensors"
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
  "id": "01d66ae9-78be-4a8d-b737-24eee5e1d447",
  "revision": 0,
  "last_node_id": 94,
  "last_link_id": 183,
  "nodes": [
    {
      "id": 89,
      "type": "VAEDecode",
      "pos": [
        560,
        -1020
      ],
      "size": [
        210,
        46
      ],
      "flags": {},
      "order": 8,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 181
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 174
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "slot_index": 0,
          "links": [
            175
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "VAEDecode",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": []
    },
    {
      "id": 91,
      "type": "CLIPTextEncode",
      "pos": [
        -150,
        -990
      ],
      "size": [
        350,
        170
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 176
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            178
          ]
        }
      ],
      "title": "Positive Prompt",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "CLIPTextEncode",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        "A lo-fi, grungy wide shot of a ragged large red tree leaning slightly to one side Polaroid aesthetic. the tree is alone in a desolate landscape, the tree is illuminated by a red light, the background is pitch black"
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 86,
      "type": "EmptySD3LatentImage",
      "pos": [
        -520,
        -440
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
          "slot_index": 0,
          "links": [
            180
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "EmptySD3LatentImage",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        1024,
        1024,
        1
      ]
    },
    {
      "id": 85,
      "type": "CLIPTextEncode",
      "pos": [
        -140,
        -770
      ],
      "size": [
        330,
        180
      ],
      "flags": {
        "collapsed": false
      },
      "order": 4,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 168
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            179
          ]
        }
      ],
      "title": "Negative Prompt",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "CLIPTextEncode",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        "bad ugly jpeg artifacts"
      ],
      "color": "#322",
      "bgcolor": "#533"
    },
    {
      "id": 93,
      "type": "KSampler",
      "pos": [
        230,
        -890
      ],
      "size": [
        310,
        460
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 177
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 178
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 179
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 180
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            181
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "KSampler",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        548242654058770,
        "randomize",
        50,
        5,
        "uni_pc",
        "simple",
        1
      ]
    },
    {
      "id": 84,
      "type": "ModelSamplingSD3",
      "pos": [
        230,
        -1020
      ],
      "size": [
        210,
        60
      ],
      "flags": {},
      "order": 6,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 182
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            177
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "ModelSamplingSD3",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        3
      ]
    },
    {
      "id": 82,
      "type": "QuadrupleCLIPLoader",
      "pos": [
        -520,
        -820
      ],
      "size": [
        320,
        130
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
            168,
            176
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "QuadrupleCLIPLoader",
        "models": [
          {
            "name": "clip_l_hidream.safetensors",
            "url": "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/clip_l_hidream.safetensors",
            "directory": "text_encoders"
          },
          {
            "name": "clip_g_hidream.safetensors",
            "url": "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/clip_g_hidream.safetensors",
            "directory": "text_encoders"
          },
          {
            "name": "t5xxl_fp8_e4m3fn_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors",
            "directory": "text_encoders"
          },
          {
            "name": "llama_3.1_8b_instruct_fp8_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/text_encoders/llama_3.1_8b_instruct_fp8_scaled.safetensors",
            "directory": "text_encoders"
          }
        ],
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        "clip_l_hidream.safetensors",
        "clip_g_hidream.safetensors",
        "t5xxl_fp8_e4m3fn_scaled.safetensors",
        "llama_3.1_8b_instruct_fp8_scaled.safetensors"
      ]
    },
    {
      "id": 81,
      "type": "VAELoader",
      "pos": [
        -520,
        -630
      ],
      "size": [
        320,
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
            174
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "VAELoader",
        "models": [
          {
            "name": "ae.safetensors",
            "url": "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/vae/ae.safetensors?download=true",
            "directory": "vae"
          }
        ],
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        "ae.safetensors"
      ]
    },
    {
      "id": 90,
      "type": "SaveImage",
      "pos": [
        560,
        -890
      ],
      "size": [
        570,
        420
      ],
      "flags": {},
      "order": 9,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 175
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.28",
        "Node name for S&R": "SaveImage",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65
      },
      "widgets_values": [
        "ComfyUI"
      ]
    },
    {
      "id": 94,
      "type": "UNETLoader",
      "pos": [
        -520,
        -970
      ],
      "size": [
        310,
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
          "links": [
            182
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.56",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "hidream_i1_full_fp16.safetensors",
            "url": "https://huggingface.co/Comfy-Org/HiDream-I1_ComfyUI/resolve/main/split_files/diffusion_models/hidream_i1_full_fp16.safetensors?download=true",
            "directory": "diffusion_models"
          }
        ]
      },
      "widgets_values": [
        "hidream_i1_full_fp16.safetensors",
        "default"
      ]
    }
  ],
  "links": [
    [
      168,
      82,
      0,
      85,
      0,
      "CLIP"
    ],
    [
      174,
      81,
      0,
      89,
      1,
      "VAE"
    ],
    [
      175,
      89,
      0,
      90,
      0,
      "IMAGE"
    ],
    [
      176,
      82,
      0,
      91,
      0,
      "CLIP"
    ],
    [
      177,
      84,
      0,
      93,
      0,
      "MODEL"
    ],
    [
      178,
      91,
      0,
      93,
      1,
      "CONDITIONING"
    ],
    [
      179,
      85,
      0,
      93,
      2,
      "CONDITIONING"
    ],
    [
      180,
      86,
      0,
      93,
      3,
      "LATENT"
    ],
    [
      181,
      93,
      0,
      89,
      0,
      "LATENT"
    ],
    [
      182,
      94,
      0,
      84,
      0,
      "MODEL"
    ]
  ],
  "groups": [
    {
      "id": 3,
      "title": "Step 1 - Load Models",
      "bounding": [
        -560,
        -1060,
        380,
        520
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 4,
      "title": "Step 2 - Image size",
      "bounding": [
        -560,
        -520,
        380,
        200
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 5,
      "title": "Step3 - Prompt",
      "bounding": [
        -160,
        -1060,
        370,
        520
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    }
  ],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.7939909989340479,
      "offset": [
        790.6979123665944,
        1185.449742479015
      ]
    },
    "frontendVersion": "1.28.7",
    "node_versions": {
      "comfy-core": "0.3.28"
    },
    "VHS_latentpreview": false,
    "VHS_latentpreviewrate": 0,
    "VHS_MetadataImage": true,
    "VHS_KeepIntermediate": true
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/hidream-i1-full.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "81": {
    "inputs": {
      "vae_name": "ae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "82": {
    "inputs": {
      "clip_name1": "clip_l_hidream.safetensors",
      "clip_name2": "clip_g_hidream.safetensors",
      "clip_name3": "t5xxl_fp8_e4m3fn_scaled.safetensors",
      "clip_name4": "llama_3.1_8b_instruct_fp8_scaled.safetensors"
    },
    "class_type": "QuadrupleCLIPLoader",
    "_meta": {
      "title": "QuadrupleCLIPLoader"
    }
  },
  "84": {
    "inputs": {
      "shift": 3,
      "model": [
        "94",
        0
      ]
    },
    "class_type": "ModelSamplingSD3",
    "_meta": {
      "title": "ModelSamplingSD3"
    }
  },
  "85": {
    "inputs": {
      "text": "bad ugly jpeg artifacts",
      "clip": [
        "82",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "Negative Prompt"
    }
  },
  "86": {
    "inputs": {
      "width": 1024,
      "height": 1024,
      "batch_size": 1
    },
    "class_type": "EmptySD3LatentImage",
    "_meta": {
      "title": "EmptySD3LatentImage"
    }
  },
  "89": {
    "inputs": {
      "samples": [
        "93",
        0
      ],
      "vae": [
        "81",
        0
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "90": {
    "inputs": {
      "filename_prefix": "ComfyUI",
      "images": [
        "89",
        0
      ]
    },
    "class_type": "SaveImage",
    "_meta": {
      "title": "Save Image"
    }
  },
  "91": {
    "inputs": {
      "text": "A lo-fi, grungy wide shot of a ragged large red tree leaning slightly to one side Polaroid aesthetic. the tree is alone in a desolate landscape, the tree is illuminated by a red light, the background is pitch black",
      "clip": [
        "82",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "Positive Prompt"
    }
  },
  "93": {
    "inputs": {
      "seed": "__RANDOM_INT__",
      "steps": 50,
      "cfg": 5,
      "sampler_name": "uni_pc",
      "scheduler": "simple",
      "denoise": 1,
      "model": [
        "84",
        0
      ],
      "positive": [
        "91",
        0
      ],
      "negative": [
        "85",
        0
      ],
      "latent_image": [
        "86",
        0
      ]
    },
    "class_type": "KSampler",
    "_meta": {
      "title": "KSampler"
    }
  },
  "94": {
    "inputs": {
      "unet_name": "hidream_i1_full_fp16.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  }
}
WORKFLOW_API_JSON
    payload_json=$(jq -n --argjson workflow "$workflow_json" '{input: {workflow_json: $workflow}}')
    rm /opt/comfyui-api-wrapper/payloads/*.json
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/hidream-i1-full.json
}

main