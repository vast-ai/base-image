from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
from collections import deque
import yaml
import json
import httpx
import asyncio
import aiofiles
import os
import io
import zipfile
from datetime import datetime
import logging
import time
import ipaddress
import subprocess
import GPUtil
import psutil
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("log_monitor")

tunnel_manager=os.environ.get("TUNNEL_MANAGER", "http://localhost:11112")

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def get_scheme():
    if os.environ.get("ENABLE_HTTPS", "false").lower() != "true":
        scheme = "http"
    else:
        scheme = "https"
    return scheme

def load_config():
    yaml_path = '/etc/portal.yaml'
    
    # Wait until the file exists - caddy-manager handles config writing
    while not os.path.exists(yaml_path):
        print(f"Waiting for {yaml_path} to appear...")
        time.sleep(1)

    with open(yaml_path, 'r') as file:
        config_applications = yaml.safe_load(file)['applications']
        return hydrate_applications(config_applications)

def hydrate_applications(applications):
    for app_name, app in applications.items():
        hostname = app["hostname"]
        external_port = app["external_port"]
        internal_port = app["internal_port"]
        if external_port == internal_port and internal_port == 8080:
            scheme = "https"
        else:
            scheme = get_scheme()
        applications[app_name]["target_url"] = f'{scheme}://{hostname}:{external_port}'
        applications[app_name]["mapped_port"] = os.environ.get(f'VAST_TCP_PORT_{external_port}', "")
    return applications

def strip_port(host):
    return host.split(':')[0]

def get_instance_properties():
    return {
        "id": os.environ.get("CONTAINER_ID",""),
        "gpu": get_gpu_info(),
        "direct_https": "true" if os.environ.get("ENABLE_HTTPS", "false").lower() == "true" else "false"
    }

def get_gpu_info():
    """Get formatted GPU information for both NVIDIA and AMD GPUs"""
    gpu_models = {}
    
    # Try to get NVIDIA GPUs
    try:
        nvidia_gpus = GPUtil.getGPUs()
        for gpu in nvidia_gpus:
            if gpu.name in gpu_models:
                gpu_models[gpu.name] += 1
            else:
                gpu_models[gpu.name] = 1
    except Exception:
        pass
    
    # Try to get AMD GPUs
    try:
        rocm_gpus = get_rocm_gpus()
        for gpu in rocm_gpus:
            if gpu.name in gpu_models:
                gpu_models[gpu.name] += 1
            else:
                gpu_models[gpu.name] = 1
    except Exception:
        pass
    
    # Check if any GPUs are available
    if not gpu_models:
        return "No GPU detected"
    
    # Format the output
    result = []
    for name, count in gpu_models.items():
        if count > 1:
            result.append(f"{count}× {name}")
        else:
            result.append(name)
    
    return ", ".join(result)

def get_rocm_gpus():
    """Get AMD GPU information using rocm-smi command line tool"""
    try:
        # Check if rocm-smi is available
        rocm_available = subprocess.run(
            ['which', 'rocm-smi'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE
        ).returncode == 0
        
        if not rocm_available:
            return []
        
        # First get GPU info with memory and usage
        result = subprocess.run(
            ['rocm-smi', '--showmeminfo', 'vram', '--showuse', '--json'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
        )
        
        if result.returncode != 0:
            return []
        
        # Parse JSON output for memory and usage
        rocm_data = json.loads(result.stdout)
        
        # Get additional GPU info including name
        name_result = subprocess.run(
            ['rocm-smi', '--showproductname', '--json'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
        )
        
        # Parse GPU name data if available
        gpu_names = {}
        if name_result.returncode == 0:
            try:
                name_data = json.loads(name_result.stdout)
                for card_id, card_data in name_data.items():
                    if isinstance(card_data, dict):
                        # Try to construct a meaningful name from available fields
                        vendor = card_data.get('Card Vendor', '').split('[')[-1].split(']')[0] if '[' in card_data.get('Card Vendor', '') else card_data.get('Card Vendor', '')
                        sku = card_data.get('Card SKU', '')
                        gfx = card_data.get('GFX Version', '')
                        
                        if vendor and sku:
                            gpu_names[card_id] = f"{vendor} {sku} ({gfx})"
                        elif vendor:
                            gpu_names[card_id] = f"{vendor} GPU"
                        else:
                            gpu_names[card_id] = "AMD GPU"
            except:
                pass
        
        gpus = []
        
        for card_id, card_data in rocm_data.items():
            if not isinstance(card_data, dict):
                continue
                
            # Create a GPU object similar to GPUtil's structure
            gpu = type('', (), {})()
            gpu.id = int(card_id.replace('card', ''))
            
            # Set GPU name from the name data if available, otherwise use a default
            gpu.name = gpu_names.get(card_id, 'AMD GPU')
            
            # Extract memory info based on the actual output format
            gpu.memoryTotal = int(card_data.get('VRAM Total Memory (B)', 0))
            gpu.memoryTotal_mb = round(gpu.memoryTotal / (1024 * 1024), 2)  # Convert to MB
            
            gpu.memoryUsed = int(card_data.get('VRAM Total Used Memory (B)', 0))
            gpu.memoryUsed_mb = round(gpu.memoryUsed / (1024 * 1024), 2)  # Convert to MB
            
            # Extract GPU utilization
            gpu_busy = card_data.get('GPU use (%)', 0)
            try:
                gpu.load = float(gpu_busy) / 100.0 if isinstance(gpu_busy, (int, float, str)) else 0
            except (ValueError, TypeError):
                gpu.load = 0
                
            gpus.append(gpu)
            
        return gpus
    except Exception as e:
        print(f"Error getting ROCm GPU info: {str(e)}")
        return []
    
def is_in_container():
    """Check if we're running inside a container"""
    return os.path.exists('/sys/fs/cgroup/memory/memory.limit_in_bytes') or os.path.exists('/sys/fs/cgroup/memory.max')

def get_container_memory_limit():
    """Get memory limit allocated to the container"""
    try:
        # cgroups v1
        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
            limit = int(f.read().strip())
            # Very large values typically indicate no limit
            return limit if limit < 10**15 else None
    except:
        try:
            # cgroups v2
            with open('/sys/fs/cgroup/memory.max', 'r') as f:
                value = f.read().strip()
                if value == 'max':
                    return None
                return int(value)
        except:
            return None

def get_container_memory_usage():
    """Get current memory usage of the container"""
    try:
        # cgroups v1
        with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
            return int(f.read().strip())
    except:
        try:
            # cgroups v2
            with open('/sys/fs/cgroup/memory.current', 'r') as f:
                return int(f.read().strip())
        except:
            return None

def get_container_memory_stats():
    """Get detailed memory stats for container including total, used and percentage"""
    if not is_in_container():
        # Not in a container, return None to fall back to psutil
        return None
        
    limit = get_container_memory_limit()
    usage = get_container_memory_usage()
    
    if limit is None or usage is None:
        return None
        
    # Calculate percentage
    percent = (usage / limit) * 100
    
    return {
        'total': limit,
        'used': usage,
        'percent': percent
    }


templates.env.filters["strip_port"] = strip_port

tunnels = {}
tunnel_api_timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, token: Optional[str] = None):
    if token is not None:
        return RedirectResponse(url="/", status_code=302)

    await set_external_ip(request.headers.get("X-Forwarded-Host"))
    return templates.TemplateResponse("index.html", {
        "request": request,
        "instance": get_instance_properties(),
        })

@app.get("/get-applications")
async def get_applications(request: Request):
    applications = load_config()
    auth_token = request.cookies.get(f"" + os.environ.get('VAST_CONTAINERLABEL') + "_auth_token")
    for app_name, app in applications.items():
        separator = '&' if '?' in app["open_path"] else '?'
        app["open_path"] += f"{separator}token={auth_token}"

    return JSONResponse(applications)

@app.get("/get-direct-url/{port}")
async def get_direct_url(port: int):
    url = f"{tunnel_manager}/get-direct-url/{port}"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            result = response.json()
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Direct URL unavailable")
        except httpx.HTTPError as e:
            # No stack trace in the live application
            raise HTTPException(status_code=500, detail=f"Error communicating with the API")
        except:
            raise HTTPException(status_code=500, detail=f"Unhandled error response from API")

@app.get("/get-existing-quick-tunnel/{target_url:path}")
async def get_existing_quick_tunnel(target_url: str):
    url = f"{tunnel_manager}/get-quick-tunnel-if-exists/{target_url}"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            result = response.json()
            return HTMLResponse(result['tunnel_url'])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Tunnel not found")
        except httpx.HTTPError as e:
            # No stack trace in the live application
            raise HTTPException(status_code=500, detail=f"Error communicating with the API")
        except:
            raise HTTPException(status_code=500, detail=f"Unhandled error response from API")
        
@app.get("/get-existing-named-tunnel/{port}")
async def get_existing_named_tunnel(port: int):
    url = f"{tunnel_manager}/get-named-tunnel/{port}"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            result = response.json()
            return HTMLResponse(result['tunnel_url'])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Tunnel not found")
        except httpx.HTTPError as e:
            # No stack trace in the live application
            raise HTTPException(status_code=500, detail=f"Error communicating with the API")
        except:
            raise HTTPException(status_code=500, detail=f"Unhandled error response from API")
        
@app.get("/get-all-quick-tunnels")
async def get_all_quick_tunnels():
    url = f"{tunnel_manager}/get-all-quick-tunnels"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            result = response.json()
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Tunnel not found")
        except httpx.HTTPError as e:
            # No stack trace in the live application
            raise HTTPException(status_code=500, detail=f"Error communicating with the API")
        except:
            raise HTTPException(status_code=500, detail=f"Unhandled error response from API")
        

@app.get("/get-named-tunnels")
async def get_named_tunnels():
    url = f"{tunnel_manager}/get-named-tunnels"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            result = response.json()
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code in [404, 500]:
                raise HTTPException(status_code=404, detail=f"Tunnel config not found")
        except httpx.HTTPError as e:
            # No stack trace in the live application
            raise HTTPException(status_code=500, detail=f"Error communicating with the API")
        except:
            raise HTTPException(status_code=500, detail=f"Unhandled error response from API")


@app.post("/start-quick-tunnel/{target_url:path}")
async def start_quick_tunnel(target_url: str):  
    url = f"{tunnel_manager}/get-quick-tunnel/{target_url}"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            result = response.json()
            return result
        except httpx.HTTPError as e:
            # No stack trace in the live application
            raise HTTPException(status_code=500, detail=f"Error communicating with the API")
        except:
            raise HTTPException(status_code=500, detail=f"Unhandled error response from API")

@app.post("/stop-quick-tunnel/{target_url:path}")
async def stop_quick_tunnel(target_url: str):
    url = f"{tunnel_manager}/stop-quick-tunnel/{target_url}"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.post(url)
            response.raise_for_status()
            result = response.json()
            return result
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Error communicating with the API: {str(e)}")

@app.post("/refresh-quick-tunnel/{target_url:path}")
async def refresh_quick_tunnel(target_url: str):
    url = f"{tunnel_manager}/refresh-quick-tunnel/{target_url}"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        try:
            response = await client.post(url)
            response.raise_for_status()
            result = response.json()
            return result
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Error communicating with the API: {str(e)}")

async def set_external_ip(forwarded_host):
    try:
        ip, port = forwarded_host.split(":")
        ip_obj = ipaddress.IPv4Address(ip)
        if port != os.environ.get("VAST_TCP_PORT_1111"):
            return
        async with httpx.AsyncClient() as client:
            response = await client.put(f"{tunnel_manager}/set-public-ip/{ip}")
    except Exception:
        return

    return


## Log reader functions
# Constants
MAX_LINES = 500  # Maximum lines to keep in buffer
POLL_INTERVAL = 0.2  # File polling interval in seconds
LOG_DIRECTORY = "/var/log/portal"  # Default directory to monitor

# State for WebSocket and monitoring
websocket_clients = set()  # Set of connected WebSocket clients
client_tasks = {}  # Client ID to asyncio Task
chronological_log_buffer = deque(maxlen=MAX_LINES)  # Single buffer for all logs in chronological order
file_specific_buffers = {}  # Filename -> Deque (for debugging/specific file views if needed)
file_positions = {}  # Filename -> Last position
file_mtimes = {}    # Filename -> Last modification time
monitor_task = None  # Main monitoring task

# Helper function to format log lines
def highlight_log_level(line):
    if 'WARN' in line.upper():
        return f'<span class="warning">{line}</span>'
    elif 'ERR' in line.upper():
        return f'<span class="error">{line}</span>'
    return f'<span>{line}</span>'

# Dedicated task for each client to handle heartbeats and messages
async def client_handler(websocket: WebSocket, client_id: int):
    """Handle a single client's WebSocket connection"""
    try:
        # Send connection confirmation
        await websocket.send_text('<div class="log-system-message" style="color:green;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">Connected to log stream</div>')
        
        # Send historical logs from the single chronological buffer
        for line in chronological_log_buffer:
            html_content = highlight_log_level(line)
            await websocket.send_text(html_content)
        
        # Heartbeat loop
        while True:
            # Process any messages from client (including pings)
            try:
                # Very short timeout to avoid blocking the task
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                logger.debug(f"Received message from client {client_id}: {message[:50]}...")
                
                # If it's a ping, send a pong
                if message == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # No message received, that's expected
                pass
            
            # Send heartbeat every 10 seconds
            await asyncio.sleep(10)
            try:
                await websocket.send_text("heartbeat")
                logger.debug(f"Sent heartbeat to client {client_id}")
            except Exception as e:
                logger.error(f"Failed to send heartbeat to client {client_id}: {e}")
                # Connection is probably broken, exit the loop
                break
    
    except WebSocketDisconnect:
        logger.info(f"WebSocket client {client_id} disconnected normally")
    except asyncio.CancelledError:
        logger.info(f"Client handler for {client_id} was cancelled")
    except Exception as e:
        logger.error(f"Error in client handler for {client_id}: {e}", exc_info=True)
    finally:
        # Clean up client state
        remove_client(websocket, client_id)

# Remove a client
def remove_client(websocket, client_id):
    """Safely remove a client and cancel its task"""
    if websocket in websocket_clients:
        websocket_clients.remove(websocket)
    
    if client_id in client_tasks:
        client_tasks[client_id].cancel()
        del client_tasks[client_id]
    
    logger.info(f"Client {client_id} removed, remaining clients: {len(websocket_clients)}")

# Get all log files in the directory
async def get_log_files(directory):
    try:
        # Get all log files in the directory
        log_files = [f for f in os.listdir(directory) if f.endswith('.log')]
        
        # Get the full path for each file
        full_paths = [os.path.join(directory, f) for f in log_files]
        
        # Sort files by modification time (oldest first, newest last)
        sorted_paths = sorted(full_paths, key=lambda x: os.path.getmtime(x))
        
        # Extract just the filenames from the sorted paths
        sorted_files = [os.path.basename(path) for path in sorted_paths]
        
        return sorted_files
    except Exception as e:
        logger.error(f"Error listing directory {directory}: {e}")
        return []

# Send message to all connected clients
async def broadcast_message(message):
    """Send a formatted log message to all connected clients in parallel"""
    if not websocket_clients:
        return
    
    # Format the log message
    html_content = highlight_log_level(message)
    
    # Create tasks to send to all clients in parallel
    send_tasks = []
    for client in websocket_clients:
        task = asyncio.create_task(send_to_client(client, html_content))
        send_tasks.append(task)
    
    # Wait for all tasks to complete
    results = await asyncio.gather(*send_tasks, return_exceptions=True)
    
    # Remove any disconnected clients
    disconnected_clients = set()
    for client, result in zip(websocket_clients, results):
        if isinstance(result, Exception):
            logger.error(f"Error sending to client {id(client)}: {result}")
            disconnected_clients.add(client)
    
    for client in disconnected_clients:
        websocket_clients.remove(client)

async def send_to_client(client, content):
    """Helper function to send content to a single client"""
    await client.send_text(content)
    return True

# Tail a single log file
async def tail_log_file(filepath):
    """Monitor a log file for changes and broadcast new content"""
    filename = os.path.basename(filepath)
    
    try:
        # Get file stats
        if not os.path.exists(filepath):
            return

        # Get current file info
        current_size = os.path.getsize(filepath)
        current_mtime = os.path.getmtime(filepath)
        last_position = file_positions.get(filename, None)
        last_mtime = file_mtimes.get(filename, 0)
        
        # First time seeing this file
        if last_position is None:
            logger.info(f"New file: {filename}, size={current_size}")
            file_specific_buffers[filename] = deque(maxlen=MAX_LINES)  # For file-specific tracking
            
            # For new files, read the last MAX_LINES lines
            async with aiofiles.open(filepath, 'r') as file:
                lines = await file.readlines()
                if len(lines) > MAX_LINES:
                    lines = lines[-MAX_LINES:]
                
                for line in lines:
                    line = line.strip()
                    if line:
                        # Add to file-specific buffer
                        file_specific_buffers[filename].append(line)
                        # Add to chronological buffer
                        chronological_log_buffer.append(line)
                        # Broadcast to connected clients
                        await broadcast_message(line)
                
                # Set the position to end of file
                file_positions[filename] = current_size
                file_mtimes[filename] = current_mtime
            
        # File has been modified since last check
        elif current_mtime > last_mtime or current_size != last_position:
            logger.debug(f"File {filename} changed: size={current_size}, last_pos={last_position}")
            
            # If file was truncated or rotated (smaller than before)
            if current_size < last_position:
                logger.info(f"File {filename} was truncated")
                last_position = 0
            
            # Open and seek to last position
            async with aiofiles.open(filepath, 'r') as file:
                if last_position > 0:
                    await file.seek(last_position)
                
                # Read all new lines
                new_lines = []
                async for line in file:
                    line = line.strip()
                    if line:
                        new_lines.append(line)
                        # Add to both buffers
                        file_specific_buffers[filename].append(line)
                        chronological_log_buffer.append(line)
                        await broadcast_message(line)
                
                # Update position and mtime
                file_positions[filename] = await file.tell()
                file_mtimes[filename] = current_mtime

    except Exception as e:
        logger.error(f"Error tailing {filepath}: {e}")

# Main monitoring loop
async def monitor_log_directory(directory):
    """Main task to monitor log directory and tail files"""
    logger.info(f"Starting log monitoring in {directory}")
    
    while True:
        try:
            # Get current log files
            log_files = await get_log_files(directory)
            
            # Monitor each file
            for log_file in log_files:
                filepath = os.path.join(directory, log_file)
                await tail_log_file(filepath)
                
            # Clean up deleted files
            for filename in list(file_positions.keys()):
                if filename not in log_files:
                    logger.info(f"File {filename} was removed, cleaning up")
                    file_positions.pop(filename, None)
                    file_mtimes.pop(filename, None)
            
            # Small delay before next poll
            await asyncio.sleep(POLL_INTERVAL)
            
        except asyncio.CancelledError:
            logger.info("Monitor task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in monitor_log_directory: {e}")
            await asyncio.sleep(1)

# WebSocket connection handler
async def websocket_logs(websocket: WebSocket):
    """Handle a new WebSocket connection"""
    await websocket.accept()
    
    # Generate client ID and add to clients list
    client_id = id(websocket)
    websocket_clients.add(websocket)
    logger.info(f"WebSocket client {client_id} connected, total clients: {len(websocket_clients)}")
    
    # Create a dedicated task for this client
    client_task = asyncio.create_task(client_handler(websocket, client_id))
    client_tasks[client_id] = client_task
    
    try:
        # Wait for the client handler to complete
        await client_task
    except asyncio.CancelledError:
        # Expected when client disconnects, no need to log as error
        logger.debug(f"WebSocket client {client_id} disconnected")
    except Exception as e:
        logger.error(f"Error in main websocket handler for client {client_id}: {e}")
    finally:
        # Ensure client is removed
        remove_client(websocket, client_id)

# Cleanup function to cancel all tasks
async def cleanup_tasks():
    """Clean up all tasks on shutdown"""
    global monitor_task
    
    # Cancel monitor task
    if monitor_task:
        logger.info("Cancelling monitor task")
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
    
    # Cancel all client tasks
    for client_id, task in list(client_tasks.items()):
        logger.info(f"Cancelling client task {client_id}")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    # Clear collections
    websocket_clients.clear()
    client_tasks.clear()
    logger.info("All tasks cleaned up")

@app.websocket("/ws-logs")
async def logs_websocket(websocket: WebSocket):
    await websocket_logs(websocket)

@app.get("/download-logs")
async def download_logs(filename: str = None):
    """
    Zip the /var/log directory and serve it as a downloadable file.
    
    Parameters:
    - filename: Optional custom filename for the zip file
    """
    try:
        # Create a memory buffer to store the zip file
        zip_buffer = io.BytesIO()
        
        # Define the directory to zip
        log_dir = "/var/log"
        
        # Check if the directory exists
        if not os.path.exists(log_dir):
            raise HTTPException(status_code=404, detail="Log directory not found")
        
        # Use provided filename or generate one with timestamp
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_filename = f"logs_{timestamp}.zip"
        else:
            # Ensure the filename ends with .zip
            zip_filename = filename if filename.endswith('.zip') else f"{filename}.zip"
        
        # Create a zip file in the memory buffer
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Walk through the directory and add all files to the zip
            for root, dirs, files in os.walk(log_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Calculate the relative path for the file inside the zip
                    relative_path = os.path.relpath(file_path, os.path.dirname(log_dir))
                    try:
                        zipf.write(file_path, relative_path)
                    except (PermissionError, zipfile.LargeZipFile, OSError) as e:
                        # Skip files that can't be accessed or are too large
                        continue
        
        # Reset the buffer position to the beginning
        zip_buffer.seek(0)
        
        # Return the zip file as a downloadable response
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating zip file: {str(e)}")

@app.get("/system-metrics")
async def get_system_metrics():
    # Try to get container memory metrics first
    container_memory = get_container_memory_stats()
    
    # Initialize metrics dictionary with memory info
    metrics = {
        'ram': container_memory if container_memory else {
            'total': psutil.virtual_memory().total,
            'used': psutil.virtual_memory().used,
            'percent': psutil.virtual_memory().percent
        },
        'disk': {
            'total': psutil.disk_usage('/').total,
            'used': psutil.disk_usage('/').used,
            'percent': psutil.disk_usage('/').percent
        }
    }
    
    # Add container detection info
    metrics['environment'] = 'container' if is_in_container() else 'host'
    
    # Get GPU metrics from both NVIDIA and AMD sources
    all_gpus = []
    nvidia_error = None
    rocm_error = None
    
    # Try to get NVIDIA GPUs
    try:
        nvidia_gpus = GPUtil.getGPUs()
        all_gpus.extend(nvidia_gpus)
    except Exception as e:
        nvidia_error = str(e)
    
    # Try to get ROCm GPUs
    try:
        rocm_gpus = get_rocm_gpus()
        all_gpus.extend(rocm_gpus)
    except Exception as e:
        rocm_error = str(e)
        
    # Calculate metrics if any GPUs are found
    if all_gpus:
        # Calculate average load across all GPUs
        avg_load = np.mean([gpu.load for gpu in all_gpus])
        
        # Sum total and used memory across all GPUs
        # Handle potential unit differences between NVIDIA and ROCm
        total_memory = 0
        used_memory = 0
        
        for gpu in all_gpus:
            # For ROCm GPUs, memoryTotal might be in bytes, while NVIDIA is in MB
            if hasattr(gpu, 'memoryTotal_mb'):
                # Use the MB values directly if available
                total_memory += gpu.memoryTotal_mb
                used_memory += gpu.memoryUsed_mb
            else:
                # Assume standard GPUtil format which is already in MB
                total_memory += gpu.memoryTotal
                used_memory += gpu.memoryUsed
        
        # Calculate overall memory usage percentage
        memory_percent = (used_memory / total_memory * 100) if total_memory > 0 else 0
        
        metrics['gpu'] = {
            'count': len(all_gpus),
            'avg_load_percent': float(avg_load * 100),  # Convert to percentage
            'memory_used': float(used_memory),
            'memory_total': float(total_memory),
            'memory_percent': float(memory_percent),
            'memory_unit': 'MB'  # Add unit for clarity
        }
        
        # Add GPU details by type
        nvidia_count = len([gpu for gpu in all_gpus if hasattr(gpu, 'id') and not isinstance(gpu.id, str)])
        rocm_count = len(all_gpus) - nvidia_count
        
        if nvidia_count > 0:
            metrics['gpu']['nvidia_count'] = nvidia_count
        if rocm_count > 0:
            metrics['gpu']['amd_count'] = rocm_count
            
    else:
        metrics['gpu'] = {
            'count': 0,
            'avg_load_percent': 0,
            'memory_used': 0,
            'memory_total': 0,
            'memory_percent': 0,
            'memory_unit': 'MB'  # Keep consistent unit notation
        }
        
        # Add error information if applicable
        errors = {}
        if nvidia_error:
            errors['nvidia'] = nvidia_error
        if rocm_error:
            errors['rocm'] = rocm_error
            
        if errors:
            metrics['gpu']['errors'] = errors

    return JSONResponse(content=metrics)

@app.on_event("startup")
async def startup_event():
    app.state.monitor_task = asyncio.create_task(
        monitor_log_directory("/var/log/portal")
    )

@app.on_event("shutdown") 
async def shutdown_event():
    if hasattr(app.state, 'monitor_task'):
        app.state.monitor_task.cancel()
