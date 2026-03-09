from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Union
from collections import deque
import yaml
import json
import httpx
import asyncio
import aiofiles
import os
import io
import re
import html as html_module
import zipfile
from datetime import datetime
import logging
import time
import ipaddress
import subprocess
import socket
import xmlrpc.client
import http.client
import GPUtil
import psutil

# ANSI color maps
_ANSI_FG_COLORS = {
    30: '#000', 31: '#c00', 32: '#0a0', 33: '#c50', 34: '#00c', 35: '#c0c', 36: '#0cc', 37: '#ccc',
    90: '#555', 91: '#f55', 92: '#5f5', 93: '#ff5', 94: '#55f', 95: '#f5f', 96: '#5ff', 97: '#fff',
}
_ANSI_BG_COLORS = {
    40: '#000', 41: '#c00', 42: '#0a0', 43: '#c50', 44: '#00c', 45: '#c0c', 46: '#0cc', 47: '#ccc',
    100: '#555', 101: '#f55', 102: '#5f5', 103: '#ff5', 104: '#55f', 105: '#f5f', 106: '#5ff', 107: '#fff',
}

# Regex to strip C0/C1 control characters except tab (\x09).
# \r and \n are already consumed by _process_chunk before text reaches here.
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b-\x0d\x0e-\x1a\x7f]')

def ansi_to_html(text: str) -> str:
    """Convert ANSI escape codes to HTML spans. HTML-escapes text content.
    Strips stray control characters (SI, SO, BEL, etc.) that are invisible
    in a real terminal but render as garbage in HTML."""
    # Split on ANSI sequences, escape text parts, convert codes to spans
    parts = re.split(r'(\x1b\[[0-9;]*m)', text)
    result = []
    open_spans = 0
    for part in parts:
        m = re.match(r'\x1b\[([0-9;]*)m', part)
        if m:
            codes = [int(c) for c in m.group(1).split(';') if c] if m.group(1) else [0]
            for code in codes:
                if code == 0:
                    result.append('</span>' * open_spans)
                    open_spans = 0
                elif code == 1:
                    result.append('<span style="font-weight:bold">')
                    open_spans += 1
                elif code == 2:
                    result.append('<span style="opacity:0.7">')
                    open_spans += 1
                elif code in _ANSI_FG_COLORS:
                    result.append(f'<span style="color:{_ANSI_FG_COLORS[code]}">')
                    open_spans += 1
                elif code in _ANSI_BG_COLORS:
                    result.append(f'<span style="background-color:{_ANSI_BG_COLORS[code]}">')
                    open_spans += 1
                # Unknown codes: silently ignored
        else:
            cleaned = _CONTROL_CHAR_RE.sub('', part)
            result.append(html_module.escape(cleaned))
    result.append('</span>' * open_spans)
    return ''.join(result)

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("log_monitor")

tunnel_manager=os.environ.get("TUNNEL_MANAGER", "http://localhost:11112")


@asynccontextmanager
async def lifespan(app):
    # Startup
    # Prime psutil CPU counter so first real poll returns a meaningful value
    psutil.cpu_percent(interval=None)
    app.state.monitor_task = asyncio.create_task(
        monitor_log_directory("/var/log/portal")
    )
    yield
    # Shutdown
    if hasattr(app.state, 'monitor_task'):
        app.state.monitor_task.cancel()

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def get_scheme() -> str:
    if os.environ.get("ENABLE_HTTPS", "false").lower() != "true":
        scheme = "http"
    else:
        scheme = "https"
    return scheme

def load_config() -> dict:
    yaml_path = '/etc/portal.yaml'
    
    # Wait until the file exists - caddy-manager handles config writing
    while not os.path.exists(yaml_path):
        print(f"Waiting for {yaml_path} to appear...")
        time.sleep(1)

    with open(yaml_path, 'r') as file:
        config_applications = yaml.safe_load(file)['applications']
        return hydrate_applications(config_applications)

def hydrate_applications(applications: dict) -> dict:
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

def strip_port(host: str) -> str:
    return host.split(':')[0]

def get_instance_properties() -> dict:
    return {
        "id": os.environ.get("CONTAINER_ID",""),
        "gpu": get_gpu_info(),
        "direct_https": "true" if os.environ.get("ENABLE_HTTPS", "false").lower() == "true" else "false"
    }

def get_gpu_info() -> str:
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

def get_rocm_gpus() -> list:
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
            except Exception:
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
    
def is_in_container() -> bool:
    """Check if we're running inside a container"""
    return os.path.exists('/sys/fs/cgroup/memory/memory.limit_in_bytes') or os.path.exists('/sys/fs/cgroup/memory.max')

def get_container_memory_limit() -> Optional[int]:
    """Get memory limit allocated to the container"""
    try:
        # cgroups v1
        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
            limit = int(f.read().strip())
            # Very large values typically indicate no limit
            return limit if limit < 10**15 else None
    except Exception:
        try:
            # cgroups v2
            with open('/sys/fs/cgroup/memory.max', 'r') as f:
                value = f.read().strip()
                if value == 'max':
                    return None
                return int(value)
        except Exception:
            return None

def get_container_memory_usage() -> Optional[int]:
    """Get current memory usage of the container"""
    try:
        # cgroups v1
        with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
            return int(f.read().strip())
    except Exception:
        try:
            # cgroups v2
            with open('/sys/fs/cgroup/memory.current', 'r') as f:
                return int(f.read().strip())
        except Exception:
            return None

def get_container_memory_stats() -> Optional[dict]:
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


# --- CPU metrics ---

# State for delta-based CPU measurement (cgroups)
_cpu_prev: dict = {'timestamp': 0.0, 'usage': 0, 'percent': 0.0}

def get_cpu_quota_cores() -> float:
    """Get the CPU quota in fractional cores from cgroups (for % normalization).
    Returns the quota as a float (e.g. 19.2), or falls back to os.cpu_count()."""
    try:
        # cgroups v2: cpu.max → "quota period"
        with open('/sys/fs/cgroup/cpu.max', 'r') as f:
            parts = f.read().strip().split()
            if parts[0] != 'max':
                return max(1.0, int(parts[0]) / int(parts[1]))
    except Exception:
        pass
    try:
        # cgroups v1: cpu.cfs_quota_us / cpu.cfs_period_us
        for prefix in ('/sys/fs/cgroup/cpu/', '/sys/fs/cgroup/cpu,cpuacct/'):
            quota_path = prefix + 'cpu.cfs_quota_us'
            period_path = prefix + 'cpu.cfs_period_us'
            if os.path.exists(quota_path):
                with open(quota_path, 'r') as f:
                    quota = int(f.read().strip())
                if quota == -1:
                    break  # no limit
                with open(period_path, 'r') as f:
                    period = int(f.read().strip())
                return max(1.0, quota / period)
    except Exception:
        pass
    return float(os.cpu_count() or 1)


def get_container_cpu_usage() -> Optional[dict]:
    """Get CPU usage for the container using cgroups delta measurement."""
    global _cpu_prev

    now = time.monotonic()
    usage_ns = None

    # cgroups v2: cpu.stat contains usage_usec
    try:
        with open('/sys/fs/cgroup/cpu.stat', 'r') as f:
            for line in f:
                if line.startswith('usage_usec'):
                    usage_ns = int(line.split()[1]) * 1000
                    break
    except Exception:
        pass

    # cgroups v1: cpuacct.usage in nanoseconds
    if usage_ns is None:
        for prefix in ('/sys/fs/cgroup/cpuacct/', '/sys/fs/cgroup/cpu,cpuacct/'):
            path = prefix + 'cpuacct.usage'
            try:
                with open(path, 'r') as f:
                    usage_ns = int(f.read().strip())
                break
            except Exception:
                continue

    if usage_ns is None:
        return None

    # Use quota (fractional cores) for percentage normalization,
    # but os.cpu_count() for the user-visible core count.
    quota_cores = get_cpu_quota_cores()

    elapsed = now - _cpu_prev['timestamp']
    if _cpu_prev['timestamp'] > 0 and elapsed > 0.5:
        delta_usage = usage_ns - _cpu_prev['usage']
        delta_time_ns = elapsed * 1e9
        percent = (delta_usage / delta_time_ns) * 100.0 / quota_cores
        _cpu_prev['percent'] = max(0.0, min(percent, 100.0))

    _cpu_prev['timestamp'] = now
    _cpu_prev['usage'] = usage_ns

    return {
        'percent': round(_cpu_prev['percent'], 1),
        'count': os.cpu_count() or 1
    }


def get_cpu_stats() -> dict:
    """Get CPU usage, preferring container cgroup metrics when available."""
    if is_in_container():
        container_cpu = get_container_cpu_usage()
        if container_cpu is not None:
            return container_cpu
    # VM / host fallback
    return {
        'percent': psutil.cpu_percent(interval=None),
        'count': psutil.cpu_count() or 1
    }


# --- Workspace volume metrics ---

def get_workspace_volume_info() -> Optional[dict]:
    """Get disk usage for $WORKSPACE, only if it's on a separate mounted volume."""
    workspace = os.environ.get('WORKSPACE')
    if not workspace or not os.path.isdir(workspace):
        return None
    try:
        # Different st_dev means a separate filesystem / mount
        if os.stat('/').st_dev == os.stat(workspace).st_dev:
            return None
        usage = psutil.disk_usage(workspace)
        return {
            'path': workspace,
            'total': usage.total,
            'used': usage.used,
            'percent': usage.percent
        }
    except Exception:
        return None


templates.env.filters["strip_port"] = strip_port

tunnels = {}
tunnel_api_timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, token: Optional[str] = None) -> HTMLResponse:
    if token is not None:
        return RedirectResponse(url="/", status_code=302)

    await set_external_ip(request.headers.get("X-Forwarded-Host"))
    return templates.TemplateResponse("index.html", {
        "request": request,
        "instance": get_instance_properties(),
        })

@app.get("/health")
async def health_check() -> JSONResponse:
    return JSONResponse({"status": "ok"})

@app.get("/get-applications")
async def get_applications(request: Request) -> JSONResponse:
    applications = load_config()
    auth_token = request.cookies.get(f"" + os.environ.get('VAST_CONTAINERLABEL') + "_auth_token")
    for app_name, app in applications.items():
        separator = '&' if '?' in app["open_path"] else '?'
        app["open_path"] += f"{separator}token={auth_token}"

    return JSONResponse(applications)

async def _tunnel_proxy(method: str, path: str):
    """Proxy a request to the tunnel manager service."""
    url = f"{tunnel_manager}{path}"
    async with httpx.AsyncClient(timeout=tunnel_api_timeout) as client:
        if method == "GET":
            response = await client.get(url)
        else:
            response = await client.post(url)
        response.raise_for_status()
        return response.json()

@app.get("/get-direct-url/{port}")
async def get_direct_url(port: int):
    try:
        result = await _tunnel_proxy("GET", f"/get-direct-url/{port}")
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Direct URL unavailable")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

@app.get("/get-existing-quick-tunnel/{target_url:path}")
async def get_existing_quick_tunnel(target_url: str):
    try:
        result = await _tunnel_proxy("GET", f"/get-quick-tunnel-if-exists/{target_url}")
        return HTMLResponse(result['tunnel_url'])
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Tunnel not found")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

@app.get("/get-existing-named-tunnel/{port}")
async def get_existing_named_tunnel(port: int):
    try:
        result = await _tunnel_proxy("GET", f"/get-named-tunnel/{port}")
        return HTMLResponse(result['tunnel_url'])
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Tunnel not found")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

@app.get("/get-all-quick-tunnels")
async def get_all_quick_tunnels():
    try:
        result = await _tunnel_proxy("GET", "/get-all-quick-tunnels")
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Tunnel not found")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

@app.get("/get-named-tunnels")
async def get_named_tunnels():
    try:
        result = await _tunnel_proxy("GET", "/get-named-tunnels")
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code in [404, 500]:
            raise HTTPException(status_code=404, detail="Tunnel config not found")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

@app.post("/start-quick-tunnel/{target_url:path}")
async def start_quick_tunnel(target_url: str):
    try:
        result = await _tunnel_proxy("GET", f"/get-quick-tunnel/{target_url}")
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Tunnel not found")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

@app.post("/stop-quick-tunnel/{target_url:path}")
async def stop_quick_tunnel(target_url: str):
    try:
        result = await _tunnel_proxy("POST", f"/stop-quick-tunnel/{target_url}")
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Tunnel not found")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

@app.post("/refresh-quick-tunnel/{target_url:path}")
async def refresh_quick_tunnel(target_url: str):
    try:
        result = await _tunnel_proxy("POST", f"/refresh-quick-tunnel/{target_url}")
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Tunnel not found")
        raise HTTPException(status_code=e.response.status_code, detail="Tunnel manager error")
    except httpx.HTTPError:
        raise HTTPException(status_code=500, detail="Error communicating with the API")
    except Exception:
        raise HTTPException(status_code=500, detail="Unhandled error response from API")

## Supervisor process management via XML-RPC

SUPERVISOR_SOCK = "/var/run/supervisor.sock"
_unstoppable_env = os.environ.get("PORTAL_UNSTOPPABLE", "")
UNSTOPPABLE_PROCESSES = frozenset(
    {"instance_portal"} | {s.strip() for s in _unstoppable_env.split(",") if s.strip()}
)

class _UnixStreamHTTPConnection(http.client.HTTPConnection):
    """HTTP connection over a Unix domain socket."""
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(SUPERVISOR_SOCK)

class _UnixStreamTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        return _UnixStreamHTTPConnection(host)

def _get_supervisor_proxy() -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(
        "http://localhost",
        transport=_UnixStreamTransport(),
    )

def _is_launch_managed() -> bool:
    """Check if /.launch exists (Vast manages jupyter directly)."""
    return os.path.isfile("/.launch")

@app.get("/supervisor/processes")
async def get_supervisor_processes():
    try:
        proxy = _get_supervisor_proxy()
        all_info = await asyncio.to_thread(proxy.supervisor.getAllProcessInfo)
        launch_managed = _is_launch_managed()
        result = []
        for proc in all_info:
            # Hide jupyter when /.launch is present (Vast manages it directly)
            if launch_managed and proc["name"] == "jupyter":
                continue
            result.append({
                "name": proc["name"],
                "group": proc["group"],
                "state": proc["statename"],
                "description": proc.get("description", ""),
                "pid": proc.get("pid", 0),
                "start": proc.get("start", 0),
                "now": proc.get("now", 0),
                "unstoppable": proc["name"] in UNSTOPPABLE_PROCESSES,
            })
        return result
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to communicate with supervisor")

@app.post("/supervisor/process/{name}/start")
async def supervisor_start_process(name: str):
    try:
        proxy = _get_supervisor_proxy()
        await asyncio.to_thread(proxy.supervisor.startProcess, name)
        return {"status": "ok", "name": name, "action": "start"}
    except xmlrpc.client.Fault as e:
        raise HTTPException(status_code=400, detail=e.faultString)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to communicate with supervisor")

@app.post("/supervisor/process/{name}/stop")
async def supervisor_stop_process(name: str):
    if name in UNSTOPPABLE_PROCESSES:
        raise HTTPException(status_code=403, detail=f"Process '{name}' cannot be stopped")
    try:
        proxy = _get_supervisor_proxy()
        await asyncio.to_thread(proxy.supervisor.stopProcess, name)
        return {"status": "ok", "name": name, "action": "stop"}
    except xmlrpc.client.Fault as e:
        raise HTTPException(status_code=400, detail=e.faultString)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to communicate with supervisor")

@app.post("/supervisor/process/{name}/restart")
async def supervisor_restart_process(name: str):
    try:
        proxy = _get_supervisor_proxy()
        # Stop first, ignore error if already stopped
        try:
            await asyncio.to_thread(proxy.supervisor.stopProcess, name)
        except xmlrpc.client.Fault:
            pass
        await asyncio.to_thread(proxy.supervisor.startProcess, name)
        return {"status": "ok", "name": name, "action": "restart"}
    except xmlrpc.client.Fault as e:
        raise HTTPException(status_code=400, detail=e.faultString)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to communicate with supervisor")


async def set_external_ip(forwarded_host: Optional[str]) -> None:
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

# Per-file terminal emulation state (persists across chunk reads)
_file_line_buffers: dict[str, str] = {}   # Accumulated text for current incomplete line
_file_line_active: dict[str, bool] = {}   # Whether client has a live span for this file's current line

# Dedicated task for each client to handle heartbeats and messages
async def client_handler(websocket: WebSocket, client_id: int) -> None:
    """Handle a single client's WebSocket connection"""
    try:
        # Send connection confirmation
        await websocket.send_text(json.dumps({
            "type": "system",
            "html": '<div class="log-system-message" style="color:green;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">Connected to log stream</div>'
        }))

        # Send historical logs from the single chronological buffer
        for line in chronological_log_buffer:
            html_content = ansi_to_html(line)
            await websocket.send_text(json.dumps({"type": "append", "html": html_content}))
        
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
def remove_client(websocket: WebSocket, client_id: int) -> None:
    """Safely remove a client and cancel its task"""
    if websocket in websocket_clients:
        websocket_clients.remove(websocket)
    
    if client_id in client_tasks:
        client_tasks[client_id].cancel()
        del client_tasks[client_id]
    
    logger.info(f"Client {client_id} removed, remaining clients: {len(websocket_clients)}")

# Get all log files in the directory
async def get_log_files(directory: str) -> List[str]:
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
async def broadcast_message(message: str, msg_type: str = "append", filename: str = "") -> None:
    """Send a formatted log message to all connected clients in parallel"""
    if not websocket_clients:
        return

    html_content = ansi_to_html(message)
    json_msg = json.dumps({"type": msg_type, "html": html_content, "file": filename})

    send_tasks = []
    for client in websocket_clients:
        task = asyncio.create_task(send_to_client(client, json_msg))
        send_tasks.append(task)

    results = await asyncio.gather(*send_tasks, return_exceptions=True)

    disconnected_clients = set()
    for client, result in zip(websocket_clients, results):
        if isinstance(result, Exception):
            logger.error(f"Error sending to client {id(client)}: {result}")
            disconnected_clients.add(client)

    for client in disconnected_clients:
        websocket_clients.remove(client)

async def send_to_client(client: WebSocket, content: str) -> bool:
    """Helper function to send content to a single client"""
    await client.send_text(content)
    return True

# Tail a single log file
async def tail_log_file(filepath: str) -> None:
    """Monitor a log file for changes and broadcast new content"""
    filename = os.path.basename(filepath)

    try:
        if not os.path.exists(filepath):
            return

        current_size = os.path.getsize(filepath)
        current_mtime = os.path.getmtime(filepath)
        last_position = file_positions.get(filename, None)
        last_mtime = file_mtimes.get(filename, 0)

        # First time seeing this file
        if last_position is None:
            logger.info(f"New file: {filename}, size={current_size}")
            file_specific_buffers[filename] = deque(maxlen=MAX_LINES)

            async with aiofiles.open(filepath, 'rb') as file:
                raw = await file.read()

            text = raw.decode('utf-8', errors='replace')
            # For initial read, only keep last MAX_LINES lines
            lines = text.split('\n')
            if len(lines) > MAX_LINES:
                lines = lines[-MAX_LINES:]
                text = '\n'.join(lines)

            await _process_chunk(text, filename)

            file_positions[filename] = current_size
            file_mtimes[filename] = current_mtime

        # File has been modified since last check
        elif current_mtime > last_mtime or current_size != last_position:
            logger.debug(f"File {filename} changed: size={current_size}, last_pos={last_position}")

            if current_size < last_position:
                logger.info(f"File {filename} was truncated")
                last_position = 0
                _file_line_buffers.pop(filename, None)
                _file_line_active.pop(filename, None)

            async with aiofiles.open(filepath, 'rb') as file:
                if last_position > 0:
                    await file.seek(last_position)
                raw = await file.read()

            text = raw.decode('utf-8', errors='replace')
            await _process_chunk(text, filename)

            file_positions[filename] = current_size
            file_mtimes[filename] = current_mtime

    except Exception as e:
        logger.error(f"Error tailing {filepath}: {e}")


async def _process_chunk(text: str, filename: str) -> None:
    """Process a chunk of text with terminal-like \\r and \\n handling.

    Maintains per-file state across calls so partial lines are not flushed
    prematurely. \\r means 'cursor back to start of line' (next content
    overwrites), \\n means 'line complete, advance'.
    """
    buf = _file_line_buffers.get(filename, "")
    line_active = _file_line_active.get(filename, False)

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\r':
            # \r\n is a regular newline
            if i + 1 < len(text) and text[i + 1] == '\n':
                if buf:
                    await _emit_line(filename, buf, "overwrite" if line_active else "append")
                    buf = ""
                line_active = False
                i += 2
                continue
            # Pure \r — emit what we have, stay on same line for overwrite
            if buf:
                await _emit_line(filename, buf, "overwrite" if line_active else "append")
                line_active = True   # client now has a span for this line
                buf = ""
        elif ch == '\n':
            # Line complete
            if buf:
                await _emit_line(filename, buf, "overwrite" if line_active else "append")
                buf = ""
            line_active = False
        else:
            buf += ch
        i += 1

    # If there's pending content, emit a preview so partial lines are visible
    # in real time (e.g. curl dots appearing progressively).  We keep buf
    # intact so the next chunk continues accumulating on the same line.
    if buf:
        await _emit_line(filename, buf, "overwrite" if line_active else "append")
        line_active = True

    _file_line_buffers[filename] = buf
    _file_line_active[filename] = line_active


async def _emit_line(filename: str, text: str, msg_type: str) -> None:
    """Store a line in buffers and broadcast to clients."""
    if msg_type == "overwrite":
        # Replace last entry in file-specific buffer (avoid filling buffer with
        # hundreds of intermediate progress-bar states)
        if file_specific_buffers.get(filename):
            file_specific_buffers[filename][-1] = text
        else:
            file_specific_buffers[filename].append(text)
        # For chronological buffer: also replace last entry to avoid pollution,
        # but only if that last entry actually belongs to this file's overwrite
        # sequence.  We approximate by checking if the buffer is non-empty.
        if chronological_log_buffer:
            chronological_log_buffer[-1] = text
        else:
            chronological_log_buffer.append(text)
    else:
        file_specific_buffers[filename].append(text)
        chronological_log_buffer.append(text)

    await broadcast_message(text, msg_type, filename)

# Main monitoring loop
async def monitor_log_directory(directory: str) -> None:
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
                    _file_line_buffers.pop(filename, None)
                    _file_line_active.pop(filename, None)
            
            # Small delay before next poll
            await asyncio.sleep(POLL_INTERVAL)
            
        except asyncio.CancelledError:
            logger.info("Monitor task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in monitor_log_directory: {e}")
            await asyncio.sleep(1)

# WebSocket connection handler
async def websocket_logs(websocket: WebSocket) -> None:
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
async def cleanup_tasks() -> None:
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
async def logs_websocket(websocket: WebSocket) -> None:
    await websocket_logs(websocket)

@app.get("/download-logs")
async def download_logs(filename: str = None) -> StreamingResponse:
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
async def get_system_metrics() -> JSONResponse:
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

    # CPU metrics
    metrics['cpu'] = get_cpu_stats()

    # Workspace volume (only if mounted as a separate filesystem)
    volume_info = get_workspace_volume_info()
    if volume_info:
        metrics['volume'] = volume_info
    
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
        avg_load = sum(gpu.load for gpu in all_gpus) / len(all_gpus)
        
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

