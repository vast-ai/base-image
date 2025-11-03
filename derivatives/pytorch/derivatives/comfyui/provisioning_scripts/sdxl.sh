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
  "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0_0.9vae.safetensors
  |$MODELS_DIR/checkpoints/sd_xl_base_1.0_0.9vae.safetensors"
  "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors
  |$MODELS_DIR/checkpoints/sd_xl_base_1.0.safetensors"
  "https://huggingface.co/stabilityai/stable-diffusion-xl-refiner-1.0/resolve/main/sd_xl_refiner_1.0_0.9vae.safetensors
  |$MODELS_DIR/checkpoints/sd_xl_refiner_1.0_0.9vae.safetensors"
  "https://huggingface.co/stabilityai/stable-diffusion-xl-refiner-1.0/resolve/main/sd_xl_refiner_1.0.safetensors
  |$MODELS_DIR/checkpoints/sd_xl_refiner_1.0.safetensors"
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
  "id": "ba17d20f-e71b-4075-bf7d-80003102da39",
  "revision": 0,
  "last_node_id": 48,
  "last_link_id": 44,
  "nodes": [
    {
      "id": 36,
      "type": "Note",
      "pos": [
        8.959289798168081,
        -527.6496759614387
      ],
      "size": [
        315.70074462890625,
        147.9551239013672
      ],
      "flags": {},
      "order": 0,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Note - Load Checkpoint BASE",
      "properties": {
        "text": ""
      },
      "widgets_values": [
        "This is a checkpoint model loader. \n - This is set up automatically with the optimal settings for whatever SD model version you choose to use.\n - In this example, it is for the Base SDXL model\n - This node is also used for SD1.5 and SD2.x models\n \nNOTE: When loading in another person's workflow, be sure to manually choose your own *local* model. This also applies to LoRas and all their deviations"
      ],
      "color": "#323",
      "bgcolor": "#535"
    },
    {
      "id": 37,
      "type": "Note",
      "pos": [
        677.4360599783068,
        -523.1053038329113
      ],
      "size": [
        330,
        140
      ],
      "flags": {},
      "order": 1,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Note - Load Checkpoint REFINER",
      "properties": {
        "text": ""
      },
      "widgets_values": [
        "This is a checkpoint model loader. \n - This is set up automatically with the optimal settings for whatever SD model version you choose to use.\n - In this example, it is for the Refiner SDXL model\n\nNOTE: When loading in another person's workflow, be sure to manually choose your own *local* model. This also applies to LoRas and all their deviations."
      ],
      "color": "#323",
      "bgcolor": "#535"
    },
    {
      "id": 5,
      "type": "EmptyLatentImage",
      "pos": [
        3.468924004214869,
        288.72858337366586
      ],
      "size": [
        300,
        110
      ],
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            27
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
      ],
      "color": "#323",
      "bgcolor": "#535"
    },
    {
      "id": 17,
      "type": "VAEDecode",
      "pos": [
        1570.8482798022494,
        -6.506884995129528
      ],
      "size": [
        200,
        50
      ],
      "flags": {},
      "order": 17,
      "mode": 0,
      "inputs": [
        {
          "name": "samples",
          "type": "LATENT",
          "link": 25
        },
        {
          "name": "vae",
          "type": "VAE",
          "link": 34
        }
      ],
      "outputs": [
        {
          "name": "IMAGE",
          "type": "IMAGE",
          "slot_index": 0,
          "links": [
            28
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "VAEDecode"
      },
      "widgets_values": [],
      "color": "#332922",
      "bgcolor": "#593930"
    },
    {
      "id": 41,
      "type": "Note",
      "pos": [
        1510.8482798022496,
        93.49311500487052
      ],
      "size": [
        320,
        120
      ],
      "flags": {},
      "order": 3,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Note - VAE Decoder",
      "properties": {
        "text": ""
      },
      "widgets_values": [
        "This node will take the latent data from the KSampler and, using the VAE, it will decode it into visible data\n\nVAE = Latent --> Visible\n\nThis can then be sent to the Save Image node to be saved as a PNG."
      ],
      "color": "#332922",
      "bgcolor": "#593930"
    },
    {
      "id": 42,
      "type": "Note",
      "pos": [
        23.468924004215527,
        438.72858337366563
      ],
      "size": [
        260,
        210
      ],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Note - Empty Latent Image",
      "properties": {
        "text": ""
      },
      "widgets_values": [
        "This node sets the image's resolution in Width and Height.\n\nNOTE: For SDXL, it is recommended to use trained values listed below:\n - 1024 x 1024\n - 1152 x 896\n - 896  x 1152\n - 1216 x 832\n - 832  x 1216\n - 1344 x 768\n - 768  x 1344\n - 1536 x 640\n - 640  x 1536"
      ],
      "color": "#323",
      "bgcolor": "#535"
    },
    {
      "id": 12,
      "type": "CheckpointLoaderSimple",
      "pos": [
        667.4360599783068,
        -674.1053038329121
      ],
      "size": [
        350,
        100
      ],
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "MODEL",
          "type": "MODEL",
          "slot_index": 0,
          "links": [
            14
          ]
        },
        {
          "name": "CLIP",
          "type": "CLIP",
          "slot_index": 1,
          "links": [
            19,
            20
          ]
        },
        {
          "name": "VAE",
          "type": "VAE",
          "slot_index": 2,
          "links": [
            34
          ]
        }
      ],
      "title": "Load Checkpoint - REFINER",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CheckpointLoaderSimple"
      },
      "widgets_values": [
        "sd_xl_refiner_1.0.safetensors"
      ],
      "color": "#323",
      "bgcolor": "#535"
    },
    {
      "id": 4,
      "type": "CheckpointLoaderSimple",
      "pos": [
        -7.040710201831908,
        -677.6496759614386
      ],
      "size": [
        350,
        100
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
            10
          ]
        },
        {
          "name": "CLIP",
          "type": "CLIP",
          "slot_index": 1,
          "links": [
            3,
            5
          ]
        },
        {
          "name": "VAE",
          "type": "VAE",
          "slot_index": 2,
          "links": []
        }
      ],
      "title": "Load Checkpoint - BASE",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CheckpointLoaderSimple"
      },
      "widgets_values": [
        "sd_xl_base_1.0.safetensors"
      ],
      "color": "#323",
      "bgcolor": "#535"
    },
    {
      "id": 47,
      "type": "PrimitiveNode",
      "pos": [
        1010.3067882302635,
        418.83916004329257
      ],
      "size": [
        210,
        82
      ],
      "flags": {},
      "order": 7,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "INT",
          "type": "INT",
          "widget": {
            "name": "end_at_step"
          },
          "slot_index": 0,
          "links": [
            43,
            44
          ]
        }
      ],
      "title": "end_at_step",
      "properties": {
        "Run widget replace on values": false
      },
      "widgets_values": [
        20,
        "fixed"
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 45,
      "type": "PrimitiveNode",
      "pos": [
        1012.3067882302632,
        271.8391600432927
      ],
      "size": [
        210,
        82
      ],
      "flags": {},
      "order": 8,
      "mode": 0,
      "inputs": [],
      "outputs": [
        {
          "name": "INT",
          "type": "INT",
          "widget": {
            "name": "steps"
          },
          "links": [
            38,
            41
          ]
        }
      ],
      "title": "steps",
      "properties": {
        "Run widget replace on values": false
      },
      "widgets_values": [
        25,
        "fixed"
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 48,
      "type": "Note",
      "pos": [
        1008.7781042289396,
        555.227771891982
      ],
      "size": [
        213.90769258609475,
        110.17156742821044
      ],
      "flags": {},
      "order": 9,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "properties": {
        "text": ""
      },
      "widgets_values": [
        "These can be used to control the total sampling steps and the step at which the sampling switches to the refiner."
      ],
      "color": "#432",
      "bgcolor": "#653"
    },
    {
      "id": 16,
      "type": "CLIPTextEncode",
      "pos": [
        944.0392377139365,
        -23.49195919570613
      ],
      "size": [
        340,
        140
      ],
      "flags": {},
      "order": 12,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 20
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            24
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "text, watermark"
      ],
      "color": "#322",
      "bgcolor": "#533"
    },
    {
      "id": 15,
      "type": "CLIPTextEncode",
      "pos": [
        944.0392377139365,
        -203.49195919570593
      ],
      "size": [
        340,
        140
      ],
      "flags": {},
      "order": 11,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 19
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            23
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "daytime scenery  sky nature dark blue bottle with a galaxy stars milky way in it"
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 6,
      "type": "CLIPTextEncode",
      "pos": [
        5.606421578905653,
        -183.90841961008994
      ],
      "size": [
        320,
        160
      ],
      "flags": {},
      "order": 13,
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
          "slot_index": 0,
          "links": [
            11
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "daytime sky nature dark blue galaxy bottle"
      ],
      "color": "#232",
      "bgcolor": "#353"
    },
    {
      "id": 7,
      "type": "CLIPTextEncode",
      "pos": [
        5.606421578905653,
        26.09158038990996
      ],
      "size": [
        320,
        150
      ],
      "flags": {},
      "order": 14,
      "mode": 0,
      "inputs": [
        {
          "name": "clip",
          "type": "CLIP",
          "link": 5
        }
      ],
      "outputs": [
        {
          "name": "CONDITIONING",
          "type": "CONDITIONING",
          "slot_index": 0,
          "links": [
            12
          ]
        }
      ],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "CLIPTextEncode"
      },
      "widgets_values": [
        "text, watermark"
      ],
      "color": "#322",
      "bgcolor": "#533"
    },
    {
      "id": 11,
      "type": "KSamplerAdvanced",
      "pos": [
        1498.857778032649,
        -669.6431882748839
      ],
      "size": [
        300,
        340
      ],
      "flags": {},
      "order": 16,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 14
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 23
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 24
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 13
        },
        {
          "name": "steps",
          "type": "INT",
          "widget": {
            "name": "steps"
          },
          "link": 38
        },
        {
          "name": "start_at_step",
          "type": "INT",
          "widget": {
            "name": "start_at_step"
          },
          "link": 44
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            25
          ]
        }
      ],
      "title": "KSampler (Advanced) - REFINER",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "KSamplerAdvanced"
      },
      "widgets_values": [
        "disable",
        0,
        "fixed",
        25,
        8,
        "euler",
        "normal",
        20,
        10000,
        "disable"
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 10,
      "type": "KSamplerAdvanced",
      "pos": [
        499.79766520677464,
        -263.39686085046054
      ],
      "size": [
        300,
        334
      ],
      "flags": {},
      "order": 15,
      "mode": 0,
      "inputs": [
        {
          "name": "model",
          "type": "MODEL",
          "link": 10
        },
        {
          "name": "positive",
          "type": "CONDITIONING",
          "link": 11
        },
        {
          "name": "negative",
          "type": "CONDITIONING",
          "link": 12
        },
        {
          "name": "latent_image",
          "type": "LATENT",
          "link": 27
        },
        {
          "name": "steps",
          "type": "INT",
          "widget": {
            "name": "steps"
          },
          "link": 41
        },
        {
          "name": "end_at_step",
          "type": "INT",
          "widget": {
            "name": "end_at_step"
          },
          "link": 43
        }
      ],
      "outputs": [
        {
          "name": "LATENT",
          "type": "LATENT",
          "slot_index": 0,
          "links": [
            13
          ]
        }
      ],
      "title": "KSampler (Advanced) - BASE",
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66",
        "Node name for S&R": "KSamplerAdvanced"
      },
      "widgets_values": [
        "enable",
        1102966908265645,
        "randomize",
        25,
        8,
        "euler",
        "normal",
        0,
        20,
        "enable"
      ],
      "color": "#223",
      "bgcolor": "#335"
    },
    {
      "id": 19,
      "type": "SaveImage",
      "pos": [
        1926.258079666269,
        -659.4349773607368
      ],
      "size": [
        735.552734375,
        823.98193359375
      ],
      "flags": {},
      "order": 18,
      "mode": 0,
      "inputs": [
        {
          "name": "images",
          "type": "IMAGE",
          "link": 28
        }
      ],
      "outputs": [],
      "properties": {
        "cnr_id": "comfy-core",
        "ver": "0.3.66"
      },
      "widgets_values": [
        "ComfyUI"
      ],
      "color": "#222",
      "bgcolor": "#000"
    },
    {
      "id": 40,
      "type": "Note",
      "pos": [
        421.5733340979502,
        159.13978662958547
      ],
      "size": [
        451.5049743652344,
        424.4164123535156
      ],
      "flags": {},
      "order": 10,
      "mode": 0,
      "inputs": [],
      "outputs": [],
      "title": "Note - KSampler  ADVANCED General Information",
      "properties": {
        "text": ""
      },
      "widgets_values": [
        "Here are the settings that SHOULD stay in place if you want this workflow to work correctly:\n - add_noise: enable = This adds random noise into the picture so the model can denoise it\n\n - return_with_leftover_noise: enable = This sends the latent image data and all it's leftover noise to the next KSampler node.\n\nThe settings to pay attention to:\n - control_after_generate = generates a new random seed after each workflow job completed.\n - steps = This is the amount of iterations you would like to run the positive and negative CLIP prompts through. Each Step will add (positive) or remove (negative) pixels based on what stable diffusion \"thinks\" should be there according to the model's training\n - cfg = This is how much you want SDXL to adhere to the prompt. Lower CFG gives you more creative but often blurrier results. Higher CFG (recommended max 10) gives you stricter results according to the CLIP prompt. If the CFG value is too high, it can also result in \"burn-in\" where the edges of the picture become even stronger, often highlighting details in unnatural ways.\n - sampler_name = This is the sampler type, and unfortunately different samplers and schedulers have better results with fewer steps, while others have better success with higher steps. This will require experimentation on your part!\n - scheduler = The algorithm/method used to choose the timesteps to denoise the picture.\n - start_at_step = This is the step number the KSampler will start out it's process of de-noising the picture or \"removing the random noise to reveal the picture within\". The first KSampler usually starts with Step 0. Starting at step 0 is the same as setting denoise to 1.0 in the regular Sampler node.\n - end_at_step = This is the step number the KSampler will stop it's process of de-noising the picture. If there is any remaining leftover noise and return_with_leftover_noise is enabled, then it will pass on the left over noise to the next KSampler (assuming there is another one)."
      ],
      "color": "#223",
      "bgcolor": "#335"
    }
  ],
  "links": [
    [
      3,
      4,
      1,
      6,
      0,
      "CLIP"
    ],
    [
      5,
      4,
      1,
      7,
      0,
      "CLIP"
    ],
    [
      10,
      4,
      0,
      10,
      0,
      "MODEL"
    ],
    [
      11,
      6,
      0,
      10,
      1,
      "CONDITIONING"
    ],
    [
      12,
      7,
      0,
      10,
      2,
      "CONDITIONING"
    ],
    [
      13,
      10,
      0,
      11,
      3,
      "LATENT"
    ],
    [
      14,
      12,
      0,
      11,
      0,
      "MODEL"
    ],
    [
      19,
      12,
      1,
      15,
      0,
      "CLIP"
    ],
    [
      20,
      12,
      1,
      16,
      0,
      "CLIP"
    ],
    [
      23,
      15,
      0,
      11,
      1,
      "CONDITIONING"
    ],
    [
      24,
      16,
      0,
      11,
      2,
      "CONDITIONING"
    ],
    [
      25,
      11,
      0,
      17,
      0,
      "LATENT"
    ],
    [
      27,
      5,
      0,
      10,
      3,
      "LATENT"
    ],
    [
      28,
      17,
      0,
      19,
      0,
      "IMAGE"
    ],
    [
      34,
      12,
      2,
      17,
      1,
      "VAE"
    ],
    [
      38,
      45,
      0,
      11,
      4,
      "INT"
    ],
    [
      41,
      45,
      0,
      10,
      4,
      "INT"
    ],
    [
      43,
      47,
      0,
      10,
      5,
      "INT"
    ],
    [
      44,
      47,
      0,
      11,
      5,
      "INT"
    ]
  ],
  "groups": [
    {
      "id": 1,
      "title": "Base Prompt",
      "bounding": [
        -19.393578421094244,
        -267.90841961008977,
        366,
        463
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 2,
      "title": "Refiner Prompt",
      "bounding": [
        923.0392377139357,
        -288.4919591957065,
        376,
        429
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 3,
      "title": "Load in BASE SDXL Model",
      "bounding": [
        -17.040710201831907,
        -757.6496759614386,
        369,
        399
      ],
      "color": "#a1309b",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 4,
      "title": "Load in REFINER SDXL Model",
      "bounding": [
        648.4360599783067,
        -763.1053038329121,
        391,
        400
      ],
      "color": "#a1309b",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 5,
      "title": "Empty Latent Image",
      "bounding": [
        -17.035178449815245,
        214.6085125477655,
        339,
        443
      ],
      "color": "#a1309b",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 6,
      "title": "VAE Decoder",
      "bounding": [
        1492.077238465945,
        -85.10947885529936,
        360,
        350
      ],
      "color": "#b06634",
      "font_size": 24,
      "flags": {}
    },
    {
      "id": 7,
      "title": "Step Control",
      "bounding": [
        977.7781042289396,
        160.2277718919819,
        284,
        524
      ],
      "color": "#3f789e",
      "font_size": 24,
      "flags": {}
    }
  ],
  "config": {},
  "extra": {
    "ds": {
      "scale": 0.587762150533612,
      "offset": [
        204.18570126592553,
        774.3778868315504
      ]
    },
    "frontendVersion": "1.28.7"
  },
  "version": 0.4
}
WORKFLOW_JSON
    echo "$workflow_json" > "${WORKFLOW_DIR}/sdxl.json"
}

write_api_workflow() {
    local workflow_json
    local payload_json
    read -r -d '' workflow_json << 'WORKFLOW_API_JSON' || true
{
  "4": {
    "inputs": {
      "ckpt_name": "sd_xl_base_1.0.safetensors"
    },
    "class_type": "CheckpointLoaderSimple",
    "_meta": {
      "title": "Load Checkpoint - BASE"
    }
  },
  "5": {
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
  "6": {
    "inputs": {
      "text": "daytime sky nature dark blue galaxy bottle",
      "clip": [
        "4",
        1
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "7": {
    "inputs": {
      "text": "text, watermark",
      "clip": [
        "4",
        1
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "10": {
    "inputs": {
      "add_noise": "enable",
      "noise_seed": 1102966908265645,
      "steps": 25,
      "cfg": 8,
      "sampler_name": "euler",
      "scheduler": "normal",
      "start_at_step": 0,
      "end_at_step": 20,
      "return_with_leftover_noise": "enable",
      "model": [
        "4",
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
        "5",
        0
      ]
    },
    "class_type": "KSamplerAdvanced",
    "_meta": {
      "title": "KSampler (Advanced) - BASE"
    }
  },
  "11": {
    "inputs": {
      "add_noise": "disable",
      "noise_seed": 0,
      "steps": 25,
      "cfg": 8,
      "sampler_name": "euler",
      "scheduler": "normal",
      "start_at_step": 20,
      "end_at_step": 10000,
      "return_with_leftover_noise": "disable",
      "model": [
        "12",
        0
      ],
      "positive": [
        "15",
        0
      ],
      "negative": [
        "16",
        0
      ],
      "latent_image": [
        "10",
        0
      ]
    },
    "class_type": "KSamplerAdvanced",
    "_meta": {
      "title": "KSampler (Advanced) - REFINER"
    }
  },
  "12": {
    "inputs": {
      "ckpt_name": "sd_xl_refiner_1.0.safetensors"
    },
    "class_type": "CheckpointLoaderSimple",
    "_meta": {
      "title": "Load Checkpoint - REFINER"
    }
  },
  "15": {
    "inputs": {
      "text": "daytime scenery  sky nature dark blue bottle with a galaxy stars milky way in it",
      "clip": [
        "12",
        1
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "16": {
    "inputs": {
      "text": "text, watermark",
      "clip": [
        "12",
        1
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "17": {
    "inputs": {
      "samples": [
        "11",
        0
      ],
      "vae": [
        "12",
        2
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "19": {
    "inputs": {
      "filename_prefix": "ComfyUI",
      "images": [
        "17",
        0
      ]
    },
    "class_type": "SaveImage",
    "_meta": {
      "title": "Save Image"
    }
  }
}
WORKFLOW_API_JSON
    payload_json=$(jq -n --argjson workflow "$workflow_json" '{input: {workflow_json: $workflow}}')
    rm /opt/comfyui-api-wrapper/payloads/*.json
    echo "$payload_json" > /opt/comfyui-api-wrapper/payloads/sdxl.json
}

main