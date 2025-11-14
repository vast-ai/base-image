#!/bin/bash

set -euo pipefail

### Configuration ###
WORKSPACE_DIR="${WORKSPACE:-/workspace}"
MODELS_DIR="${WORKSPACE_DIR}/ComfyUI/models"
HF_SEMAPHORE_DIR="${WORKSPACE_DIR}/hf_download_sem_$$"
HF_MAX_PARALLEL=3
MODEL_LOG=${MODEL_LOG:-/var/log/portal/comfyui.log}

# Model declarations: "URL|OUTPUT_PATH"
HF_MODELS=(
  "https://huggingface.co/Comfy-Org/ACE-Step_ComfyUI_repackaged/resolve/main/all_in_one/ace_step_v1_3.5b.safetensors
  |$MODELS_DIR/checkpoints/ace_step_v1_3.5b.safetensors"
)
### End Configuration ###

script_cleanup() {
   rm -rf "$HF_SEMAPHORE_DIR"
}

# If this script fails we cannot let a serverless worker be marked as ready.
script_error() {
    local exit_code=$?
    local line_number=$1
    echo "[ERROR] Provisioning Script failed at line $line_number with exit code $exit_code" | tee -a "$MODEL_LOG"
}

trap script_cleanup EXIT
trap 'script_error $LINENO' ERR

main() {
    . /venv/main/bin/activate
    set_cleanup_job
    mkdir -p "$HF_SEMAPHORE_DIR"
    write_workflow
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
# Adjustments as recommended at https://huggingface.co/Comfy-Org/ACE-Step_ComfyUI_repackaged/discussions/1#6845136255e580607333edda, converted to API format
write_workflow() {
    # Define the workflow JSON once
    local workflow_json
    read -r -d '' workflow_json << 'WORKFLOW_JSON' || true
{
  "14": {
    "inputs": {
      "tags": "funk, pop, soul, rock, melodic, guitar, drums, bass, keyboard, percussion, 105 BPM, energetic, upbeat, groovy, vibrant, dynamic",
      "lyrics": "[verse]\nNeon lights they flicker bright\nCity hums in dead of night\nRhythms pulse through concrete veins\nLost in echoes of refrains\n\n[verse]\nBassline groovin' in my chest\nHeartbeats match the city's zest\nElectric whispers fill the air\nSynthesized dreams everywhere\n\n[chorus]\nTurn it up and let it flow\nFeel the fire let it grow\nIn this rhythm we belong\nHear the night sing out our song\n\n[verse]\nGuitar strings they start to weep\nWake the soul from silent sleep\nEvery note a story told\nIn this night we’re bold and gold\n\n[bridge]\nVoices blend in harmony\nLost in pure cacophony\nTimeless echoes timeless cries\nSoulful shouts beneath the skies\n\n[verse]\nKeyboard dances on the keys\nMelodies on evening breeze\nCatch the tune and hold it tight\nIn this moment we take flight\n",
      "lyrics_strength": 0.9900000000000002,
      "clip": [
        "40",
        1
      ]
    },
    "class_type": "TextEncodeAceStepAudio",
    "_meta": {
      "title": "TextEncodeAceStepAudio"
    }
  },
  "17": {
    "inputs": {
      "seconds": 180,
      "batch_size": 1
    },
    "class_type": "EmptyAceStepLatentAudio",
    "_meta": {
      "title": "EmptyAceStepLatentAudio"
    }
  },
  "18": {
    "inputs": {
      "samples": [
        "52",
        0
      ],
      "vae": [
        "40",
        2
      ]
    },
    "class_type": "VAEDecodeAudio",
    "_meta": {
      "title": "VAE Decode Audio"
    }
  },
  "40": {
    "inputs": {
      "ckpt_name": "ace_step_v1_3.5b.safetensors"
    },
    "class_type": "CheckpointLoaderSimple",
    "_meta": {
      "title": "Load Checkpoint"
    }
  },
  "44": {
    "inputs": {
      "conditioning": [
        "14",
        0
      ]
    },
    "class_type": "ConditioningZeroOut",
    "_meta": {
      "title": "ConditioningZeroOut"
    }
  },
  "49": {
    "inputs": {
      "model": [
        "51",
        0
      ],
      "operation": [
        "50",
        0
      ]
    },
    "class_type": "LatentApplyOperationCFG",
    "_meta": {
      "title": "LatentApplyOperationCFG"
    }
  },
  "50": {
    "inputs": {
      "multiplier": 1.15
    },
    "class_type": "LatentOperationTonemapReinhard",
    "_meta": {
      "title": "LatentOperationTonemapReinhard"
    }
  },
  "51": {
    "inputs": {
      "shift": 6,
      "model": [
        "40",
        0
      ]
    },
    "class_type": "ModelSamplingSD3",
    "_meta": {
      "title": "ModelSamplingSD3"
    }
  },
  "52": {
    "inputs": {
      "seed": 763314908749429,
      "steps": 65,
      "cfg": 4,
      "sampler_name": "er_sde",
      "scheduler": "linear_quadratic",
      "denoise": 1,
      "model": [
        "49",
        0
      ],
      "positive": [
        "14",
        0
      ],
      "negative": [
        "44",
        0
      ],
      "latent_image": [
        "17",
        0
      ]
    },
    "class_type": "KSampler",
    "_meta": {
      "title": "KSampler"
    }
  },
  "59": {
    "inputs": {
      "filename_prefix": "audio/ComfyUI",
      "quality": "V0",
      "audioUI": "",
      "audio": [
        "18",
        0
      ]
    },
    "class_type": "SaveAudioMP3",
    "_meta": {
      "title": "Save Audio (MP3)"
    }
  }
}
WORKFLOW_JSON

    # Write first file (original format)
    rm -f /opt/comfyui-api-wrapper/payloads/*
    cat > /opt/comfyui-api-wrapper/payloads/ace-step-v1-3.5b.json << EOF
{
    "input": {
        "request_id": "",
        "workflow_json": ${workflow_json}
    }
}
EOF

    # Wait for directory to exist (from git clone), then write second file
    local benchmark_dir="$WORKSPACE/vast-pyworker/workers/comfyui-json/misc"
    while [[ ! -d "$benchmark_dir" ]]; do
        sleep 1
    done
    
    echo "$workflow_json" > "$benchmark_dir/benchmark.json"
}

# Add a cron job to remove older (oldest +24 hours) output files if disk space is low
set_cleanup_job() {
    if [[ ! -f /opt/instance-tools/bin/clean-output.sh ]]; then
        cat > /opt/instance-tools/bin/clean-output.sh << 'CLEAN_OUTPUT'
#!/bin/bash
output_dir="${WORKSPACE:-/workspace}/ComfyUI/output/"
min_free_mb=512
available_space=$(df -m "${output_dir}" | awk 'NR==2 {print $4}')
if [[ "$available_space" -lt "$min_free_mb" ]]; then
    oldest=$(find "${output_dir}" -mindepth 1 -type f -printf "%T@\n" 2>/dev/null | sort -n | head -1 | awk '{printf "%.0f", $1}')
    if [[ -n "$oldest" ]]; then
        cutoff=$(awk "BEGIN {printf \"%.0f\", ${oldest}+86400}")
        # Only delete files
        find "${output_dir}" -mindepth 1 -type f ! -newermt "@${cutoff}" -delete
        # Delete broken symlinks
        find "${output_dir}" -mindepth 1 -xtype l -delete
        # Now delete *empty* directories separately
        find "${output_dir}" -mindepth 1 -type d -empty -delete
    fi
fi
CLEAN_OUTPUT
        chmod +x /opt/instance-tools/bin/clean-output.sh
    fi

    if ! crontab -l 2>/dev/null | grep -qF 'clean-output.sh'; then
        (crontab -l 2>/dev/null; echo '*/10 * * * * /opt/instance-tools/bin/clean-output.sh') | crontab -
    fi
}

main