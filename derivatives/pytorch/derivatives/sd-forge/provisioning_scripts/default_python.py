#!/opt/portal-aio/venv/bin/python
"""
Forge Provisioning Script

Downloads models, installs extensions, and prepares the Forge environment.
"""

import os
import sys
import subprocess


def bootstrap_dependencies():
    """Install required dependencies if not present."""
    required = {
        "requests": "requests",
        "filelock": "filelock",
        "huggingface_hub": "huggingface_hub",
    }

    missing = []
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"[bootstrap] Installing missing dependencies: {', '.join(missing)}")
        # Use pip from the same venv as the running Python interpreter
        venv_bin = os.path.dirname(sys.executable)
        pip_path = os.path.join(venv_bin, "pip")
        subprocess.check_call(
            [pip_path, "install", "--quiet"] + missing,
            stdout=subprocess.DEVNULL,
        )
        print("[bootstrap] Dependencies installed successfully")


# Install dependencies before importing them
bootstrap_dependencies()

import logging
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional
import shutil
import re

import requests
from filelock import FileLock
from huggingface_hub import hf_hub_download

### Configuration ###

WORKSPACE_DIR = os.environ.get("WORKSPACE", "/workspace")
FORGE_DIR = f"{WORKSPACE_DIR}/stable-diffusion-webui-forge"
MODELS_DIR = f"{FORGE_DIR}/models"
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "3"))
PROVISIONING_LOG = os.environ.get("PROVISIONING_LOG", "/var/log/portal/forge.log")

# APT packages to install
APT_PACKAGES: list[str] = [
    # "package-1",
    # "package-2",
]

# Python packages to install
PIP_PACKAGES: list[str] = [
    # "package-1",
    # "package-2",
]

# Extensions to install (git URLs)
EXTENSIONS_DEFAULT: list[str] = [
    # "https://github.com/example/extension-name",
]

# Model downloads use "URL|OUTPUT_PATH" format
# HuggingFace models (requires HF_TOKEN for gated models)
HF_MODELS_DEFAULT: list[str] = [
    # f"https://huggingface.co/stabilityai/sd-vae-ft-mse-original/resolve/main/vae-ft-mse-840000-ema-pruned.safetensors|{MODELS_DIR}/VAE/vae-ft-mse-840000-ema-pruned.safetensors",
]

# CivitAI models (requires CIVITAI_TOKEN for some models)
CIVITAI_MODELS_DEFAULT: list[str] = [
    # f"https://civitai.com/api/download/models/798204?type=Model&format=SafeTensor&size=full&fp=fp16|{MODELS_DIR}/Stable-diffusion/",
]

# Generic wget downloads (no auth)
WGET_DOWNLOADS_DEFAULT: list[str] = [
    # f"https://example.com/file.safetensors|{MODELS_DIR}/other/file.safetensors",
]

### End Configuration ###


@dataclass
class DownloadEntry:
    """Represents a URL|PATH download entry."""
    url: str
    output_path: str


def setup_logging() -> logging.Logger:
    """Configure logging to both file and stdout."""
    log_path = Path(PROVISIONING_LOG)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("provisioning")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # File handler
    fh = logging.FileHandler(log_path)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


log = setup_logging()


def parse_env_list(env_var: str) -> list[str]:
    """Parse a semicolon-separated environment variable into a list."""
    value = os.environ.get(env_var, "")
    if not value:
        return []

    entries = []
    for entry in value.split(";"):
        entry = " ".join(entry.split())  # Normalize whitespace
        if entry and not entry.startswith("#"):
            entries.append(entry)
    return entries


def merge_with_env(env_var: str, defaults: list[str]) -> list[str]:
    """Merge default entries with environment variable additions."""
    result = []

    # Add defaults (filtering comments and empty entries)
    for entry in defaults:
        entry = " ".join(entry.split())  # Normalize whitespace
        if entry and not entry.startswith("#"):
            result.append(entry)

    # Add entries from environment variable
    env_entries = parse_env_list(env_var)
    if env_entries:
        log.info(f"Adding entries from {env_var} environment variable")
        result.extend(env_entries)

    return result


def parse_download_entry(entry: str) -> Optional[DownloadEntry]:
    """Parse a 'URL|OUTPUT_PATH' string into a DownloadEntry."""
    if "|" not in entry:
        log.warning(f"Invalid entry format (missing |): {entry}")
        return None

    url, output_path = entry.split("|", 1)
    url = url.strip()
    output_path = output_path.strip()

    if not url or not output_path:
        log.warning(f"Invalid entry (empty URL or path): {entry}")
        return None

    return DownloadEntry(url=url, output_path=output_path)


def has_valid_hf_token() -> bool:
    """Check if HF_TOKEN is set and valid."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        return False

    try:
        resp = requests.get(
            "https://huggingface.co/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def has_valid_civitai_token() -> bool:
    """Check if CIVITAI_TOKEN is set and valid."""
    token = os.environ.get("CIVITAI_TOKEN")
    if not token:
        return False

    try:
        resp = requests.get(
            "https://civitai.com/api/v1/models?hidden=1&limit=1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def download_hf_file(entry: DownloadEntry, max_retries: int = 5) -> bool:
    """Download a file from HuggingFace using the hub library."""
    output_path = Path(entry.output_path)
    lock_path = output_path.with_suffix(output_path.suffix + ".lock")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(lock_path, timeout=300):
        # Check if already exists
        if output_path.exists():
            log.info(f"File already exists: {output_path} (skipping)")
            return True

        # Parse HuggingFace URL
        # Format: https://huggingface.co/org/repo/resolve/branch/path/to/file
        match = re.match(
            r"https://huggingface\.co/([^/]+/[^/]+)/resolve/[^/]+/(.+)",
            entry.url
        )
        if not match:
            log.error(f"Invalid HuggingFace URL: {entry.url}")
            return False

        repo_id = match.group(1)
        filename = match.group(2)

        for attempt in range(1, max_retries + 1):
            try:
                log.info(f"Downloading {repo_id}/{filename} (attempt {attempt}/{max_retries})...")

                downloaded_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=output_path.parent,
                    local_dir_use_symlinks=False,
                    token=os.environ.get("HF_TOKEN"),
                )

                # Move to final location if needed
                downloaded = Path(downloaded_path)
                if downloaded != output_path:
                    shutil.move(downloaded, output_path)

                log.info(f"Successfully downloaded: {output_path}")
                return True

            except Exception as e:
                delay = 2 ** attempt
                log.warning(f"Download failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    log.info(f"Retrying in {delay}s...")
                    import time
                    time.sleep(delay)

        log.error(f"Failed to download {entry.url} after {max_retries} attempts")
        return False


def get_content_disposition_filename(url: str, headers: dict) -> Optional[str]:
    """Get filename from content-disposition header via HEAD request."""
    try:
        resp = requests.head(url, headers=headers, allow_redirects=True, timeout=30)
        cd = resp.headers.get("content-disposition", "")
        match = re.search(r'filename="?([^";\n]+)"?', cd)
        if match:
            return match.group(1).strip()
    except requests.RequestException:
        pass
    return None


def download_file(entry: DownloadEntry, auth_type: str = "", max_retries: int = 5) -> bool:
    """Download a file using requests (for CivitAI and generic URLs)."""
    output_path = Path(entry.output_path)
    use_content_disposition = entry.output_path.endswith("/")

    if use_content_disposition:
        output_dir = Path(entry.output_path.rstrip("/"))
    else:
        output_dir = output_path.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    # Build auth headers
    headers = {}
    if auth_type == "civitai" and os.environ.get("CIVITAI_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['CIVITAI_TOKEN']}"

    # Create lock based on URL hash
    url_hash = hashlib.md5(entry.url.encode()).hexdigest()
    lock_path = output_dir / f".download_{url_hash}.lock"

    with FileLock(lock_path, timeout=300):
        # Determine output filename
        if use_content_disposition:
            filename = get_content_disposition_filename(entry.url, headers)
            if not filename:
                filename = entry.url.split("/")[-1].split("?")[0]
            final_path = output_dir / filename
        else:
            final_path = output_path

        # Check if already exists
        if final_path.exists():
            log.info(f"File already exists: {final_path} (skipping)")
            return True

        for attempt in range(1, max_retries + 1):
            try:
                log.info(f"Downloading: {entry.url} (attempt {attempt}/{max_retries})...")

                resp = requests.get(entry.url, headers=headers, stream=True, timeout=60)
                resp.raise_for_status()

                # Download to temp file first
                temp_path = final_path.with_suffix(final_path.suffix + ".tmp")
                with open(temp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)

                # Move to final location
                shutil.move(temp_path, final_path)

                log.info(f"Successfully downloaded: {final_path}")
                return True

            except Exception as e:
                delay = 2 ** attempt
                log.warning(f"Download failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    log.info(f"Retrying in {delay}s...")
                    import time
                    time.sleep(delay)

        log.error(f"Failed to download {entry.url} after {max_retries} attempts")
        return False


def install_apt_packages():
    """Install APT packages if any are configured."""
    if not APT_PACKAGES:
        return

    log.info("Installing APT packages...")
    subprocess.run(["sudo", "apt-get", "update"], check=True)
    subprocess.run(["sudo", "apt-get", "install", "-y"] + APT_PACKAGES, check=True)


def install_pip_packages():
    """Install Python packages if any are configured."""
    if not PIP_PACKAGES:
        return

    log.info("Installing Python packages...")
    subprocess.run(["uv", "pip", "install", "--no-cache-dir"] + PIP_PACKAGES, check=True)


def install_extensions(extensions: list[str]):
    """Install Forge extensions from git repositories."""
    if not extensions:
        log.info("No extensions to install")
        return

    log.info(f"Installing {len(extensions)} extension(s)...")

    # Configure git to avoid ownership errors
    git_config = "/tmp/temporary-git-config"
    subprocess.run(
        ["git", "config", "--file", git_config, "--add", "safe.directory", "*"],
        check=True
    )
    os.environ["GIT_CONFIG_GLOBAL"] = git_config

    extensions_dir = Path(FORGE_DIR) / "extensions"
    extensions_dir.mkdir(parents=True, exist_ok=True)

    for repo_url in extensions:
        if not repo_url:
            continue

        # Extract directory name from URL
        dir_name = repo_url.rstrip("/").split("/")[-1]
        if dir_name.endswith(".git"):
            dir_name = dir_name[:-4]

        ext_path = extensions_dir / dir_name

        if ext_path.exists():
            log.info(f"Extension already installed: {dir_name}")
        else:
            log.info(f"Installing extension: {repo_url}")
            subprocess.run(
                ["git", "clone", repo_url, str(ext_path), "--recursive"],
                check=True
            )


def download_models(entries: list[str], auth_type: str) -> bool:
    """Download models in parallel."""
    download_entries = []
    for entry_str in entries:
        entry = parse_download_entry(entry_str)
        if entry:
            download_entries.append(entry)

    if not download_entries:
        return True

    failed = False

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {}
        for entry in download_entries:
            log.info(f"Queuing download: {entry.url} -> {entry.output_path}")

            if auth_type == "hf":
                future = executor.submit(download_hf_file, entry)
            else:
                future = executor.submit(download_file, entry, auth_type)

            futures[future] = entry

        for future in as_completed(futures):
            entry = futures[future]
            try:
                if not future.result():
                    log.error(f"Download failed: {entry.url}")
                    failed = True
            except Exception as e:
                log.error(f"Download error for {entry.url}: {e}")
                failed = True

    return not failed


def run_startup_test():
    """Run Forge startup test to ensure dependencies are ready."""
    log.info("Running Forge startup test...")

    # Configure git
    git_config = "/tmp/temporary-git-config"
    subprocess.run(
        ["git", "config", "--file", git_config, "--add", "safe.directory", "*"],
        check=True
    )
    os.environ["GIT_CONFIG_GLOBAL"] = git_config

    env = os.environ.copy()
    env["LD_PRELOAD"] = "libtcmalloc_minimal.so.4"

    subprocess.run(
        [
            "/venv/main/bin/python", "launch.py",
            "--skip-python-version-check",
            "--no-download-sd-model",
            "--do-not-download-clip",
            "--no-half",
            "--port", "11404",
            "--exit"
        ],
        cwd=FORGE_DIR,
        env=env,
        check=True
    )


def main():
    log.info("=" * 40)
    log.info("Starting Forge provisioning...")
    log.info("=" * 40)

    # Check for skip flag
    if Path("/.noprovisioning").exists():
        log.info("Provisioning skipped (/.noprovisioning exists)")
        return 0

    # Validate tokens
    if os.environ.get("HF_TOKEN"):
        if has_valid_hf_token():
            log.info("HuggingFace token validated")
        else:
            log.warning("HF_TOKEN is set but appears invalid")

    if os.environ.get("CIVITAI_TOKEN"):
        if has_valid_civitai_token():
            log.info("CivitAI token validated")
        else:
            log.warning("CIVITAI_TOKEN is set but appears invalid")

    # Build model lists (merge defaults with env vars)
    hf_models = merge_with_env("HF_MODELS", HF_MODELS_DEFAULT)
    civitai_models = merge_with_env("CIVITAI_MODELS", CIVITAI_MODELS_DEFAULT)
    wget_downloads = merge_with_env("WGET_DOWNLOADS", WGET_DOWNLOADS_DEFAULT)
    extensions = merge_with_env("EXTENSIONS", EXTENSIONS_DEFAULT)

    log.info(f"HF_MODELS: {len(hf_models)} entries")
    log.info(f"CIVITAI_MODELS: {len(civitai_models)} entries")
    log.info(f"WGET_DOWNLOADS: {len(wget_downloads)} entries")

    # Install packages
    install_apt_packages()
    install_pip_packages()

    # Install extensions
    install_extensions(extensions)

    # Download models
    log.info("Starting model downloads...")
    download_failed = False

    if hf_models:
        if not download_models(hf_models, "hf"):
            download_failed = True

    if civitai_models:
        if not download_models(civitai_models, "civitai"):
            download_failed = True

    if wget_downloads:
        if not download_models(wget_downloads, ""):
            download_failed = True

    if download_failed:
        log.error("One or more downloads failed")
        return 1

    log.info("All downloads completed successfully")

    # Run startup test
    run_startup_test()

    log.info("=" * 40)
    log.info("Provisioning complete!")
    log.info("=" * 40)

    return 0


if __name__ == "__main__":
    sys.exit(main())
