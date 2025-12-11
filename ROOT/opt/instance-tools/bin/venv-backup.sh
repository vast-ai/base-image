#!/bin/bash

# Deprecated.  no longer auto-enabled

# Configuration
SYSTEM_VENV_DIR="/venv"
BACKUP_DIR="${WORKSPACE:-/workspace}/.venv-backups/${CONTAINER_ID}"
TIMESTAMP=$(date +"%Y-%m-%d-%H%M")
LOG_FILE="${BACKUP_DIR}/backup.log"
VENV_BACKUP_COUNT=${VENV_BACKUP_COUNT:-48}  # 24 hours of backups at 30-minute intervals

# No backup if user set 0
[[ $VENV_BACKUP_COUNT -eq 0 ]] && exit

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Log function
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Function to check if directory is a valid venv
is_venv() {
    local dir="$1"
    # Check for key virtual environment markers
    if [ -f "$dir/bin/activate" ] && [ -f "$dir/bin/python" ]; then
        return 0
    else
        return 1
    fi
}

# Function to process virtual environments in a directory
process_venvs() {
    local base_dir="$1"
    local found_venvs=0
    
    find "$base_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | while read -r venv_path; do
        if is_venv "$venv_path"; then
            found_venvs=1
            venv_name=$(basename "$venv_path")
            backup_file="${BACKUP_DIR}/venv-${venv_name}-${TIMESTAMP}.txt"

            log "Processing virtual environment: $venv_path"
                
            if source "$venv_path/bin/activate" 2>/dev/null; then
                if pip freeze > "$backup_file"; then
                    log "SUCCESS: Created backup at $backup_file"
                    ln -sf "$backup_file" "${BACKUP_DIR}/venv-${venv_name}-latest.txt"
                else
                    log "ERROR: Failed to create requirements file for $venv_path"
                fi
                deactivate
            else
                log "ERROR: Failed to activate virtual environment at $venv_path"
            fi

        fi
    done
    
    echo $found_venvs
}


process_venvs "$SYSTEM_VENV_DIR"


# Cleanup old backups (keep last 48 for each venv - 24 hours of backups)
cd "$BACKUP_DIR"
for venv_prefix in venv-*-latest.txt; do
    if [ -L "$venv_prefix" ]; then
        venv_name=$(echo "$venv_prefix" | sed 's/venv-\(.*\)-latest.txt/\1/')
        ls -t "venv-${venv_name}-"*.txt | grep -v "latest.txt" | tail -n +$((VENV_BACKUP_COUNT + 1)) | xargs -r rm --
    fi
done

log "Backup process completed"