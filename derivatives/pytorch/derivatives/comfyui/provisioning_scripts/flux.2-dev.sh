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
  "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors
  |$MODELS_DIR/vae/flux2-vae.safetensors"
  "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/diffusion_models/flux2_dev_fp8mixed.safetensors
  |$MODELS_DIR/diffusion_models/flux2_dev_fp8mixed.safetensors"
  "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/text_encoders/mistral_3_small_flux2_bf16.safetensors
  |$MODELS_DIR/text_encoders/mistral_3_small_flux2_bf16.safetensors"
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
  "id": "7c048efb-a059-44e2-970a-43e1eb472d0d",
  "revision": 0,
  "last_node_id": 51,
  "last_link_id": 138,
  "nodes": [
    {
      "id": 13,
      "type": "SamplerCustomAdvanced",
      "pos": [
        864,
        192
      ],
      "size": [
        272.3617858886719,
        124.53733825683594
      ],
      "flags": {},
      "order": 21,
      "mode": 0,
      "inputs": [
        {
          "name": "noise",
          "type": "NOISE",
          "link": 37
        },
        {
          "name": "guider",
          "type": "GUIDER",
          "link": 30
        },
        {
          "name": "sampler",
          "type": "SAMPLER",
          "link": 19
        },
        {
          "name": "sigmas",
          "type": "SIGMAS",
          "link": 132
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 131
        }
      ],
      "outputs": [
        {
          "name": "output",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            24
          ]
        },
        {
          "name": "denoised_output",
          "type": "LATENT",
          "links": null
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "SamplerCustomAdvanced"
      },
      "widgets_values": []
    },
    {
      "id": 8,
      "type": "VAEDecode",
      "pos": [
        866,
        367
      ],
      "size": [
        210,
        46
      ],
      "flags": {},
      "order": 22,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 24
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 12
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "slot_index": 0,
          "links": [
            9
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "VAEDecode"
      },
      "widgets_values": []
    },
    {
      "id": 16,
      "type": "KSamplerSelect",
      "pos": [
        480,
        740
      ],
      "size": [
        315,
        58
      ],
      "flags": {},
      "order": 0,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "SAMPLER",
          "type": "SAMPLER",
          "links": [
            19
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "KSamplerSelect"
      },
      "widgets_values": [
        "euler"
      ]
    },
    {
      "id": 22,
      "type": "BasicGuider",
      "pos": [
        576,
        48
      ],
      "size": [
        222.3482666015625,
        46
      ],
      "flags": {},
      "order": 20,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 133
        },
        {
          "name": "conditioning",
          "type": "CONDITIONING",
          "link": 130
        }
      ],
      "outputs": [
        {
          "name": "GUIDER",
          "type": "GUIDER",
          "slot_index": 0,
          "links": [
            30
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "BasicGuider"
      },
      "widgets_values": []
    },
    {
      "id": 40,
      "type": "VAEEncode",
      "pos": [
        165.49060363340644,
        43.926673731494525
      ],
      "size": [
        140,
        46
      ],
      "flags": {},
      "order": 17,
      "mode": 0,
      "inputs": [
        {
          "name": "pixels",
          "type": "IMAGE",
          "link": 122
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 120
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            121
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "VAEEncode"
      },
      "widgets_values": []
    },
    {
      "id": 41,
      "type": "ImageScaleToTotalPixels",
      "pos": [
        -156.19988089586272,
        -3.7311758283972223
      ],
      "size": [
        270,
        82
      ],
      "flags": {},
      "order": 14,
      "mode": 0,
      "inputs": [
        {
          "name": "image",
          "type": "IMAGE",
          "link": 123
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "links": [
            122
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "ImageScaleToTotalPixels"
      },
      "widgets_values": [
        "area",
        1
      ]
    },
    {
      "id": 45,
      "type": "ImageScaleToTotalPixels",
      "pos": [
        -160,
        -380
      ],
      "size": [
        270,
        82
      ],
      "flags": {},
      "order": 10,
      "mode": 0,
      "inputs": [
        {
          "name": "image",
          "type": "IMAGE",
          "link": 128
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "links": [
            126
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "ImageScaleToTotalPixels"
      },
      "widgets_values": [
        "area",
        1
      ]
    },
    {
      "id": 44,
      "type": "VAEEncode",
      "pos": [
        171.32724960075282,
        -55.05675969081743
      ],
      "size": [
        140,
        46
      ],
      "flags": {},
      "order": 15,
      "mode": 0,
      "inputs": [
        {
          "name": "pixels",
          "type": "IMAGE",
          "link": 126
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 127
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            125
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "VAEEncode"
      },
      "widgets_values": []
    },
    {
      "id": 46,
      "type": "LoadImage",
      "pos": [
        -470,
        -380
      ],
      "size": [
        274.080078125,
        314
      ],
      "flags": {},
      "order": 1,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "links": [
            128
          ]
        },
        {
          "name": "MASK",
          "type": "MASK",
          "links": null
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "LoadImage"
      },
      "widgets_values": [
        "sunset.png",
        "image"
      ]
    },
    {
      "id": 26,
      "type": "FluxGuidance",
      "pos": [
        480,
        144
      ],
      "size": [
        317.4000244140625,
        58
      ],
      "flags": {},
      "order": 16,
      "mode": 0,
      "inputs": [
        {
          "name": "conditioning",
          "type": "CONDITIONING",
          "link": 41
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            118
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "FluxGuidance"
      },
      "widgets_values": [
        4
      ],
      "color": "#233",
      "bgcolor": "#355"
    },
    {
      "id": 48,
      "type": "Flux2Scheduler",
      "pos": [
        524.4243218480328,
        840.7535840503427
      ],
      "size": [
        270,
        106
      ],
      "flags": {},
      "order": 12,
      "mode": 0,
      "inputs": [
        {
          "name": "width",
          "type": "INT",
          "widget": {
            "name": "width"
          },
          "link": 136
        },
        {
          "name": "height",
          "type": "INT",
          "widget": {
            "name": "height"
          },
          "link": 138
        }
      ],
      "outputs": [
        {
          "name": "SIGMAS",
          "type": "SIGMAS",
          "links": [
            132
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "Flux2Scheduler"
      },
      "widgets_values": [
        20,
        1024,
        1024
      ]
    },
    {
      "id": 51,
      "type": "PrimitiveNode",
      "pos": [
        110,
        740
      ],
      "size": [
        210,
        82
      ],
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "INT",
          "type": "INT",
          "widget": {
            "name": "height"
          },
          "links": [
            137,
            138
          ]
        }
      ],
      "title": "height",
      "properties": {
        "Run widget replace on values": false
      },
      "widgets_values": [
        1024,
        "fixed"
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 50,
      "type": "PrimitiveNode",
      "pos": [
        110,
        620
      ],
      "size": [
        210,
        82
      ],
      "flags": {},
      "order": 3,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "INT",
          "type": "INT",
          "widget": {
            "name": "width"
          },
          "links": [
            135,
            136
          ]
        }
      ],
      "title": "width",
      "properties": {
        "Run widget replace on values": false
      },
      "widgets_values": [
        1024,
        "fixed"
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 49,
      "type": "Note",
      "pos": [
        368.29086262543825,
        -205.44885961550833
      ],
      "size": [
        210,
        112.67995780780103
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "properties": {},
      "widgets_values": [
        "Unbypass (CTRL-B) the ReferenceLatent nodes to give ref images. Chain more of them to give more images."
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 25,
      "type": "RandomNoise",
      "pos": [
        480,
        600
      ],
      "size": [
        315,
        82
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "NOISE",
          "type": "NOISE",
          "links": [
            37
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "RandomNoise"
      },
      "widgets_values": [
        1064877805904688,
        "randomize"
      ],
      "color": "#2a363b",
      "bgcolor": "#3f5159"
    },
    {
      "id": 6,
      "type": "CLIPTextEncode",
      "pos": [
        384,
        240
      ],
      "size": [
        422.84503173828125,
        164.31304931640625
      ],
      "flags": {},
      "order": 13,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 117
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            41
          ]
        }
      ],
      "title": "CLIP Text Encode (Positive Prompt)",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "cute anime girl with gigantic fennec ears and a big fluffy fox tail with long wavy blonde hair and large blue eyes blonde colored eyelashes wearing a pink sweater a large oversized gold trimmed black winter coat and a long blue maxi skirt and a red scarf, she is happy while singing on stage like an idol while holding a microphone, there are colorful lights, it is a postcard held by a hand in front of a beautiful city at sunset and there is cursive writing that says \"Flux 2, Now in ComfyUI\""
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 38,
      "type": "CLIPLoader",
      "pos": [
        50,
        270
      ],
      "size": [
        298.1818181818182,
        106
      ],
      "flags": {},
      "order": 6,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "CLIP",
          "type": "CLIP",
          "links": [
            117
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "CLIPLoader"
      },
      "widgets_values": [
        "mistral_3_small_flux2_bf16.safetensors",
        "flux2",
        "default"
      ]
    },
    {
      "id": 12,
      "type": "UNETLoader",
      "pos": [
        48,
        144
      ],
      "size": [
        315,
        82
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            133
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "UNETLoader"
      },
      "widgets_values": [
        "flux2_dev_fp8mixed.safetensors",
        "default"
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 10,
      "type": "VAELoader",
      "pos": [
        48,
        432
      ],
      "size": [
        311.81634521484375,
        60.429901123046875
      ],
      "flags": {},
      "order": 8,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "VAE",
          "type": "VAE",
          "slot_index": 0,
          "links": [
            12,
            120,
            127
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "VAELoader"
      },
      "widgets_values": [
        "flux2-vae.safetensors"
      ]
    },
    {
      "id": 42,
      "type": "LoadImage",
      "pos": [
        -466.82693606301376,
        -3.7311758283972303
      ],
      "size": [
        274.080078125,
        314
      ],
      "flags": {},
      "order": 9,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "links": [
            123
          ]
        },
        {
          "name": "MASK",
          "type": "MASK",
          "links": null
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "LoadImage"
      },
      "widgets_values": [
        "fennec_girl_sing.png",
        "image"
      ]
    },
    {
      "id": 43,
      "type": "ReferenceLatent",
      "pos": [
        362.3569995644578,
        -36.81875998117904
      ],
      "size": [
        197.712890625,
        46
      ],
      "flags": {},
      "order": 19,
      "mode": 4,
      "inputs": [
        {
          "name": "conditioning",
          "type": "CONDITIONING",
          "link": 129
        },
        {
          "name": "latent",
          "shape": 7,
          "type": "LATENT",
          "link": 125
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "links": [
            130
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "ReferenceLatent"
      },
      "widgets_values": []
    },
    {
      "id": 39,
      "type": "ReferenceLatent",
      "pos": [
        351.86683673369737,
        49.883904926480916
      ],
      "size": [
        197.712890625,
        46
      ],
      "flags": {},
      "order": 18,
      "mode": 4,
      "inputs": [
        {
          "name": "conditioning",
          "type": "CONDITIONING",
          "link": 118
        },
        {
          "name": "latent",
          "shape": 7,
          "type": "LATENT",
          "link": 121
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "links": [
            129
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "ReferenceLatent"
      },
      "widgets_values": []
    },
    {
      "id": 9,
      "type": "SaveImage",
      "pos": [
        1181.5190575458278,
        -312.5419270552867
      ],
      "size": [
        985.3012084960938,
        1060.3828125
      ],
      "flags": {},
      "order": 23,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 9
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75"
      },
      "widgets_values": [
        "ComfyUI"
      ]
    },
    {
      "id": 47,
      "type": "EmptyFlux2LatentImage",
      "pos": [
        516.5582473495501,
        444.55528282853845
      ],
      "size": [
        270,
        106
      ],
      "flags": {},
      "order": 11,
      "mode": 0,
      "inputs": [
        {
          "name": "width",
          "type": "INT",
          "widget": {
            "name": "width"
          },
          "link": 135
        },
        {
          "name": "height",
          "type": "INT",
          "widget": {
            "name": "height"
          },
          "link": 137
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "links": [
            131
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.75",
        "Node name for S&R": "EmptyFlux2LatentImage"
      },
      "widgets_values": [
        1024,
        1024,
        1
      ]
    }
  ],
  "links": [
    [
      9,
      8,
      0,
      9,
      0,
      "IMAGE"
    ],
    [
      12,
      10,
      0,
      8,
      1,
      "VAE"
    ],
    [
      19,
      16,
      0,
      13,
      2,
      "SAMPLER"
    ],
    [
      24,
      13,
      0,
      8,
      0,
      "LATENT"
    ],
    [
      30,
      22,
      0,
      13,
      1,
      "GUIDER"
    ],
    [
      37,
      25,
      0,
      13,
      0,
      "NOISE"
    ],
    [
      41,
      6,
      0,
      26,
      0,
      "CONDITIONING"
    ],
    [
      117,
      38,
      0,
      6,
      0,
      "CLIP"
    ],
    [
      118,
      26,
      0,
      39,
      0,
      "CONDITIONING"
    ],
    [
      120,
      10,
      0,
      40,
      1,
      "VAE"
    ],
    [
      121,
      40,
      0,
      39,
      1,
      "LATENT"
    ],
    [
      122,
      41,
      0,
      40,
      0,
      "IMAGE"
    ],
    [
      123,
      42,
      0,
      41,
      0,
      "IMAGE"
    ],
    [
      125,
      44,
      0,
      43,
      1,
      "LATENT"
    ],
    [
      126,
      45,
      0,
      44,
      0,
      "IMAGE"
    ],
    [
      127,
      10,
      0,
      44,
      1,
      "VAE"
    ],
    [
      128,
      46,
      0,
      45,
      0,
      "IMAGE"
    ],
    [
      129,
      39,
      0,
      43,
      0,
      "CONDITIONING"
    ],
    [
      130,
      43,
      0,
      22,
      1,
      "CONDITIONING"
    ],
    [
      131,
      47,
      0,
      13,
      4,
      "LATENT"
    ],
    [
      132,
      48,
      0,
      13,
      3,
      "SIGMAS"
    ],
    [
      133,
      12,
      0,
      22,
      0,
      "MODEL"
    ],
    [
      135,
      50,
      0,
      47,
      0,
      "INT"
    ],
    [
      136,
      50,
      0,
      48,
      0,
      "INT"
    ],
    [
      137,
      51,
      0,
      47,
      1,
      "INT"
    ],
    [
      138,
      51,
      0,
      48,
      1,
      "INT"
    ]
  ],
  "groups": [],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.5827712929782028,
      "offset": [
        654.1458285816999,
        623.0557939510758
      ]
    },
    "frontendVersion": "1.30.6",
    "groupNodes": {}
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/flux.2-dev.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "6": {
    "inputs": {
      "text": "cute anime girl with gigantic fennec ears and a big fluffy fox tail with long wavy blonde hair and large blue eyes blonde colored eyelashes wearing a pink sweater a large oversized gold trimmed black winter coat and a long blue maxi skirt and a red scarf, she is happy while singing on stage like an idol while holding a microphone, there are colorful lights, it is a postcard held by a hand in front of a beautiful city at sunset and there is cursive writing that says \"Flux 2, Now in ComfyUI\"",
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
  "8": {
    "inputs": {
      "samples": [
        "13",
        0
      ],
      "vae": [
        "10",
        0
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "9": {
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
  "10": {
    "inputs": {
      "vae_name": "flux2-vae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "12": {
    "inputs": {
      "unet_name": "flux2_dev_fp8mixed.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "13": {
    "inputs": {
      "noise": [
        "25",
        0
      ],
      "guider": [
        "22",
        0
      ],
      "sampler": [
        "16",
        0
      ],
      "sigmas": [
        "48",
        0
      ],
      "latent_image": [
        "47",
        0
      ]
    },
    "class_type": "SamplerCustomAdvanced",
    "_meta": {
      "title": "SamplerCustomAdvanced"
    }
  },
  "16": {
    "inputs": {
      "sampler_name": "euler"
    },
    "class_type": "KSamplerSelect",
    "_meta": {
      "title": "KSamplerSelect"
    }
  },
  "22": {
    "inputs": {
      "model": [
        "12",
        0
      ],
      "conditioning": [
        "26",
        0
      ]
    },
    "class_type": "BasicGuider",
    "_meta": {
      "title": "BasicGuider"
    }
  },
  "25": {
    "inputs": {
      "noise_seed": "__RANDOM_INT__"
    },
    "class_type": "RandomNoise",
    "_meta": {
      "title": "RandomNoise"
    }
  },
  "26": {
    "inputs": {
      "guidance": 4,
      "conditioning": [
        "6",
        0
      ]
    },
    "class_type": "FluxGuidance",
    "_meta": {
      "title": "FluxGuidance"
    }
  },
  "38": {
    "inputs": {
      "clip_name": "mistral_3_small_flux2_bf16.safetensors",
      "type": "flux2",
      "device": "default"
    },
    "class_type": "CLIPLoader",
    "_meta": {
      "title": "Load CLIP"
    }
  },
  "40": {
    "inputs": {
      "pixels": [
        "41",
        0
      ],
      "vae": [
        "10",
        0
      ]
    },
    "class_type": "VAEEncode",
    "_meta": {
      "title": "VAE Encode"
    }
  },
  "41": {
    "inputs": {
      "upscale_method": "area",
      "megapixels": 1,
      "image": [
        "42",
        0
      ]
    },
    "class_type": "ImageScaleToTotalPixels",
    "_meta": {
      "title": "ImageScaleToTotalPixels"
    }
  },
  "42": {
    "inputs": {
      "image": "fennec_girl_sing.png"
    },
    "class_type": "LoadImage",
    "_meta": {
      "title": "Load Image"
    }
  },
  "44": {
    "inputs": {
      "pixels": [
        "45",
        0
      ],
      "vae": [
        "10",
        0
      ]
    },
    "class_type": "VAEEncode",
    "_meta": {
      "title": "VAE Encode"
    }
  },
  "45": {
    "inputs": {
      "upscale_method": "area",
      "megapixels": 1,
      "image": [
        "46",
        0
      ]
    },
    "class_type": "ImageScaleToTotalPixels",
    "_meta": {
      "title": "ImageScaleToTotalPixels"
    }
  },
  "46": {
    "inputs": {
      "image": "sunset.png"
    },
    "class_type": "LoadImage",
    "_meta": {
      "title": "Load Image"
    }
  },
  "47": {
    "inputs": {
      "width": 1024,
      "height": 1024,
      "batch_size": 1
    },
    "class_type": "EmptyFlux2LatentImage",
    "_meta": {
      "title": "Empty Flux 2 Latent"
    }
  },
  "48": {
    "inputs": {
      "steps": 20,
      "width": 1024,
      "height": 1024
    },
    "class_type": "Flux2Scheduler",
    "_meta": {
      "title": "Flux2Scheduler"
    }
  }
}
WORKFLOW_API_JSON
    payload_json=$(jq -n --argjson workflow "$workflow_json" '{input: {workflow_json: $workflow}}')
    rm /opt/comfyui-api-wrapper/payloads/*.json
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/flux.2-dev.json
}

main