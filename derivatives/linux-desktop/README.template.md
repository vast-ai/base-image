# Linux Desktop
> **[Create an Instance](https://cloud.vast.ai/?ref_id=525202&creator_id=525202&name=Linux%20Desktop)**

## What is this template?

This template gives you a **full Linux desktop environment** running in a Docker container. Access your desktop through a low-latency streaming interface (Selkies) or traditional VNC. It's perfect for GPU-accelerated applications, 3D modeling, video editing, or any workflow that needs a graphical interface.

**Think:** *"Your own private Linux workstation in the cloud with GPU acceleration."*

> **Latest builds:** Docker images are automatically rebuilt monthly with the latest Blender and system updates. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Run GPU-accelerated desktop applications** like Blender, video editors, or 3D modeling software
- **Access a full desktop** through your web browser with audio support
- **Use multiple connection methods** - Selkies, VNC, or SSH
- **Install any Linux software** with root access
- **Sync files across devices** with built-in Syncthing
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Need a GPU-accelerated desktop for 3D rendering, video editing, or graphics work
- Want to run Linux desktop applications without local hardware
- Need remote access to a powerful workstation
- Are developing or testing desktop applications
- Want a portable development environment accessible from anywhere

---

## Quick Start Guide

### **Step 1: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=525202&creator_id=525202&name=Linux%20Desktop)"** when you've found an instance that works for you

### **Step 2: Wait for Setup**
The desktop environment will initialize automatically *(this takes a couple of minutes on first boot)*

### **Step 3: Access Your Desktop**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

Choose your preferred connection method:
- **Selkies Desktop** (port 6100) - Best performance, audio support, hardware video encoding
- **Guacamole VNC** (port 6200) - Browser-based VNC, good compatibility
- **Direct VNC** (port 5900) - Use your preferred VNC client

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings.

---

## Connection Methods

### **Selkies Desktop**

This is the most performant interface. It has audio support and is very responsive, but requires a fast and stable connection between your computer and the instance.

**How it streams depends on how you open it:**
- **Over HTTPS** (`ENABLE_HTTPS=true` with the [Vast.ai certificate](https://docs.vast.ai/instances/jupyter) installed, or through a tunnel) it streams over a WebSocket on the same port. This is the best experience and needs nothing else.
- **Over plain HTTP** your browser withholds the video decoder this mode needs, so the desktop streams over WebRTC instead, through the TURN server included in the image. This needs ports `73478` (TCP) and `73479/udp` mapped in your template. Without them, the page asks you to switch to HTTPS.

Set `SELKIES_MODE=webrtc` or `SELKIES_MODE=websockets` to choose the transport yourself.

**Microphone and camera** from your browser are passed into the desktop. Your browser is asked for them only while an application on the desktop is recording, and they are released shortly after it stops. The camera appears to applications as `/dev/video0` and the microphone as the default input. Set `SELKIES_MICROPHONE_ON_START` or `SELKIES_WEBCAM_ON_START` to `true` (ask on connect) or `false` (side-panel toggle only) to change that.

**Video encoding** uses the GPU's hardware encoder (NVENC) wherever it works, including instances given only some of a machine's GPUs, and falls back to software encoding otherwise. GPUs without a video encoder (A100, H100, H200) always encode in software.

To use your own TURN server instead of the included one, set `TURN_HOST`, `TURN_PORT`, `TURN_PROTOCOL`, `TURN_USERNAME` and `TURN_PASSWORD` (or the `SELKIES_TURN_*` equivalents).

### **Guacamole VNC**

This is a simple VNC interface available in your web browser.

VNC is transported by the Guacamole protocol and may be slightly faster than direct VNC.

### **VNC**

You can use your preferred VNC client to connect on the port mapped to `INSTANCE_IP:5900`

You will need to supply the value of environment variable `$OPEN_BUTTON_TOKEN` as a password. This is randomly generated on first boot and is also visible in the instance logs.

You can also set environment variable `VNC_PASSWORD` to choose your own password.

### **SSH Port Forwarding**

Instead of connecting to ports exposed to the internet, you can use SSH port forwarding to securely access services on your instance. This method connects directly to the internal ports, bypassing the Caddy authentication layer.

---

## Key Features

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| x11vnc | 5900 | 5900 |
| Selkies Desktop | 6100 | 16100 |
| Guacamole VNC | 6200 | 16200 |
| Syncthing | 8384 | 18384 |
| Jupyter | 8080 | 8080 |

When creating SSH port forwards, use the internal ports listed above. These ports don't require authentication or TLS since they're only accessible through your SSH tunnel.

### **Instance Portal (Application Manager)**
- Web-based dashboard for managing your applications
- Cloudflare tunnels for easy sharing (no port forwarding needed!)
- Log monitoring for running services
- Start and stop services with a few clicks

### **Pre-installed Applications**
- **Blender** - 3D modeling and rendering
- **Firefox** - Web browser (all architectures)
- **Google Chrome** - Web browser (x86_64 only; Google does not publish an aarch64 build)
- **GPU Benchmarks** - glmark2 for testing GPU performance

### **Dynamic Provisioning**
Need specific software installed automatically? You have two options, both run on first boot:

- **`PROVISIONING_MANIFEST`** *(recommended)* - URL or local path to a declarative YAML manifest. The provisioner handles ordering, retries, idempotency, and logging for you. A ready-to-use example that installs the [Pinokio](https://pinokio.computer/) launcher (x86_64 **and** arm64) lives at [`provisioning/pinokio.yaml`](provisioning/pinokio.yaml).
- **`PROVISIONING_SCRIPT`** - URL to a plain-text shell script (GitHub, Gist, etc.) for fully imperative setups.

If both are set, the manifest runs first and the script runs as its final phase.

### **Multiple Access Methods**
| Method | Best For | What You Get |
|--------|----------|--------------|
| **Selkies** | Interactive desktop work | Low-latency desktop with audio |
| **VNC** | Compatibility | Works with any VNC client |
| **Jupyter** | File management & terminals | Browser-based coding environment |
| **SSH** | Terminal work | Full command-line access |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart selkies`
- Add your own services with simple configuration files

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Set the workspace directory |
| `ENABLE_AUTH` | `true` | Enable or disable token-based and basic authentication |
| `AUTH_EXCLUDE` | | Disable authentication for specific ports (e.g. `6006,8384`) |
| `ENABLE_HTTPS` | `false` | Enable or disable TLS |
| `PORTAL_CONFIG` | See docs | Configures the Instance Portal and application startup |
| `PROVISIONING_MANIFEST` | | URL or path to a declarative YAML provisioning manifest (recommended; e.g. `provisioning/pinokio.yaml`) |
| `PROVISIONING_SCRIPT` | | URL pointing to a shell script (GitHub Repo, Gist) |
| `SELKIES_ENCODER` | `h264enc` | Video encoder: `h264enc` (hardware where available), `h265enc`, `vp8enc`, `vp9enc`, `av1enc`, `jpeg` |
| `SELKIES_MODE` | chosen at launch | Force the transport: `websockets` or `webrtc` |
| `VNC_PASSWORD` | `$OPEN_BUTTON_TOKEN` | Custom password for VNC connections |
| `TURN_HOST` | | Use your own TURN server instead of the included one |
| `TURN_PORT` | `3478` | Your TURN server's port |
| `TURN_PROTOCOL` | `udp` | Your TURN server's protocol (`udp` or `tcp`) |
| `TURN_USERNAME` | | Your TURN server's username |
| `TURN_PASSWORD` | | Your TURN server's password |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `cuda-12.9-ubuntu24.04-2026-02-01`). This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html), which allows newer CUDA toolkit versions to run on older drivers. This is only available on **datacenter GPUs** (e.g., H100, A100, L40S, RTX Pro series). Consumer GPUs do not support forward compatibility and require a driver that natively supports the CUDA version.

---

## Customization Tips

### **Installing Software**
```bash
# You have root access - install anything!
apt update && apt install -y your-favorite-package

# Install Python packages
uv pip install --system requests

# Add system services
echo "your-service-config" > /etc/supervisor/conf.d/my-app.conf
supervisorctl reread && supervisorctl update
```

### **Template Customization**
Want to save your perfect setup? Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later.

---

## Container Limitations

This desktop runs inside a Docker container, which provides excellent performance and portability but has some limitations compared to a full virtual machine:

- **No user namespaces** - Applications that require creating nested containers or user namespaces (like some sandboxed browsers, Flatpak, or Snap) may not work
- **No systemd** - Services are managed by Supervisor instead of systemd
- **Shared kernel** - The container shares the host's kernel, so kernel modules cannot be loaded

### Need Full VM Capabilities?

If your workflow requires features that don't work in a container, try the **[Ubuntu Desktop (VM)](https://cloud.vast.ai/?ref_id=525202&creator_id=525202&name=Ubuntu%20Desktop%20(VM))** template instead. The VM template provides a full virtual machine with complete isolation and no container restrictions.

---

## Need More Help?

- **Selkies Project:** [GitHub Repository](https://github.com/selkies-project)
- **Apache Guacamole:** [Official Documentation](https://guacamole.apache.org/)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/linux-desktop)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
