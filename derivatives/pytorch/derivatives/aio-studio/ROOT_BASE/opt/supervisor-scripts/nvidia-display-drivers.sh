#!/bin/bash

# Install NVIDIA display driver libraries to provide EGL/GLX/Vulkan for
# 32-bit and 64-bit applications.
#
# The nvidia-container-toolkit injects 64-bit driver libs from the host but
# often omits 32-bit libs and may be missing GLX components.  Installing
# via apt fails because the repo version rarely matches the host kernel
# module exactly.
#
# This script downloads the exact .run package for the host's driver version,
# extracts it, and copies shared libraries to
# /opt/nvidia-drivers/ registered via ldconfig.

NVIDIA_DRIVERS_DIR="/opt/nvidia-drivers"
# Lower priority than container toolkit libs
NVIDIA_DRIVERS_LDCONF="/etc/ld.so.conf.d/zz-nvidia-drivers.conf"

configure_nvidia_display_drivers() {
    command -v nvidia-smi &>/dev/null || return 0

    # Read driver version from kernel module
    local DRIVER_VERSION
    DRIVER_VERSION="$(head -n1 </proc/driver/nvidia/version | awk '{ for(i=1;i<=NF;i++) if($i ~ /^[0-9]+\.[0-9]+(\.[0-9]+)?$/) { print $i; exit } }')"
    if [[ -z "$DRIVER_VERSION" ]]; then
        echo "Warning: Could not determine NVIDIA driver version from /proc"
        return 0
    fi

    # If we already extracted libs for this exact driver version, ensure
    # ldconfig paths are registered and return early.
    if [[ -f "${NVIDIA_DRIVERS_DIR}/.driver-version" ]]; then
        local CACHED_VERSION
        CACHED_VERSION="$(<"${NVIDIA_DRIVERS_DIR}/.driver-version")"
        if [[ "$CACHED_VERSION" == "$DRIVER_VERSION" ]]; then
            if [[ ! -f "$NVIDIA_DRIVERS_LDCONF" ]]; then
                {
                    echo "${NVIDIA_DRIVERS_DIR}/lib64"
                    echo "${NVIDIA_DRIVERS_DIR}/lib32"
                } > "$NVIDIA_DRIVERS_LDCONF"
                ldconfig
            fi
            echo "NVIDIA display driver ${DRIVER_VERSION} libs already installed"
            return 0
        fi
        echo "Driver version changed (${CACHED_VERSION} -> ${DRIVER_VERSION}), clearing stale libs"
        rm -rf "${NVIDIA_DRIVERS_DIR}"
        rm -f "${NVIDIA_DRIVERS_LDCONF}"
        ldconfig
    fi

    # Always extract both 64-bit and 32-bit driver libraries from the .run
    # package.  The nvidia-container-toolkit injects some 64-bit libs from
    # the host but often omits 32-bit libs, Vulkan presentation components,
    # and on newer GPUs (e.g. Blackwell) may provide incomplete libraries
    # that cause crashes.
    echo "Installing NVIDIA display driver ${DRIVER_VERSION} libs (64-bit + 32-bit)..."

    # Download the .run package
    local DRIVER_ARCH="x86_64"
    local RUN_FILE="NVIDIA-Linux-${DRIVER_ARCH}-${DRIVER_VERSION}.run"
    local TMPDIR
    TMPDIR="$(mktemp -d /tmp/nvidia-driver-XXXXXX)"

    local DOWNLOAD_OK=false
    for BASE_URL in \
        "https://international.download.nvidia.com/XFree86/Linux-${DRIVER_ARCH}/${DRIVER_VERSION}" \
        "https://international.download.nvidia.com/tesla/${DRIVER_VERSION}"; do
        echo "Downloading ${BASE_URL}/${RUN_FILE} ..."
        if curl -fL --progress-bar -o "${TMPDIR}/${RUN_FILE}" "${BASE_URL}/${RUN_FILE}"; then
            DOWNLOAD_OK=true
            break
        fi
    done

    if ! $DOWNLOAD_OK; then
        echo "Warning: Failed to download NVIDIA driver ${DRIVER_VERSION} .run package"
        rm -rf "$TMPDIR"
        return 0
    fi

    # Extract without running nvidia-installer
    local EXTRACT_DIR="${TMPDIR}/extracted"
    if ! sh "${TMPDIR}/${RUN_FILE}" --extract-only --target "$EXTRACT_DIR" 2>/dev/null; then
        echo "Warning: Failed to extract NVIDIA driver .run package"
        rm -rf "$TMPDIR"
        return 0
    fi

    # Copy libraries and Vulkan manifests
    mkdir -p "${NVIDIA_DRIVERS_DIR}/lib64" "${NVIDIA_DRIVERS_DIR}/lib32"

    # 64-bit libs are in the extraction root
    if [[ -d "$EXTRACT_DIR" ]]; then
        cp -a "$EXTRACT_DIR"/*.so* "${NVIDIA_DRIVERS_DIR}/lib64/" 2>/dev/null
        for json in nvidia_icd.json nvidia_layers.json; do
            if [[ -f "$EXTRACT_DIR/$json" ]]; then
                cp -a "$EXTRACT_DIR/$json" "${NVIDIA_DRIVERS_DIR}/lib64/"
            fi
        done
        echo "Installed 64-bit NVIDIA display/Vulkan driver libs"
    fi

    # 32-bit libs are in the 32/ subdirectory
    if [[ -d "${EXTRACT_DIR}/32" ]]; then
        cp -a "${EXTRACT_DIR}/32"/*.so* "${NVIDIA_DRIVERS_DIR}/lib32/" 2>/dev/null
        echo "Installed 32-bit NVIDIA display driver libs"
    fi

    # Point the Vulkan loader at our manifests if they aren't already
    # in a standard search path.  Only write if the file is not a
    # read-only bind-mount from the toolkit.
    if [[ -f "${NVIDIA_DRIVERS_DIR}/lib64/nvidia_icd.json" ]]; then
        local icd_dest="/etc/vulkan/icd.d/nvidia_icd.json"
        if cp -f "${NVIDIA_DRIVERS_DIR}/lib64/nvidia_icd.json" "$icd_dest" 2>/dev/null; then
            echo "Installed Vulkan ICD manifest -> ${icd_dest}"
        fi
    fi
    if [[ -f "${NVIDIA_DRIVERS_DIR}/lib64/nvidia_layers.json" ]]; then
        local layers_dest="/etc/vulkan/implicit_layer.d/nvidia_layers.json"
        mkdir -p /etc/vulkan/implicit_layer.d
        if cp -f "${NVIDIA_DRIVERS_DIR}/lib64/nvidia_layers.json" "$layers_dest" 2>/dev/null; then
            echo "Installed Vulkan implicit layer manifest -> ${layers_dest}"
        fi
    fi

    # Record version for cache validation on next boot
    echo "$DRIVER_VERSION" > "${NVIDIA_DRIVERS_DIR}/.driver-version"

    # Clean up temp files
    rm -rf "$TMPDIR"

    # Register library paths with ldconfig
    {
        echo "${NVIDIA_DRIVERS_DIR}/lib64"
        echo "${NVIDIA_DRIVERS_DIR}/lib32"
    } > "$NVIDIA_DRIVERS_LDCONF"
    ldconfig

    echo "NVIDIA display driver ${DRIVER_VERSION} libs configured"
}

configure_nvidia_display_drivers
