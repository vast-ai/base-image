#!/bin/bash 
set -eou pipefail

apt-get install -y libaio-dev

. /venv/main/bin/activate

cd /workspace

git clone https://github.com/hpcaitech/Open-Sora

sed -i '/^torch/d; /^torchvision/d' /workspace/Open-Sora/requirements.txt

pip install -vv /workspace/Open-Sora/

pip install torch=="${PYTORCH_VERSION}" xformers --index-url "${PYTORCH_INDEX_URL}"
pip install flash-attn --no-build-isolation

cd /tmp
git clone https://github.com/hpcaitech/TensorNVMe.git && cd TensorNVMe
pip install -r requirements.txt
pip install -v --no-cache-dir .

cd /workspace/Open-Sora

huggingface-cli download hpcai-tech/Open-Sora-v2 --local-dir ./ckpts

## Make a gradio app

cat > app.py << 'EOL'
import gradio as gr
import os
import subprocess
import tempfile
import shutil
import time
import csv
from PIL import Image
import uuid

# Configuration paths
OUTPUT_DIR = "output_videos"
TEMP_DIR = "temp_files"
CSV_FILENAME = "generation_inputs.csv"

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

def create_csv_file(prompts, image_paths=None):
    """Create a CSV file with prompts and optional image paths"""
    csv_path = os.path.join(TEMP_DIR, CSV_FILENAME)
    
    if image_paths:
        # For image-to-video
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            # Based on the error, the script is expecting a 'text' field
            writer.writerow(['text', 'image_path'])
            for prompt, img_path in zip(prompts, image_paths):
                if img_path and prompt:
                    writer.writerow([prompt, img_path])
    else:
        # For text-to-video
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['text'])
            for prompt in prompts:
                if prompt:
                    writer.writerow([prompt])
    
    return csv_path

def save_uploaded_image(image):
    """Save uploaded image to a temporary location and return the path"""
    if image is None:
        return None
        
    img_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.png")
    image.save(img_path)
    return img_path

def generate_video(
    generation_type,
    prompt,
    resolution,
    aspect_ratio,
    num_frames,
    num_gpus,
    offload_memory,
    uploaded_image=None,
    progress=gr.Progress()
):
    """Generate video based on parameters"""
    progress(0, desc="Preparing...")
    
    # We'll use 'samples' as the save directory since that's what appears in your logs
    sample_dir = "samples"
    os.makedirs(sample_dir, exist_ok=True)
    
    # Process inputs
    prompt = prompt.strip()
    if not prompt and generation_type != "image_to_video":
        return None, "Error: Prompt is required for text-to-video generation."
    
    # Handle image upload for image-to-video
    img_path = None
    if generation_type == "image_to_video":
        if uploaded_image is None:
            return None, "Error: An image is required for image-to-video generation."
        img_path = save_uploaded_image(uploaded_image)
    
    # Build the command
    base_cmd = ["torchrun"]
    
    # Set number of GPUs
    if num_gpus > 1:
        base_cmd.extend(["--nproc_per_node", str(num_gpus)])
    else:
        base_cmd.extend(["--nproc_per_node", "1"])
    
    base_cmd.append("--standalone")
    base_cmd.append("scripts/diffusion/inference.py")
    
    # Set config based on resolution and generation type
    if generation_type == "text_to_image_to_video":
        if resolution == "256x256":
            config_path = "configs/diffusion/inference/t2i2v_256px.py"
        else:  # 768x768
            config_path = "configs/diffusion/inference/t2i2v_768px.py"
    else:
        if resolution == "256x256":
            config_path = "configs/diffusion/inference/256px.py"
        else:  # 768x768
            config_path = "configs/diffusion/inference/768px.py"
    
    base_cmd.append(config_path)
    base_cmd.extend(["--save-dir", sample_dir])
    
    # Add image-to-video specific parameters
    if generation_type == "image_to_video":
        base_cmd.extend(["--cond_type", "i2v_head"])
        
        # Print the command being built for debugging
        print(f"Building image-to-video command with prompt: {prompt}")
        
        # Use direct reference with --ref instead of CSV
        if img_path:
            # Print image path for debugging
            print(f"Using image at path: {img_path}")
            
            # Add the prompt and reference image directly to the command
            base_cmd.extend(["--prompt", prompt])
            base_cmd.extend(["--ref", img_path])
            
            print(f"Command: {' '.join(base_cmd)}")
        else:
            print("Warning: No image path available for image-to-video generation")
    else:
        # For text-to-video or text-to-image-to-video
        base_cmd.extend(["--prompt", prompt])
    
    # Add aspect ratio if specified
    if aspect_ratio != "Default":
        base_cmd.extend(["--aspect_ratio", aspect_ratio])
    
    # Add number of frames if specified
    if num_frames > 0:
        base_cmd.extend(["--num_frames", str(num_frames)])
    
    # Add offload parameter if enabled
    if offload_memory:
        base_cmd.extend(["--offload", "True"])
    
    # Execute the command
    progress(0.1, desc="Starting video generation...")
    
    try:
        # Capture and log the full command for debugging
        cmd_str = ' '.join(base_cmd)
        print(f"Executing command: {cmd_str}")
        
        process = subprocess.Popen(
            base_cmd, 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        # Monitor the process
        for i in range(10, 90, 10):
            time.sleep(2)  # Simulation of progress
            progress(i/100, desc=f"Generating video... {i}%")
            
            # Check if process has ended
            if process.poll() is not None:
                break
        
        # Wait for completion
        stdout, stderr = process.communicate()
        progress(0.9, desc="Processing output...")
        
        if process.returncode != 0:
            print(f"Error output: {stderr}")
            return None, f"Error during generation: {stderr}"
        
        # Based on the log output, videos are saved to 'samples/video_256px' or 'samples/video_768px'
        video_dir = None
        
        # Check for generated video in expected directories based on resolution
        if resolution == "256x256":
            video_dir = os.path.join(sample_dir, "video_256px")
        else:
            video_dir = os.path.join(sample_dir, "video_768px")
        
        # If the directory doesn't exist, search for it
        if not os.path.exists(video_dir):
            # Look for any directory under samples that might contain videos
            for root, dirs, files in os.walk(sample_dir):
                for directory in dirs:
                    if "video" in directory.lower():
                        video_dir = os.path.join(root, directory)
                        break
                if video_dir and os.path.exists(video_dir):
                    break
        
        # If still not found, look for any mp4 file recursively
        if not video_dir or not os.path.exists(video_dir):
            video_files = []
            for root, dirs, files in os.walk(sample_dir):
                for file in files:
                    if file.endswith('.mp4'):
                        video_files.append(os.path.join(root, file))
            
            if video_files:
                # Return the most recently created video file
                video_files.sort(key=os.path.getmtime, reverse=True)
                return video_files[0], f"Video generated successfully at {video_files[0]}!"
            else:
                # Scan stdout for any mentions of saved files
                save_indicators = ["Saved to", "saved at", "output:", "mp4"]
                for line in stdout.split('\n'):
                    for indicator in save_indicators:
                        if indicator in line and ".mp4" in line:
                            # Extract path that might contain the MP4 file
                            potential_path = line.split(indicator)[-1].strip()
                            if os.path.exists(potential_path) and potential_path.endswith('.mp4'):
                                return potential_path, f"Video found at {potential_path}!"
                
                # If we still haven't found it, check if stderr contains path information
                for line in stderr.split('\n'):
                    for indicator in save_indicators:
                        if indicator in line and ".mp4" in line:
                            potential_path = line.split(indicator)[-1].strip()
                            if os.path.exists(potential_path) and potential_path.endswith('.mp4'):
                                return potential_path, f"Video found at {potential_path}!"
                
                # If all else fails, print debug info
                print(f"Command output: {stdout}")
                print(f"Error output: {stderr}")
                return None, "Generation completed but could not locate the video file. Check the 'samples' directory manually."
        
        # Look for video files in the identified directory
        if os.path.exists(video_dir):
            video_files = [f for f in os.listdir(video_dir) if f.endswith('.mp4')]
            if video_files:
                # Get the most recent video file
                video_files.sort(key=lambda x: os.path.getmtime(os.path.join(video_dir, x)), reverse=True)
                video_path = os.path.join(video_dir, video_files[0])
                progress(1.0, desc="Done!")
                return video_path, f"Video generated successfully at {video_path}!"
        
        # Final fallback - look in the entire samples directory recursively
        all_video_files = []
        for root, dirs, files in os.walk(sample_dir):
            for file in files:
                if file.endswith('.mp4'):
                    all_video_files.append(os.path.join(root, file))
        
        if all_video_files:
            # Sort by modification time to get the most recent
            all_video_files.sort(key=os.path.getmtime, reverse=True)
            return all_video_files[0], f"Video found at {all_video_files[0]}!"
        
        return None, "Generation completed but no video file was found. Check the samples directory manually."
        
    except Exception as e:
        import traceback
        print(f"Exception: {str(e)}")
        print(traceback.format_exc())
        return None, f"Error executing command: {str(e)}"

with gr.Blocks(title="Video Generation App") as app:
    gr.Markdown("# AI Video Generation")
    gr.Markdown("Generate videos from text or images using AI models")
    
    with gr.Tab("Text-to-Video"):
        with gr.Row():
            with gr.Column(scale=2):
                t2v_prompt = gr.Textbox(
                    placeholder="Enter a detailed description of the video you want to generate...",
                    label="Prompt",
                    lines=5
                )
            
            with gr.Column(scale=1):
                t2v_generation_type = gr.Radio(
                    ["text_to_image_to_video", "direct_text_to_video"],
                    label="Generation Method",
                    value="text_to_image_to_video",
                    info="t2i2v is recommended for higher quality"
                )
                
                t2v_resolution = gr.Radio(
                    ["256x256", "768x768"], 
                    label="Resolution", 
                    value="256x256",
                    info="Higher resolution requires more GPU memory"
                )
                
                t2v_aspect_ratio = gr.Dropdown(
                    ["Default", "16:9", "9:16", "1:1", "2.39:1"],
                    label="Aspect Ratio",
                    value="Default"
                )
                
                t2v_num_frames = gr.Slider(
                    minimum=5, 
                    maximum=129, 
                    step=4, 
                    value=17,
                    label="Number of Frames",
                    info="Must be 4k+1 and less than 129"
                )
                
                t2v_num_gpus = gr.Slider(
                    minimum=1,
                    maximum=8,
                    step=1,
                    value=1,
                    label="Number of GPUs",
                    info="For 768px, multiple GPUs are recommended"
                )
                
                t2v_offload = gr.Checkbox(
                    label="Offload to Save Memory", 
                    value=False,
                    info="Reduces memory usage but may be slower"
                )
        
        t2v_generate_btn = gr.Button("Generate Video", variant="primary")
        
        with gr.Row():
            t2v_output_video = gr.Video(label="Generated Video")
            t2v_output_message = gr.Textbox(label="Status", interactive=False)
    
    with gr.Tab("Image-to-Video"):
        with gr.Row():
            with gr.Column(scale=1):
                i2v_image = gr.Image(
                    label="Reference Image",
                    type="pil",
                    image_mode="RGB"
                )
            
            with gr.Column(scale=2):
                i2v_prompt = gr.Textbox(
                    placeholder="Describe how the image should be animated...",
                    label="Prompt",
                    lines=5
                )
                
                i2v_resolution = gr.Radio(
                    ["256x256", "768x768"], 
                    label="Resolution", 
                    value="256x256",
                    info="Higher resolution requires more GPU memory"
                )
                
                i2v_aspect_ratio = gr.Dropdown(
                    ["Default", "16:9", "9:16", "1:1", "2.39:1"],
                    label="Aspect Ratio",
                    value="Default"
                )
                
                i2v_num_frames = gr.Slider(
                    minimum=5, 
                    maximum=129, 
                    step=4, 
                    value=17,
                    label="Number of Frames",
                    info="Must be 4k+1 and less than 129"
                )
                
                i2v_num_gpus = gr.Slider(
                    minimum=1,
                    maximum=8,
                    step=1,
                    value=1,
                    label="Number of GPUs",
                    info="For 768px, multiple GPUs are recommended"
                )
                
                i2v_offload = gr.Checkbox(
                    label="Offload to Save Memory", 
                    value=False,
                    info="Reduces memory usage but may be slower"
                )
        
        i2v_generate_btn = gr.Button("Generate Video", variant="primary")
        
        with gr.Row():
            i2v_output_video = gr.Video(label="Generated Video")
            i2v_output_message = gr.Textbox(label="Status", interactive=False)
    
    # Set up event handlers
    t2v_generate_btn.click(
        generate_video,
        inputs=[
            t2v_generation_type,
            t2v_prompt,
            t2v_resolution,
            t2v_aspect_ratio,
            t2v_num_frames,
            t2v_num_gpus,
            t2v_offload
        ],
        outputs=[t2v_output_video, t2v_output_message]
    )
    
    i2v_generate_btn.click(
        generate_video,
        inputs=[
            gr.Textbox(value="image_to_video", visible=False),  # Fixed value for generation_type
            i2v_prompt,
            i2v_resolution,
            i2v_aspect_ratio,
            i2v_num_frames,
            i2v_num_gpus,
            i2v_offload,
            i2v_image
        ],
        outputs=[i2v_output_video, i2v_output_message]
    )

# Launch the app
if __name__ == "__main__":
    try:
        # Try to bind specifically to 127.0.0.1
        app.launch(share=False, server_name="127.0.0.1", server_port=17860)
    except OSError as e:
        print(f"Error binding to 127.0.0.1:17860: {e}")
        print("Trying alternate port...")
        # Try with a different port if 17860 is unavailable
        try:
            app.launch(share=False, server_name="127.0.0.1", server_port=7860)
        except OSError:
            # As a last resort, let Gradio choose an available port
            print("Using default Gradio port on 127.0.0.1")
            app.launch(share=False, server_name="127.0.0.1")
EOL

pip install spaces


cat > /opt/supervisor-scripts/open-sora.sh << 'EOL'
#!/bin/bash

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for Open Sora in the portal config
search_term="Open Sora"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 10
done

cd ${DATA_DIRECTORY}/Open-Sora
python app.py | tee -a "/var/log/portal/${PROC_NAME}.log"

EOL

chmod +x /opt/supervisor-scripts/open-sora.sh

cat > /etc/supervisor/conf.d/open-sora.conf << 'EOL'
[program:open-sora]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/open-sora.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
# This is necessary for Vast logging to work alongside the Portal logs (Must output to /dev/stdout)
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
EOL

# Update supervisor to start the new service
supervisorctl reread
supervisorctl update
