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
  "https://huggingface.co/lightx2v/Qwen-Image-Lightning/resolve/main/Qwen-Image-Lightning-8steps-V1.0.safetensors
  |$MODELS_DIR/loras/Qwen-Image-Lightning-8steps-V1.0.safetensors"
  "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors
  |$MODELS_DIR/vae/qwen_image_vae.safetensors"
  "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
  |$MODELS_DIR/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors
  |$MODELS_DIR/diffusion_models/qwen_image_fp8_e4m3fn.safetensors"
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
  "id": "91f6bbe2-ed41-4fd6-bac7-71d5b5864ecb",
  "revision": 0,
  "last_node_id": 74,
  "last_link_id": 130,
  "nodes": [
    {
      "id": 39,
      "type": "VAELoader",
      "pos": [
        20,
        340
      ],
      "size": [
        330,
        60
      ],
      "flags": {},
      "order": 0,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "VAE",
          "type": "VAE",
          "slot_index": 0,
          "links": [
            76
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "VAELoader",
        "models": [
          {
            "name": "qwen_image_vae.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors",
            "directory": "vae"
          }
        ],
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        "qwen_image_vae.safetensors"
      ]
    },
    {
      "id": 58,
      "type": "EmptySD3LatentImage",
      "pos": [
        50,
        510
      ],
      "size": [
        270,
        106
      ],
      "flags": {},
      "order": 1,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            107
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "EmptySD3LatentImage",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        1328,
        1328,
        1
      ]
    },
    {
      "id": 6,
      "type": "CLIPTextEncode",
      "pos": [
        390,
        240
      ],
      "size": [
        422.84503173828125,
        164.31304931640625
      ],
      "flags": {},
      "order": 5,
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
      "title": "CLIP Text Encode (Positive Prompt)",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "CLIPTextEncode",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        "\"A vibrant, warm neon-lit street scene in Hong Kong at the afternoon, with a mix of colorful Chinese and English signs glowing brightly. The atmosphere is lively, cinematic, and rain-washed with reflections on the pavement. The colors are vivid, full of pink, blue, red, and green hues. Crowded buildings with overlapping neon signs. 1980s Hong Kong style. Signs include:\n\"龍鳳冰室\" \"金華燒臘\" \"HAPPY HAIR\" \"鴻運茶餐廳\" \"EASY BAR\" \"永發魚蛋粉\" \"添記粥麵\" \"SUNSHINE MOTEL\" \"美都餐室\" \"富記糖水\" \"太平館\" \"雅芳髮型屋\" \"STAR KTV\" \"銀河娛樂城\" \"百樂門舞廳\" \"BUBBLE CAFE\" \"萬豪麻雀館\" \"CITY LIGHTS BAR\" \"瑞祥香燭莊\" \"文記文具\" \"GOLDEN JADE HOTEL\" \"LOVELY BEAUTY\" \"合興百貨\" \"興旺電器\" And the background is warm yellow street and with all stores' lights on."
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 7,
      "type": "CLIPTextEncode",
      "pos": [
        390,
        460
      ],
      "size": [
        425.2780151367187,
        88
      ],
      "flags": {},
      "order": 6,
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
      "title": "CLIP Text Encode (Negative Prompt)",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "CLIPTextEncode",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        ""
      ],
      "color": "#322",
      "bgcolor": "#533"
    },
    {
      "id": 3,
      "type": "KSampler",
      "pos": [
        863,
        186
      ],
      "size": [
        315,
        474
      ],
      "flags": {},
      "order": 8,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 125
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
          "link": 107
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            128
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "KSampler",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        1076319151795607,
        "randomize",
        20,
        2.5,
        "euler",
        "simple",
        1
      ]
    },
    {
      "id": 8,
      "type": "VAEDecode",
      "pos": [
        1209,
        188
      ],
      "size": [
        210,
        46
      ],
      "flags": {},
      "order": 9,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 128
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
          "links": [
            110
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "VAEDecode",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": []
    },
    {
      "id": 66,
      "type": "ModelSamplingAuraFlow",
      "pos": [
        851.7932283818722,
        10.459471584379672
      ],
      "size": [
        315,
        82
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 130
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            125
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.49",
        "Node name for S&R": "ModelSamplingAuraFlow"
      },
      "widgets_values": [
        3.1000000000000005
      ]
    },
    {
      "id": 37,
      "type": "UNETLoader",
      "pos": [
        -45.70996044951348,
        -276.11811644486335
      ],
      "size": [
        390,
        110
      ],
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            129
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "qwen_image_fp8_e4m3fn.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors",
            "directory": "diffusion_models"
          }
        ],
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        "qwen_image_fp8_e4m3fn.safetensors",
        "default"
      ]
    },
    {
      "id": 73,
      "type": "LoraLoaderModelOnly",
      "pos": [
        407.9780033210677,
        -157.54054603616763
      ],
      "size": [
        420,
        82
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 129
        }
      ],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "links": [
            130
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.49",
        "Node name for S&R": "LoraLoaderModelOnly",
        "models": [
          {
            "name": "Qwen-Image-Lightning-8steps-V1.0.safetensors",
            "url": "https://huggingface.co/lightx2v/Qwen-Image-Lightning/resolve/main/Qwen-Image-Lightning-8steps-V1.0.safetensors",
            "directory": "loras"
          }
        ]
      },
      "widgets_values": [
        "Qwen-Image-Lightning-8steps-V1.0.safetensors",
        1
      ]
    },
    {
      "id": 38,
      "type": "CLIPLoader",
      "pos": [
        21.709749572093237,
        176.32200342325422
      ],
      "size": [
        330,
        110
      ],
      "flags": {},
      "order": 3,
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
        "ver": "0.3.48",
        "Node name for S&R": "CLIPLoader",
        "models": [
          {
            "name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
            "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
            "directory": "text_encoders"
          }
        ],
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "qwen_image",
        "default"
      ]
    },
    {
      "id": 60,
      "type": "SaveImage",
      "pos": [
        1466.3877461488391,
        -129.01342040934063
      ],
      "size": [
        550,
        630
      ],
      "flags": {},
      "order": 10,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 110
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.48",
        "Node name for S&R": "SaveImage",
        "enableTabs": false,
        "tabWidth": 65,
        "tabXOffset": 10,
        "hasSecondTab": false,
        "secondTabText": "Send Back",
        "secondTabOffset": 80,
        "secondTabWidth": 65,
        "widget_ue_connectable": {}
      },
      "widgets_values": [
        "ComfyUI"
      ]
    }
  ],
  "links": [
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
      107,
      58,
      0,
      3,
      3,
      "LATENT"
    ],
    [
      110,
      8,
      0,
      60,
      0,
      "IMAGE"
    ],
    [
      125,
      66,
      0,
      3,
      0,
      "MODEL"
    ],
    [
      128,
      3,
      0,
      8,
      0,
      "LATENT"
    ],
    [
      129,
      37,
      0,
      73,
      0,
      "MODEL"
    ],
    [
      130,
      73,
      0,
      66,
      0,
      "MODEL"
    ]
  ],
  "groups": [
    {
      "id": 1,
      "title": "Step1 - Load models",
      "bounding": [
        10,
        -20,
        350,
        433.6000061035156
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 2,
      "title": "Step2 - Image size",
      "bounding": [
        10,
        430,
        350,
        210
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 3,
      "title": "Step3 - Prompt",
      "bounding": [
        380,
        160,
        450,
        470
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 4,
      "title": "Lightx2v 8steps LoRA",
      "bounding": [
        394.8308309306629,
        -233.93765562972465,
        450,
        170
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    }
  ],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.5848809769115544,
      "offset": [
        725.7896500318514,
        442.9777934942775
      ]
    },
    "frontendVersion": "1.28.7",
    "ue_links": [],
    "links_added_by_ue": [],
    "VHS_latentpreview": false,
    "VHS_latentpreviewrate": 0,
    "VHS_MetadataImage": true,
    "VHS_KeepIntermediate": true
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/qwen_image_fp8.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "3": {
    "inputs": {
      "seed": "__RANDOM_INT__",
      "steps": 20,
      "cfg": 2.5,
      "sampler_name": "euler",
      "scheduler": "simple",
      "denoise": 1,
      "model": [
        "66",
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
        "58",
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
      "text": "\"A vibrant, warm neon-lit street scene in Hong Kong at the afternoon, with a mix of colorful Chinese and English signs glowing brightly. The atmosphere is lively, cinematic, and rain-washed with reflections on the pavement. The colors are vivid, full of pink, blue, red, and green hues. Crowded buildings with overlapping neon signs. 1980s Hong Kong style. Signs include:\n\"龍鳳冰室\" \"金華燒臘\" \"HAPPY HAIR\" \"鴻運茶餐廳\" \"EASY BAR\" \"永發魚蛋粉\" \"添記粥麵\" \"SUNSHINE MOTEL\" \"美都餐室\" \"富記糖水\" \"太平館\" \"雅芳髮型屋\" \"STAR KTV\" \"銀河娛樂城\" \"百樂門舞廳\" \"BUBBLE CAFE\" \"萬豪麻雀館\" \"CITY LIGHTS BAR\" \"瑞祥香燭莊\" \"文記文具\" \"GOLDEN JADE HOTEL\" \"LOVELY BEAUTY\" \"合興百貨\" \"興旺電器\" And the background is warm yellow street and with all stores' lights on.",
      "clip": [
        "38",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Positive Prompt)"
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
      "title": "CLIP Text Encode (Negative Prompt)"
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
  "37": {
    "inputs": {
      "unet_name": "qwen_image_fp8_e4m3fn.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "38": {
    "inputs": {
      "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
      "type": "qwen_image",
      "device": "default"
    },
    "class_type": "CLIPLoader",
    "_meta": {
      "title": "Load CLIP"
    }
  },
  "39": {
    "inputs": {
      "vae_name": "qwen_image_vae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "58": {
    "inputs": {
      "width": 1328,
      "height": 1328,
      "batch_size": 1
    },
    "class_type": "EmptySD3LatentImage",
    "_meta": {
      "title": "EmptySD3LatentImage"
    }
  },
  "60": {
    "inputs": {
      "filename_prefix": "ComfyUI",
      "images": [
        "8",
        0
      ]
    },
    "class_type": "SaveImage",
    "_meta": {
      "title": "Save Image"
    }
  },
  "66": {
    "inputs": {
      "shift": 3.1000000000000005,
      "model": [
        "37",
        0
      ]
    },
    "class_type": "ModelSamplingAuraFlow",
    "_meta": {
      "title": "ModelSamplingAuraFlow"
    }
  }
}
WORKFLOW_API_JSON
    payload_json=$(jq -n --argjson workflow "$workflow_json" '{input: {workflow_json: $workflow}}')
    rm /opt/comfyui-api-wrapper/payloads/*.json
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/Qwen-image.json
}

main