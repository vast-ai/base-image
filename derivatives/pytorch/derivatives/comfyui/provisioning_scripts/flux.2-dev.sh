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
  "last_node_id": 66,
  "last_link_id": 163,
  "nodes": [
    {
      "id": 48,
      "type": "Flux2Scheduler",
      "pos": [
        -160,
        260
      ],
      "size": [
        222.3482666015625,
        106
      ],
      "flags": {},
      "order": 13,
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
        "ver": "0.3.71",
        "Node name for S&R": "Flux2Scheduler"
      },
      "widgets_values": [
        20,
        1248,
        832
      ]
    },
    {
      "id": 22,
      "type": "BasicGuider",
      "pos": [
        -160,
        70
      ],
      "size": [
        222.3482666015625,
        46
      ],
      "flags": {},
      "order": 21,
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
          "link": 153
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
        "ver": "0.3.71",
        "Node name for S&R": "BasicGuider"
      },
      "widgets_values": []
    },
    {
      "id": 40,
      "type": "VAEEncode",
      "pos": [
        -405.919921875,
        880
      ],
      "size": [
        140,
        46
      ],
      "flags": {
        "collapsed": true
      },
      "order": 16,
      "mode": 4,
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
        "ver": "0.3.71",
        "Node name for S&R": "VAEEncode"
      },
      "widgets_values": []
    },
    {
      "id": 42,
      "type": "LoadImage",
      "pos": [
        -540,
        400
      ],
      "size": [
        274.080078125,
        314
      ],
      "flags": {},
      "order": 0,
      "mode": 4,
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
        "ver": "0.3.71",
        "Node name for S&R": "LoadImage"
      },
      "widgets_values": [
        "image_flux2_input_image.png",
        "image"
      ]
    },
    {
      "id": 44,
      "type": "VAEEncode",
      "pos": [
        -840,
        880
      ],
      "size": [
        140,
        46
      ],
      "flags": {
        "collapsed": true
      },
      "order": 17,
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
        "ver": "0.3.71",
        "Node name for S&R": "VAEEncode"
      },
      "widgets_values": []
    },
    {
      "id": 39,
      "type": "ReferenceLatent",
      "pos": [
        -463.6328125,
        920
      ],
      "size": [
        197.712890625,
        46
      ],
      "flags": {},
      "order": 20,
      "mode": 4,
      "inputs": [
        {
          "name": "conditioning",
          "type": "CONDITIONING",
          "link": 145
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
            153
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.71",
        "Node name for S&R": "ReferenceLatent"
      },
      "widgets_values": []
    },
    {
      "id": 26,
      "type": "FluxGuidance",
      "pos": [
        -520,
        220
      ],
      "size": [
        317.4000244140625,
        58
      ],
      "flags": {},
      "order": 18,
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
            144
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.71",
        "Node name for S&R": "FluxGuidance"
      },
      "widgets_values": [
        4
      ],
      "color": "#233",
      "bgcolor": "#355"
    },
    {
      "id": 16,
      "type": "KSamplerSelect",
      "pos": [
        -160,
        160
      ],
      "size": [
        222.3482666015625,
        58
      ],
      "flags": {},
      "order": 1,
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
        "ver": "0.3.71",
        "Node name for S&R": "KSamplerSelect"
      },
      "widgets_values": [
        "euler"
      ]
    },
    {
      "id": 43,
      "type": "ReferenceLatent",
      "pos": [
        -910,
        920
      ],
      "size": [
        197.712890625,
        46
      ],
      "flags": {},
      "order": 19,
      "mode": 0,
      "inputs": [
        {
          "name": "conditioning",
          "type": "CONDITIONING",
          "link": 144
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
            145
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.71",
        "Node name for S&R": "ReferenceLatent"
      },
      "widgets_values": []
    },
    {
      "id": 50,
      "type": "PrimitiveNode",
      "pos": [
        -160,
        470
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
        1248,
        "fixed"
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 51,
      "type": "PrimitiveNode",
      "pos": [
        -160,
        590
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
        832,
        "fixed"
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 10,
      "type": "VAELoader",
      "pos": [
        -973.1827364834872,
        230
      ],
      "size": [
        298.1818181818182,
        60.429901123046875
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "VAE",
          "type": "VAE",
          "slot_index": 0,
          "links": [
            120,
            127,
            159
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.71",
        "Node name for S&R": "VAELoader",
        "models": [
          {
            "name": "flux2-vae.safetensors",
            "url": "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors",
            "directory": "vae"
          }
        ]
      },
      "widgets_values": [
        "flux2-vae.safetensors"
      ]
    },
    {
      "id": 47,
      "type": "EmptyFlux2LatentImage",
      "pos": [
        110,
        500
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
            161
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.71",
        "Node name for S&R": "EmptyFlux2LatentImage"
      },
      "widgets_values": [
        1248,
        832,
        1
      ]
    },
    {
      "id": 13,
      "type": "SamplerCustomAdvanced",
      "pos": [
        90,
        -40
      ],
      "size": [
        272.3617858886719,
        124.53733825683594
      ],
      "flags": {},
      "order": 22,
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
          "link": 161
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
        "ver": "0.3.71",
        "Node name for S&R": "SamplerCustomAdvanced"
      },
      "widgets_values": []
    },
    {
      "id": 41,
      "type": "ImageScaleToTotalPixels",
      "pos": [
        -535.919921875,
        750
      ],
      "size": [
        270,
        82
      ],
      "flags": {},
      "order": 11,
      "mode": 4,
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
        "ver": "0.3.71",
        "Node name for S&R": "ImageScaleToTotalPixels"
      },
      "widgets_values": [
        "lanczos",
        1
      ]
    },
    {
      "id": 8,
      "type": "VAEDecode",
      "pos": [
        -140,
        750
      ],
      "size": [
        210,
        46
      ],
      "flags": {},
      "order": 23,
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
          "link": 159
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
        "ver": "0.3.71",
        "Node name for S&R": "VAEDecode"
      },
      "widgets_values": []
    },
    {
      "id": 45,
      "type": "ImageScaleToTotalPixels",
      "pos": [
        -970,
        750
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
        "ver": "0.3.71",
        "Node name for S&R": "ImageScaleToTotalPixels"
      },
      "widgets_values": [
        "lanczos",
        1
      ]
    },
    {
      "id": 60,
      "type": "Note",
      "pos": [
        -980,
        1070
      ],
      "size": [
        460,
        130
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "properties": {},
      "widgets_values": [
        "Unbypass (CTRL-B) the ReferenceLatent nodes to give ref images.\n\nIf you want to add more reference images, you can add multiple reference images by following the pattern.\n\nIf you don't want any reference image, just select all ReferenceLatent nodes and then use CTRL-B to bypass them, turning the workflow into a Text to Image workflow."
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 6,
      "type": "CLIPTextEncode",
      "pos": [
        -630,
        -50
      ],
      "size": [
        430,
        200
      ],
      "flags": {},
      "order": 15,
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
        "ver": "0.3.71",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "The woman is wearing a small pale yellow knitted beanie, with a white fabric patch on the front right, embroidered with big gray text “FLUX.2 COMFY.” Kepp the face"
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 12,
      "type": "UNETLoader",
      "pos": [
        -973.1827364834872,
        -42
      ],
      "size": [
        298.1818181818182,
        82
      ],
      "flags": {},
      "order": 6,
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
        "ver": "0.3.71",
        "Node name for S&R": "UNETLoader",
        "models": [
          {
            "name": "flux2_dev_fp8mixed.safetensors",
            "url": "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/diffusion_models/flux2_dev_fp8mixed.safetensors",
            "directory": "diffusion_models"
          }
        ]
      },
      "widgets_values": [
        "flux2_dev_fp8mixed.safetensors",
        "default"
      ]
    },
    {
      "id": 25,
      "type": "RandomNoise",
      "pos": [
        -160,
        -50
      ],
      "size": [
        222.3482666015625,
        82
      ],
      "flags": {},
      "order": 7,
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
        "ver": "0.3.71",
        "Node name for S&R": "RandomNoise"
      },
      "widgets_values": [
        649422536169327,
        "randomize"
      ]
    },
    {
      "id": 46,
      "type": "LoadImage",
      "pos": [
        -970,
        390
      ],
      "size": [
        274.080078125,
        314
      ],
      "flags": {},
      "order": 8,
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
        "ver": "0.3.71",
        "Node name for S&R": "LoadImage"
      },
      "widgets_values": [
        "image_flux2_input_image.png",
        "image"
      ]
    },
    {
      "id": 9,
      "type": "SaveImage",
      "pos": [
        410,
        -90
      ],
      "size": [
        985.3012084960938,
        1060.3828125
      ],
      "flags": {},
      "order": 24,
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
        "ver": "0.3.71"
      },
      "widgets_values": [
        "Flux2"
      ]
    },
    {
      "id": 38,
      "type": "CLIPLoader",
      "pos": [
        -973.1827364834872,
        82
      ],
      "size": [
        298.1818181818182,
        106
      ],
      "flags": {},
      "order": 9,
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
        "ver": "0.3.71",
        "Node name for S&R": "CLIPLoader",
        "models": [
          {
            "name": "mistral_3_small_flux2_bf16.safetensors",
            "url": "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/text_encoders/mistral_3_small_flux2_bf16.safetensors",
            "directory": "text_encoders"
          }
        ]
      },
      "widgets_values": [
        "mistral_3_small_flux2_bf16.safetensors",
        "flux2",
        "default"
      ]
    },
    {
      "id": 66,
      "type": "MarkdownNote",
      "pos": [
        -1520,
        -90
      ],
      "size": [
        510,
        420
      ],
      "flags": {
        "collapsed": false
      },
      "order": 10,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Model links",
      "properties": {},
      "widgets_values": [
        "We are using quantized weights in this workflow, the original flux 2 repo is [here](https://huggingface.co/black-forest-labs/FLUX.2-dev/)\n## Report issue\n\nIf you found any issues when running this workflow, [report template issue here](https://github.com/Comfy-Org/workflow_templates/issues)\n\n\n## Model links\n\n**text_encoders**\n\n- [mistral_3_small_flux2_bf16.safetensors](https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/text_encoders/mistral_3_small_flux2_bf16.safetensors)\n\n**diffusion_models**\n\n- [flux2_dev_fp8mixed.safetensors](https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/diffusion_models/flux2_dev_fp8mixed.safetensors)\n\n**vae**\n\n- [flux2-vae.safetensors](https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors)\n\n\nModel Storage Location\n\n```\n📂 ComfyUI/\n├── 📂 models/\n│   ├── 📂 text_encoders/\n│   │      └── mistral_3_small_flux2_bf16.safetensors\n│   ├── 📂 diffusion_models/\n│   │      └── flux2_dev_fp8mixed.safetensors\n│   └── 📂 vae/\n│          └── flux2-vae.safetensors\n```\n"
      ],
      "color": "#432",
      "bgcolor": "#653"
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
    ],
    [
      144,
      26,
      0,
      43,
      0,
      "CONDITIONING"
    ],
    [
      145,
      43,
      0,
      39,
      0,
      "CONDITIONING"
    ],
    [
      153,
      39,
      0,
      22,
      1,
      "CONDITIONING"
    ],
    [
      159,
      10,
      0,
      8,
      1,
      "VAE"
    ],
    [
      161,
      47,
      0,
      13,
      4,
      "LATENT"
    ]
  ],
  "groups": [
    {
      "id": 1,
      "title": "Step 1 - Upload models",
      "bounding": [
        -980,
        -120,
        318.18181818181813,
        416.0299011230469
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 2,
      "title": "Custom sampler",
      "bounding": [
        -170,
        -120,
        558.5359191894531,
        501.6
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 3,
      "title": "Step 4- Image size",
      "bounding": [
        -170,
        400,
        560,
        285.6
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 4,
      "title": "Step2 - Prompt",
      "bounding": [
        -640,
        -120,
        450,
        420
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 5,
      "title": "Step 3- Reference images",
      "bounding": [
        -980,
        320,
        790,
        700
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    }
  ],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.8276479435838239,
      "offset": [
        1068.473081101383,
        225.26235348666503
      ]
    },
    "frontendVersion": "1.30.6",
    "groupNodes": {},
    "VHS_latentpreview": false,
    "VHS_latentpreviewrate": 0,
    "VHS_MetadataImage": true,
    "VHS_KeepIntermediate": true,
    "workflowRendererVersion": "LG"
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/realvisxl-v5.0.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "6": {
    "inputs": {
      "text": "The woman is wearing a small pale yellow knitted beanie, with a white fabric patch on the front right, embroidered with big gray text “FLUX.2 COMFY.” Kepp the face",
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
      "filename_prefix": "Flux2",
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
        "43",
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
      "noise_seed": 649422536169327
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
  "43": {
    "inputs": {
      "conditioning": [
        "26",
        0
      ],
      "latent": [
        "44",
        0
      ]
    },
    "class_type": "ReferenceLatent",
    "_meta": {
      "title": "ReferenceLatent"
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
      "upscale_method": "lanczos",
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
      "image": "image_flux2_input_image.png"
    },
    "class_type": "LoadImage",
    "_meta": {
      "title": "Load Image"
    }
  },
  "47": {
    "inputs": {
      "width": 1248,
      "height": 832,
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
      "width": 1248,
      "height": 832
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
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/realvisxl-v5.0.json
}

main