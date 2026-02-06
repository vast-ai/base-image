# Linux Desktop
> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Linux%20Desktop)**

## What is this template?

This template gives you a **full Linux desktop environment** running in a Docker container. Access your desktop through a low-latency WebRTC interface (Selkies) or traditional VNC. It's perfect for GPU-accelerated applications, 3D modeling, video editing, or any workflow that needs a graphical interface.

**Think:** *"Your own private Linux workstation in the cloud with GPU acceleration."*

> **Latest builds:** Docker images are automatically rebuilt monthly with the latest Blender and system updates. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Run GPU-accelerated desktop applications** like Blender, video editors, or 3D modeling software
- **Access a full desktop** through your web browser with audio support
- **Use multiple connection methods** - WebRTC, VNC, or SSH
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
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Linux%20Desktop)"** when you've found an instance that works for you

### **Step 2: Wait for Setup**
The desktop environment will initialize automatically *(this takes a couple of minutes on first boot)*

### **Step 3: Access Your Desktop**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

Choose your preferred connection method:
- **Selkies Desktop** (port 6100) - Best performance, audio support, WebRTC
- **Guacamole VNC** (port 6200) - Browser-based VNC, good compatibility
- **Direct VNC** (port 5900) - Use your preferred VNC client

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings.

---

## Connection Methods

### **Selkies WebRTC**

This is the most performant interface. It has audio support and is very responsive, but requires a fast and stable connection between your computer and the instance.

Only a single user can connect to this interface at once.

The `x264enc` encoder is selected as the default for best compatibility, but you may change this to `nvh264enc` for best performance.

A TURN server is included in the docker image, but if you would like to use your own TURN server, you can do so by specifying the `TURN_HOST`, `TURN_PORT`, `TURN_PROTOCOL`, `TURN_USERNAME` & `TURN_PASSWORD` environment variables.

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
- **Google Chrome** - Web browser
- **GPU Benchmarks** - glmark2 and Unigine Heaven for testing GPU performance

### **Dynamic Provisioning**
Need specific software installed automatically? Set the `PROVISIONING_SCRIPT` environment variable to a plain-text script URL (GitHub, Gist, etc.), and we'll run your setup script on first boot!

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
| `PROVISIONING_SCRIPT` | | URL pointing to a shell script (GitHub Repo, Gist) |
| `SELKIES_ENCODER` | `x264enc` | Video encoder (`x264enc` or `nvh264enc`) |
| `VNC_PASSWORD` | `$OPEN_BUTTON_TOKEN` | Custom password for VNC connections |
| `TURN_HOST` | `$PUBLIC_IPADDR` | TURN host |
| `TURN_PORT` | `$VAST_TCP_PORT_73478` | TURN port |
| `TURN_PROTOCOL` | `tcp` | TURN protocol |
| `TURN_USERNAME` | `turnuser` | TURN username |
| `TURN_PASSWORD` | `$OPEN_BUTTON_TOKEN` | TURN password |

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

If your workflow requires features that don't work in a container, try the **[Ubuntu Desktop (VM)](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Ubuntu%20Desktop%20(VM))** template instead. The VM template provides a full virtual machine with complete isolation and no container restrictions.

---

## Need More Help?

- **Selkies Project:** [GitHub Repository](https://github.com/selkies-project)
- **Apache Guacamole:** [Official Documentation](https://guacamole.apache.org/)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/linux-desktop)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
