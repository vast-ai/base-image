"""ComfyUI workflow extension for the provisioner.

Parses ComfyUI workflow JSON files to extract model download URLs and
custom node package IDs, then appends them to the provisioner manifest.

Phase 1b extension — runs immediately after manifest load, before all other phases.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from provisioner.manifest import expand_env
from provisioner.schema import DownloadEntry, FileWrite, GitRepo

REGISTRY_URL = "https://api.comfy.org/nodes/{cnr_id}"
_WORKFLOWS_ENV_VAR = "PROVISIONING_COMFYUI_WORKFLOWS"


def _parse_workflow_urls(value: str) -> list[str]:
    """Parse a semicolon-delimited string of workflow URLs.

    Splits on ``;``, consistent with all other ``PROVISIONING_*`` env vars.
    Empty tokens and duplicates are discarded while preserving order.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for token in value.split(";"):
        token = token.strip()
        if token and token not in seen:
            urls.append(token)
            seen.add(token)
    return urls


def run(config: dict, context, dry_run: bool = False) -> None:
    """Extension entry point called by the provisioner."""
    log = context.log
    workflows = list(config.get("workflows", []))
    comfyui_dir = expand_env(
        config.get("comfyui_dir", "${WORKSPACE:-/workspace}/ComfyUI")
    )

    # Append workflow URLs from environment variable
    env_workflows = os.environ.get(_WORKFLOWS_ENV_VAR, "")
    if env_workflows:
        extra = _parse_workflow_urls(env_workflows)
        existing = set(workflows)
        for url in extra:
            if url not in existing:
                workflows.append(url)
                existing.add(url)
                log.info("Workflow from $%s: %s", _WORKFLOWS_ENV_VAR, url)

    if not workflows:
        log.info("No workflows configured, nothing to do")
        return

    existing_download_urls = {d.url for d in context.manifest.downloads}
    existing_repo_urls = {r.url for r in context.manifest.git_repos}
    failures: list[str] = []

    for url in workflows:
        log.info("Processing workflow: %s", url)

        if dry_run:
            log.info("[dry-run] Would fetch and parse workflow: %s", url)
            continue

        try:
            data = _fetch_workflow(url)
        except Exception as exc:
            log.warning("Failed to fetch workflow %s: %s", url, exc)
            failures.append(f"workflow fetch {url}: {exc}")
            continue

        if not _is_gui_format(data):
            log.warning(
                "Workflow %s appears to be API format (no 'nodes' key), skipping",
                url,
            )
            continue

        nodes = _collect_all_nodes(data)

        # Extract and append model downloads
        models = _extract_models(nodes, comfyui_dir)
        for entry in models:
            if entry.url not in existing_download_urls:
                context.manifest.downloads.append(entry)
                existing_download_urls.add(entry.url)
                log.info("  Model: %s -> %s", entry.url, entry.dest)
            else:
                log.debug("  Skipping duplicate download: %s", entry.url)

        # Extract and resolve custom node repos
        cnr_ids = _extract_custom_node_ids(nodes)
        for cnr_id in sorted(cnr_ids):
            repo_url = _resolve_node_repo(cnr_id, log)
            if repo_url and repo_url not in existing_repo_urls:
                repo_name = _repo_name_from_url(repo_url)
                dest = f"{comfyui_dir}/custom_nodes/{repo_name}"
                context.manifest.git_repos.append(
                    GitRepo(url=repo_url, dest=dest)
                )
                existing_repo_urls.add(repo_url)
                log.info("  Custom node: %s -> %s", cnr_id, dest)
            elif repo_url:
                log.debug("  Skipping duplicate repo: %s", repo_url)
            else:
                failures.append(f"node resolution {cnr_id}")

        # Save workflow JSON to ComfyUI user workflows dir
        filename = _workflow_filename(url)
        wf_path = f"{comfyui_dir}/user/default/workflows/{filename}"
        context.manifest.write_files_late.append(
            FileWrite(path=wf_path, content=json.dumps(data, indent=2))
        )
        log.info("  Will save workflow to %s", wf_path)

    if failures:
        raise RuntimeError(
            f"ComfyUI extension failed to resolve {len(failures)} item(s): "
            + "; ".join(failures)
        )


def _collect_all_nodes(data: dict) -> list[dict]:
    """Collect nodes from top-level and from nested subgraph definitions.

    Newer ComfyUI workflows embed subgraphs under
    ``definitions.subgraphs[].nodes``.  Model loaders and custom node
    references live inside these subgraphs rather than in the top-level
    ``nodes`` list.
    """
    nodes = list(data.get("nodes", []))
    for sg in data.get("definitions", {}).get("subgraphs", []):
        nodes.extend(sg.get("nodes", []))
    return nodes


def _fetch_workflow(url: str) -> dict:
    """Download and parse a workflow JSON file."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_gui_format(data: dict) -> bool:
    """Check if the workflow is GUI format (has 'nodes' key)."""
    return "nodes" in data


def _extract_models(nodes: list[dict], comfyui_dir: str) -> list[DownloadEntry]:
    """Extract model download entries from workflow nodes."""
    entries = []
    seen_urls = set()
    for node in nodes:
        props = node.get("properties", {})
        if not isinstance(props, dict):
            continue
        models = props.get("models", [])
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            url = model.get("url", "")
            name = model.get("name", "")
            directory = model.get("directory", "")
            if not url or not name:
                continue
            clean = _clean_url(url)
            if clean in seen_urls:
                continue
            seen_urls.add(clean)
            dest = f"{comfyui_dir}/models/{directory}/{name}" if directory else f"{comfyui_dir}/models/{name}"
            entries.append(DownloadEntry(url=clean, dest=dest))
    return entries


def _extract_custom_node_ids(nodes: list[dict]) -> set[str]:
    """Extract unique custom node package IDs, filtering out comfy-core."""
    ids = set()
    for node in nodes:
        props = node.get("properties", {})
        if not isinstance(props, dict):
            continue
        cnr_id = props.get("cnr_id", "")
        if cnr_id and cnr_id != "comfy-core":
            ids.add(cnr_id)
    return ids


def _resolve_node_repo(cnr_id: str, log) -> str | None:
    """Look up a custom node's git repo URL via the ComfyUI registry API."""
    url = REGISTRY_URL.format(cnr_id=urllib.parse.quote(cnr_id, safe=""))
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            repo = data.get("repository", "")
            if repo:
                # Normalize: ensure .git suffix
                if not repo.endswith(".git"):
                    repo += ".git"
                return repo
            log.warning("No repository field for node %s", cnr_id)
            return None
    except Exception as exc:
        log.warning("Failed to resolve node %s: %s", cnr_id, exc)
        return None


def _repo_name_from_url(url: str) -> str:
    """Extract the repository directory name from a git URL."""
    path = urllib.parse.urlparse(url).path
    name = os.path.basename(path)
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _workflow_filename(url: str) -> str:
    """Derive a filename from a workflow URL."""
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path)
    if not name:
        name = "workflow.json"
    if not name.endswith(".json"):
        name += ".json"
    return name


def _clean_url(url: str) -> str:
    """Strip query parameters like ?download=true from URLs."""
    parsed = urllib.parse.urlparse(url)
    if parsed.query:
        return urllib.parse.urlunparse(parsed._replace(query=""))
    return url
