# Ostris AI Toolkit

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Ostris%20AI%20Toolkit)**

## What is this template?

This template gives you a **complete AI image and video model training environment** with the Ostris AI Toolkit running in a Docker container. The toolkit provides an intuitive web interface for training custom FLUX, Stable Diffusion, and video models like Wan 2.x with your own datasets.

**Think:** *"Professional AI training made simple - train custom FLUX, Stable Diffusion, and video models with your own data!"*

---

## What can I do with this?

### **Custom Model Training**
- **Train FLUX LoRAs** for the latest generation of image models
- **Train Stable Diffusion LoRAs** using your own images and concepts
- **Train video models** like Wan 2.x for custom video generation
- **Create character LoRAs** from portrait datasets
- **Train style LoRAs** from artistic collections
- **Build concept models** for specific objects or scenes
- **Experiment with different training techniques** and hyperparameters

### **Dataset Management**
- **Upload and organize training datasets** through the web interface
- **Automatic image preprocessing** and validation
- **Caption generation and editing** for training images
- **Dataset augmentation** tools for better training results
- **Support for various image formats** and resolutions
- **Batch processing capabilities** for large datasets

### **Training Features**
- **GPU-accelerated training** with automatic optimization
- **Real-time training progress** monitoring and visualization
- **Flexible training parameters** for different use cases
- **Resume interrupted training** sessions automatically
- **Model comparison tools** to evaluate training results
- **Export trained models** in multiple formats

---

## Who is this for?

This is **perfect** if you:
- Want to train custom FLUX or Stable Diffusion models without complex setup
- Need to train LoRAs for specific characters, styles, or concepts
- Want to train video generation models like Wan 2.x
- Are an artist or designer wanting personalized AI image/video models
- Want to experiment with different training techniques and parameters
- Need a simple interface for managing training datasets
- Are building custom AI image or video generation solutions
- Want to fine-tune models for commercial or personal projects

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory for your datasets and models
- **`TRAINING_CONFIG`**: Default training configuration settings

> **Template Customization:** Want to modify this setup? Click **edit**, make your changes, and save as your own template. Find it later in **"My Templates"**. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Ostris%20AI%20Toolkit)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
The Ostris AI Toolkit will install automatically with all required dependencies *(may take 5-10 minutes for initial setup)*

### **Step 4: Access Your Training Environment**
**Easy access:** Click the **"Open"** button for instant access to the Ostris AI Toolkit interface!

**Direct access via mapped ports:**
- **Ostris AI Toolkit:** `http://your-instance-ip:18675` (main training interface)

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in your Vast.ai account settings. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings.

### **Step 5: Start Training**
Upload your training images, configure your training parameters, and start creating custom models!

---

## Key Features

### **Dataset Management Tools**
- **Batch image upload** with drag-and-drop interface
- **Automatic image validation** and format conversion
- **Caption editing** with bulk operations
- **Dataset preview** and organization tools

### **Authentication & Access**
| Method | Use Case | Access Point |
|--------|----------|--------------|
| **Web Interface** | All training operations | Click "Open" or port 18675 |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | Forward local port to internal port 8675 |

### **Finding Your Credentials**
Access your authentication details:
```bash
# SSH into your instance
echo $OPEN_BUTTON_TOKEN          # For web access
```

*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| AI Toolkit UI | 18675 | 8675 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- **Training job monitoring** with real-time status updates
- **Resource usage tracking** for GPU and memory
- **Log aggregation** for debugging training issues
- **One-click service restarts** when needed

### **Advanced Features**

#### **Dynamic Provisioning**
Set `PROVISIONING_SCRIPT` environment variable to auto-install custom software from any public URL (GitHub, Gist, etc.)

#### **Multiple Access Methods**
| Method | Best For | Access Point |
|--------|----------|--------------|
| **Web Interface** | Training and dataset management | Port 18675 |
| **Jupyter Notebook** | Custom training scripts | `/jupyter` endpoint |
| **SSH Terminal** | File management and debugging | SSH connection |
| **SSH Tunnel** | Auth-free local access | Forward to port 8675 |

#### **Service Management**
```bash
# Check service status
supervisorctl status

# Restart training service
supervisorctl restart ai-toolkit

# View service logs
supervisorctl tail -f ai-toolkit
```

---

### **Training Configuration**
```bash
# Common training parameters
Training Steps: 1000-5000 (adjust based on dataset size)
Learning Rate: 1e-4 to 1e-5 (lower for fine-tuning)
Batch Size: 1-4 (based on GPU memory)
Resolution: 512x512 or 768x768 (for SD 1.5/2.1)

# Advanced settings
Gradient Accumulation: 1-4 steps
Mixed Precision: Enabled (saves memory)
Optimizer: AdamW or Lion
```

### **Environment Variables Reference**
| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Training workspace directory |
| `PROVISIONING_SCRIPT` | (none) | Auto-setup script URL |
| `APT_PACKAGES` | (none) | Space-separated apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated Python packages to install on first boot |
| `ENABLE_HTTPS` | `false` | Enable HTTPS connections (set in Vast.ai account settings) |

### **Dataset Preparation Tips**
For best training results:
- **Image Quality:** Use high-resolution, clear images (512px minimum)
- **Dataset Size:** 20-100 images for LoRA, 5-20 for Dreambooth
- **Consistency:** Similar lighting and composition when possible
- **Captions:** Accurate, descriptive text for each image
- **Variety:** Different angles and poses for character training

---

## Need More Help?

### **Documentation & Resources**
- **Ostris AI Toolkit Documentation:** [Official Documentation](https://github.com/ostris/ai-toolkit)
- **Stable Diffusion Training Guide:** Community tutorials and best practices
- **Base Image Features:** [GitHub Repository](https://github.com/vast-ai/base-image/)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)

### **Community & Support**
- **Ostris GitHub:** [Issues & Discussions](https://github.com/ostris/ai-toolkit)
- **Vast.ai Support:** Use the messaging icon in the console

### **Getting Started Resources**
- **Training Tutorials:** Step-by-step guides for different model types
- **Dataset Examples:** Sample datasets for learning
- **Community Models:** Shared training configurations and results

---

updated 20260107
