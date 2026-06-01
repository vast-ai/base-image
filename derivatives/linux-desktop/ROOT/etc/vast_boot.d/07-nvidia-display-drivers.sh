#!/bin/bash

# The desktop image needs OpenGL/GLX/EGL/OptiX/Vulkan ready at boot. The reusable
# installer lives in the base image (and is runnable on demand on any image);
# call it here so the desktop has display libs without manual intervention.
/opt/instance-tools/bin/install-display-drivers
