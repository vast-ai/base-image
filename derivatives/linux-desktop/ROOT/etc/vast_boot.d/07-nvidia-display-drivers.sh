#!/bin/bash

# Install NVIDIA display driver libraries to provide EGL/GLX/Vulkan for
# 32-bit and 64-bit applications.
#
# The nvidia-container-toolkit injects 64-bit driver libs from the host but
# often omits 32-bit libs and Vulkan presentation components.  Installing
# via apt fails because the repo version rarely matches the host kernel
# module exactly.
#
# This script downloads the exact .run package for the host's driver version,
# extracts it, and copies shared libraries + Vulkan manifests to
# /opt/nvidia-drivers/ registered via ldconfig.

NVIDIA_DRIVERS_DIR="/opt/nvidia-drivers"
NVIDIA_DRIVERS_LDCONF="/etc/ld.so.conf.d/nvidia-drivers.conf"

# Libraries to check -- if any are missing from ldconfig we need to install.
# libGLX_nvidia  = GLX + Vulkan ICD implementation
# libEGL_nvidia  = EGL implementation
# libnvidia-glvkspirv = Vulkan SPIR-V compiler (internal Vulkan dep)
DISPLAY_LIBS=(libEGL_nvidia.so.0 libGLX_nvidia.so.0 libnvidia-glvkspirv.so)

check_libs_present() {
    # $1 = architecture filter for ldconfig -p (e.g. "x86-64" or empty for i386)
    local arch_filter="$1"
    local cache
    cache="$(ldconfig -p)"
    for lib in "${DISPLAY_LIBS[@]}"; do
        if [[ -n "$arch_filter" ]]; then
            echo "$cache" | grep -q "${lib}.*${arch_filter}" || return 1
        else
            # For 32-bit: match the lib but exclude x86-64 lines
            echo "$cache" | grep "$lib" | grep -qv 'x86-64' || return 1
        fi
    done
    return 0
}

configure_nvidia_display_drivers() {
    command -v nvidia-smi &>/dev/null || return 0

    # Read driver version from kernel module
    local DRIVER_VERSION
    DRIVER_VERSION="$(head -n1 </proc/driver/nvidia/version | awk '{ for(i=1;i<=NF;i++) if($i ~ /^[0-9]+\.[0-9]+(\.[0-9]+)?$/) { print $i; exit } }')"
    if [[ -z "$DRIVER_VERSION" ]]; then
        echo "Warning: Could not determine NVIDIA driver version from /proc"
        return 0
    fi

    # If cached libs exist for a different driver version, wipe them
    if [[ -f "${NVIDIA_DRIVERS_DIR}/.driver-version" ]]; then
        local CACHED_VERSION
        CACHED_VERSION="$(<"${NVIDIA_DRIVERS_DIR}/.driver-version")"
        if [[ "$CACHED_VERSION" != "$DRIVER_VERSION" ]]; then
            echo "Driver version changed (${CACHED_VERSION} -> ${DRIVER_VERSION}), clearing stale libs"
            rm -rf "${NVIDIA_DRIVERS_DIR}"
            rm -f "${NVIDIA_DRIVERS_LDCONF}"
            ldconfig
        fi
    fi

    # Detect which lib flavours are missing
    local NEED_64=false NEED_32=false

    if ! check_libs_present "x86-64"; then
        NEED_64=true
    fi

    # Only check 32-bit on amd64 hosts
    if [[ "$(dpkg --print-architecture 2>/dev/null)" == "amd64" ]]; then
        if ! check_libs_present ""; then
            NEED_32=true
        fi
    fi

    if ! $NEED_64 && ! $NEED_32; then
        echo "NVIDIA display/Vulkan driver libs already present"
        return 0
    fi

    echo "NVIDIA display driver libs needed (64-bit: ${NEED_64}, 32-bit: ${NEED_32})"

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

    if $NEED_64 && [[ -d "$EXTRACT_DIR" ]]; then
        # 64-bit libs are in the extraction root
        cp -a "$EXTRACT_DIR"/*.so* "${NVIDIA_DRIVERS_DIR}/lib64/" 2>/dev/null
        # Vulkan ICD and implicit layer manifests
        for json in nvidia_icd.json nvidia_layers.json; do
            if [[ -f "$EXTRACT_DIR/$json" ]]; then
                cp -a "$EXTRACT_DIR/$json" "${NVIDIA_DRIVERS_DIR}/lib64/"
            fi
        done
        echo "Installed 64-bit NVIDIA display/Vulkan driver libs"
    fi

    if $NEED_32 && [[ -d "${EXTRACT_DIR}/32" ]]; then
        # 32-bit libs are in the 32/ subdirectory
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
