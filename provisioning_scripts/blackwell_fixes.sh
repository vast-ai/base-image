#!/bin/bash
# DEPRECATED — this script is a no-op and exists only so that instances with it
# pinned as PROVISIONING_SCRIPT do not fail at boot on a 404.
#
# It used to upgrade NCCL on non-cu128 images and reinstall PyTorch from a March
# 2025 nightly (torch==2.7.0.dev20250312+cu128) for Blackwell GPUs. Both halves
# are now wrong: the torch pin is a year-old nightly that would DOWNGRADE any
# current image, and the base image already installs the newest NCCL that NVIDIA
# ever built for the cuda12.8 series.
#
# Use a cu128-or-newer image on Blackwell hardware instead:
#   https://docs.vast.ai/rtx-5090-guide
#
# Safe to remove from your template's PROVISIONING_SCRIPT.
echo "blackwell_fixes.sh is deprecated and does nothing."
echo "Use a cu128 (or newer) image on Blackwell GPUs — see https://docs.vast.ai/rtx-5090-guide"
exit 0
