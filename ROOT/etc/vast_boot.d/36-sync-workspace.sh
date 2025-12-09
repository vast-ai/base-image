#!/bin/bash

# Move workspace from image into container/volume only if the target does not exist
sync_workspace() {
    workspace=${WORKSPACE:-/workspace}
    
    mkdir -p "${workspace}/" > /dev/null 2>&1
    
    # Copy each item in /opt/workspace-internal and avoid clobbering user generated files if volume
    find /opt/workspace-internal -mindepth 1 -maxdepth 1 -print0 | while IFS= read -r -d '' item; do
        basename_item=$(basename "$item")
        target="${workspace}/${basename_item}"
        
        # Create item-specific lock file for fine-grained locking
        lockfile="${workspace}/.sync_${basename_item}.lock"
        
        # Use flock for this specific item - wait indefinitely for the lock
        (
            # First try with timeout, then wait indefinitely if needed
            if ! flock -x -w 10 9; then
                echo "Lock busy for ${basename_item}, waiting for completion..." >&2
                # Wait indefinitely for the lock (blocks until available)
                flock -x 9
            fi
            
            if [[ -d "$item" && ! -e "$target" ]]; then
                # Copy directory recursively
                cp -ru "$item" "${workspace}/"
                # Apply ownership and permissions to the copied directory and its contents
            elif [[ -f "$item" && ! -e "$target" ]]; then
                # Copy file
                cp -f "$item" "${workspace}/"
            fi
            
        ) 9>"$lockfile"
    done

    # Remove default files that do not belong in the workspace (no lock needed for removal)
    rm -f "${workspace}/onstart.sh" "${workspace}/ports.log"
}

sync_workspace