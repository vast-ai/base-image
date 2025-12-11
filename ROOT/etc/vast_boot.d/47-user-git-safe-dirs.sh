#!/bin/bash

set_git_safe_dirs() {
    # Prevents dubious ownership issues
    find "${WORKSPACE}" -name ".git" | while read gitpath; do
        parent_dir=$(dirname "$gitpath")
        if ! grep -q "$parent_dir" /root/.gitconfig > /dev/null 2>&1; then
            git config --global --add safe.directory "$parent_dir"
        fi
        if ! grep -q "$parent_dir" /home/user/.gitconfig > /dev/null 2>&1; then
            sudo -u user HOME=/home/user git config --global --add safe.directory "$parent_dir"
        fi
    done
}

set_git_safe_dirs