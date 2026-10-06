#!/bin/bash
# Selkies' gamepad interposer adds its pads to a listing of /dev/input, so the directory
# must exist; a container's /dev is rebuilt at every start (ADR 0050). A /dev/input passed
# in from the host keeps its own ownership and mode. Identical in every desktop image.
[[ -d /dev/input ]] || mkdir -pm1777 /dev/input
