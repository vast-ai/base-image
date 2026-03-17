import os
import re
import requests
import asyncio
import json
import yaml
from datetime import datetime, timedelta
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException
import aiohttp
from urllib.parse import urlparse
from cachetools import TTLCache
import ipaddress
import logging

cloudflare_metrics = os.environ.get("CLOUDFLARE_METRICS", "localhost:11113")
public_ipaddr = None
app = FastAPI()


CLOUDFLARED_BIN = "/opt/portal-aio/tunnel_manager/cloudflared"
CF_TUNNEL_TOKEN = os.environ.get('CF_TUNNEL_TOKEN')
cloudflared_account_process: Optional[asyncio.subprocess.Process] = None

def get_scheme():
    if os.environ.get("ENABLE_HTTPS", "false").lower() != "true":
        scheme = "http"
    else:
        scheme = "https"
    return scheme

def load_config():
    yaml_path = '/etc/portal.yaml'
    if os.path.exists(yaml_path):
        with open(yaml_path, 'r') as file:
            data = yaml.safe_load(file) or {}
            config_applications = data.get('applications', {})
            return hydrate_applications(config_applications)
    return {}

def hydrate_applications(applications):
    for app_name, app in applications.items():
        if app["external_port"] == app["internal_port"] and app["internal_port"] == 8080:
            scheme = "https"
        else:
            scheme = get_scheme()
        applications[app_name]["target_url"] = f'{scheme}://{app["hostname"]}:{app["external_port"]}'
    return applications

# Function to fetch the public IP address
def get_public_ip():
    # We should have this from the first request after clicking the Open button - Use the less reliable $PUBLIC_IPADDR otherwise
    if public_ipaddr is not None:
        return public_ipaddr
    else:
        return os.environ.get('PUBLIC_IPADDR')

@app.put("/set-public-ip/{ip}")
def set_external_ip(ip: str):
    try:
        # Try to create an IPv4 address object, which will raise a ValueError if invalid
        ip_obj = ipaddress.IPv4Address(ip)
        
        # If valid, set the global ip_address
        global public_ipaddr
        public_ipaddr = str(ip_obj)
        
        # Return status 200 if successful
        return {"status": "success", "ip": public_ipaddr}

    except ValueError:
        # Return status 500 if invalid IP address
        raise HTTPException(status_code=500, detail="Invalid IPv4 address")


@app.get("/get-direct-url/{port}")
def get_port_mapping(port: int):
    # Fetch the public IP
    public_ip = get_public_ip()

    if os.environ.get("ENABLE_HTTPS", "false").lower() == "true" or port == 8080:
        scheme = "https://"
    else:
        scheme = "http://"

    # Fetch the environment variable dynamically
    env_var_name = f"VAST_TCP_PORT_{port}"
    port_value = os.getenv(env_var_name)

    if port_value is None:
        raise HTTPException(status_code=404, detail=f"Environment variable {env_var_name} not found")

    # Return the {PUBLIC_IP}:{PORT_VALUE}
    return {"result": f"{scheme}{public_ip}:{port_value}"}

class QuickTunnel:
    def __init__(self, target_url: str):
        self.target = self.get_parsed_target(target_url)
        self.protocol = self.target.scheme
        self.hostname = self.target.hostname
        self.port = self.target.port
        self.process: Optional[asyncio.subprocess.Process] = None
        self.tunnel_url: Optional[str] = None
        self._print_task = None

    def get_parsed_target(self, target_url: str):
        """Parse and normalize target URL."""
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'http://' + target_url
        
        return urlparse(target_url)

    async def start(self, timeout: int = 30):
        """Start the cloudflared process and capture the tunnel URL."""
        try:
            self.process = await asyncio.create_subprocess_exec(
                CLOUDFLARED_BIN, '--no-tls-verify', '--url', self.target.geturl(),
                env = os.environ.copy(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            
            # Create a task for getting the tunnel URL
            async def wait_for_tunnel_url():
                while True:
                    if not self.process or self.process.stdout is None:
                        raise Exception("Process or stdout is None")
                        
                    line = await self.process.stdout.readline()
                    if not line:
                        raise Exception("Process stopped without providing tunnel URL")
                        
                    line_str = line.decode().strip()
                    print(f"[{self.target.geturl()}] {line_str}")
                    # Avoid error response URLS on the trycloudflare domain
                    match = re.search(r'(https://\w+(-\w+){1,}\.trycloudflare\.com)', line_str)
                    if match:
                        self.tunnel_url = match.group(1)
                        return self.tunnel_url

            # Wait for URL with timeout
            try:
                await asyncio.wait_for(wait_for_tunnel_url(), timeout)
            except asyncio.TimeoutError:
                await self.stop()
                raise TimeoutError(f"Tunnel didn't start within {timeout} seconds")

            if not self.tunnel_url:
                raise Exception("Failed to start tunnel")
            
            self._print_task = asyncio.create_task(self._print_output())
            
        except Exception as e:
            await self.stop()
            raise Exception(f"Failed to start tunnel: {str(e)}")

    async def _print_output(self):
        """Continuously print the process output."""
        while True:
            try:
                if not self.process or self.process.stdout is None:
                    break
                    
                line = await self.process.stdout.readline()
                if not line:
                    break
                    
                print(f"[{self.target.geturl()}] {line.decode().strip()}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error reading output for {self.target.geturl()}: {e}")
                break

    async def stop(self):
        """Stop the tunnel and printing."""
        if self._print_task:
            self._print_task.cancel()
            try:
                await self._print_task
            except asyncio.CancelledError:
                pass
                
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()  # Force kill if it doesn't terminate
                await self.process.wait()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

quick_tunnels: Dict[str, QuickTunnel] = {}

async def monitor_processes():
    """Ensure tunnels stopped outside of this process are handled"""
    while True:
        for key, tunnel in list(quick_tunnels.items()):
            if tunnel.process.returncode is not None:
                print(f"Process for {key} has died. Removing from dictionary.")
                del quick_tunnels[key]
        await asyncio.sleep(10)  # Check every 10 seconds

class CloudflareDaemon:
    def __init__(self, token: str):
        self.token = token
        self.metrics = f"{cloudflare_metrics}"
        self.process: Optional[asyncio.subprocess.Process] = None
        self._print_task = None

    async def start(self, timeout: int = 30):
        """Start the cloudflared process and wait for startup confirmation."""
        try:
            async def start_process():
                self.process = await asyncio.create_subprocess_exec(
                    CLOUDFLARED_BIN, 'tunnel', '--metrics', self.metrics, 
                    'run', '--token', self.token,
                    env = os.environ.copy(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )
                
                # Wait for first line of output to confirm startup
                if self.process.stdout:
                    first_line = await self.process.stdout.readline()
                    if not first_line:
                        raise Exception("Process stopped immediately after startup")
                    print(f"Cloudflare daemon started: {first_line.decode().strip()}")

            # Wait for process to start with timeout
            try:
                await asyncio.wait_for(start_process(), timeout)
            except asyncio.TimeoutError:
                await self.stop()
                raise TimeoutError(f"Daemon didn't start within {timeout} seconds")

            self._print_task = asyncio.create_task(self._print_output())
            
        except Exception as e:
            await self.stop()
            raise Exception(f"Failed to start cloudflared daemon: {str(e)}")

    async def _print_output(self):
        """Continuously print the process output."""
        while True:
            try:
                if not self.process or self.process.stdout is None:
                    break
                line = await self.process.stdout.readline()
                if not line:
                    break
                print(f"[cloudflared] {line.decode().strip()}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error reading output: {e}")
                break

    async def stop(self):
        """Stop the tunnel and printing."""
        if self._print_task:
            self._print_task.cancel()
            try:
                await self._print_task
            except asyncio.CancelledError:
                pass
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()  # Force kill if it doesn't terminate
                await self.process.wait()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()


async def create_quick_tunnel(target_url: str) -> QuickTunnel:
    tunnel = QuickTunnel(target_url)
    await tunnel.start()
    return tunnel


async def get_or_create_quick_tunnel(target_url: str) -> QuickTunnel:
    if target_url in quick_tunnels:
        return quick_tunnels[target_url]
    
    tunnel = await create_quick_tunnel(target_url)
    quick_tunnels[target_url] = tunnel
    return tunnel

async def get_existing_quick_tunnel(target_url: str) -> QuickTunnel:
    if target_url in quick_tunnels:
        return quick_tunnels[target_url]
    else:
        return None

async def get_named_tunnel_url(port: int) -> str:
    config_url = f"http://{cloudflare_metrics}/config"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(config_url, timeout=10) as response:
                if response.status != 200:
                    raise HTTPException(status_code=404, detail="Tunnel config not found")
                
                content = await response.text()
                
                try:
                    metrics = json.loads(content)
                except json.JSONDecodeError as e:
                    raise Exception("Failed to parse config as JSON")

        ingress = metrics.get('config', {}).get('ingress', [])
        
        for entry in ingress:
            service = entry.get('service', '')
            if isinstance(service, str) and service.startswith("http") and service.endswith(f":{port}"):
                hostname = entry.get('hostname')
                if hostname:
                    return f"https://{hostname}"
                
        raise HTTPException(status_code=404, detail="Named tunnel not found")
    
    except HTTPException as e:
        # Re-raise the HTTPException without modifying it
        raise e
    except asyncio.TimeoutError:
        raise Exception("Timeout while connecting to cloudflared metrics endpoint")
    except aiohttp.ClientError as e:
        raise Exception(f"Connection error: {str(e)}")
    except Exception as e:
        raise

@app.get("/get-named-tunnels")
async def get_named_tunnels():
    config_url = f"http://{cloudflare_metrics}/config"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(config_url, timeout=10) as response:
                if response.status != 200:
                    raise HTTPException(status_code=404, detail="Tunnel config not found")
                
                content = await response.text()
                metrics = json.loads(content)

        ingress = metrics.get('config', {}).get('ingress', [])
        named_tunnels = []
        
        for entry in ingress:
            service = entry.get('service', '')
            hostname = entry.get('hostname')
            if isinstance(service, str) and service.startswith("http") and hostname:
                named_tunnels.append({
                    "targetUrl": service,
                    "tunnelUrl": f"https://{hostname}"
                })
                
        return named_tunnels

    except HTTPException as e:
        # Re-raise the HTTPException without modifying it
        raise e
    except aiohttp.ClientConnectorError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get-quick-tunnel/{target_url:path}")
async def get_quick_tunnel(target_url: str):
    try:
        tunnel = await get_or_create_quick_tunnel(target_url)
        return {"tunnel_url": tunnel.tunnel_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get-quick-tunnel-if-exists/{target_url:path}")
async def get_quick_tunnel_if_exists(target_url: str):
    try:
        tunnel = await get_existing_quick_tunnel(target_url)
        if tunnel:
            return {"tunnel_url": tunnel.tunnel_url}
        else:
            raise HTTPException(status_code=404, detail="Quick tunnel not found")
    except HTTPException as e:
        # Re-raise the HTTPException without modifying it
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/get-all-quick-tunnels")
async def get_all_quick_tunnels():
    active_tunnels = []
    for target_url, tunnel in quick_tunnels.items():
        if tunnel.tunnel_url:
            active_tunnels.append({
                "targetUrl": target_url,
                "tunnelUrl": tunnel.tunnel_url
            })
    return active_tunnels

@app.post("/stop-quick-tunnel/{target_url:path}")
async def stop_quick_tunnel(target_url: str):
    if target_url not in quick_tunnels:
        raise HTTPException(status_code=404, detail="Quick tunnel not found")
    
    tunnel = quick_tunnels[target_url]
    await tunnel.stop()

    del quick_tunnels[target_url]
    return {"message": "Quick tunnel stopped successfully"}

@app.post("/refresh-quick-tunnel/{target_url:path}")
async def refresh_quick_tunnel(target_url: str):
    if target_url in quick_tunnels:
        await stop_quick_tunnel(target_url)
    tunnel = await get_or_create_quick_tunnel(target_url)
    return {"tunnel_url": tunnel.tunnel_url}

@app.get("/get-named-tunnel/{port:int}")
async def get_named_tunnel(port: int):
    if not CF_TUNNEL_TOKEN:
        raise HTTPException(status_code=404, detail="CF_TUNNEL_TOKEN is not set")
    try:
        tunnel_url = await get_named_tunnel_url(port)
        return {"tunnel_url": tunnel_url}
    
    except HTTPException as e:
        # Re-raise the HTTPException without modifying it
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def _create_default_tunnels():
    """Create quick tunnels for all configured apps.

    Runs as a background task so the API server is available immediately
    (otherwise /get-direct-url etc. return 500 while tunnels are being
    created).
    """
    try:
        config = load_config()
        unique_targets = {}
        for app_name, app_config in config.items():
            target_url = app_config['target_url']
            if target_url not in unique_targets:
                unique_targets[target_url] = app_name

        # Create tunnels sequentially with a small delay between each
        # to avoid hitting Cloudflare's rate limiter on quick tunnel
        # creation.  Failed tunnels are retried with longer back-off.
        failed_targets = []
        for i, target_url in enumerate(unique_targets):
            app_name = unique_targets[target_url]
            if i > 0:
                await asyncio.sleep(2)
            try:
                tunnel = await get_or_create_quick_tunnel(target_url)
                print(f"Default Tunnel started for {app_name} ({target_url}) - {tunnel.tunnel_url}?token={os.environ.get('OPEN_BUTTON_TOKEN')}")
            except Exception as e:
                print(f"Failed to create default tunnel for {app_name} ({target_url}): {e}")
                failed_targets.append(target_url)

        # Retry failed tunnels with increasing back-off
        for target_url in failed_targets:
            app_name = unique_targets[target_url]
            for attempt in range(1, 4):
                delay = attempt * 10
                print(f"Retrying tunnel for {app_name} ({target_url}) in {delay}s (attempt {attempt}/3)")
                await asyncio.sleep(delay)
                try:
                    tunnel = await get_or_create_quick_tunnel(target_url)
                    print(f"Default Tunnel started for {app_name} ({target_url}) - {tunnel.tunnel_url}?token={os.environ.get('OPEN_BUTTON_TOKEN')}")
                    break
                except Exception as e:
                    print(f"Retry {attempt}/3 failed for {app_name} ({target_url}): {e}")
    except Exception as e:
        print(f"Error creating default tunnels: {e}")


@app.on_event("startup")
async def startup_event():
    # Suppress uvicorn access logs after uvicorn has configured its loggers.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    try:
        # Monitor quick tunnels in case user kills them
        asyncio.create_task(monitor_processes())

        # Create the main cloudflared process to handle named tunnels
        global cloudflared_account_process
        if CF_TUNNEL_TOKEN:
            try:
                cloudflared_account_process = CloudflareDaemon(CF_TUNNEL_TOKEN)
                await cloudflared_account_process.start()
                print("Named tunnel process started")
            except Exception as e:
                print(f"Failed to start named tunnel process: {str(e)}")

        # Create default tunnels in the background so the API server
        # starts accepting requests immediately (e.g. /get-direct-url).
        asyncio.create_task(_create_default_tunnels())
    except Exception as e:
        print(f"Startup failed: {str(e)}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    for tunnel in quick_tunnels.values():
        await tunnel.stop()

    if cloudflared_account_process:
        await cloudflared_account_process.stop()
        print("Full tunnel process terminated")

    await asyncio.gather(*[tunnel.process.wait() for tunnel in quick_tunnels.values() if tunnel.process])
    if cloudflared_account_process:
        await cloudflared_account_process.wait()