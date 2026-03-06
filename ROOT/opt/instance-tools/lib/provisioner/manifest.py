"""YAML manifest loading, environment variable expansion, and env_merge."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import urllib.error
import urllib.request

import yaml

from .schema import CondaPackages, DownloadEntry, GitRepo, Manifest, PipPackages, validate_manifest

log = logging.getLogger("provisioner")

# Default download location for remote manifests (matches boot script convention)
_DEFAULT_MANIFEST_CACHE = "/provisioning.yaml"

# Matches ${VAR}, ${VAR:-default}, ${VAR:=default}
_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_var(match: re.Match) -> str:
    """Expand a single ${...} expression."""
    expr = match.group(1)

    # ${VAR:-default} or ${VAR:=default}
    for sep in (":-", ":="):
        if sep in expr:
            var_name, default = expr.split(sep, 1)
            return os.environ.get(var_name, default)

    # ${VAR}
    return os.environ.get(expr, "")


def expand_env(value: str) -> str:
    """Expand all ${...} expressions in a string."""
    return _ENV_PATTERN.sub(_expand_var, value)


def _expand_recursive(obj):
    """Recursively expand environment variables in all string values."""
    if isinstance(obj, str):
        return expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_recursive(item) for item in obj]
    return obj


def _parse_env_merge_entries(env_value: str) -> list[DownloadEntry]:
    """Parse semicolon-separated 'url|path' entries from an env var value.

    Format: "url1|path1;url2|path2"
    This matches the convention used in existing provisioning scripts.
    """
    entries = []
    for raw in env_value.split(";"):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        if "|" not in raw:
            log.warning("Skipping malformed env_merge entry (no '|'): %s", raw)
            continue
        url, dest = raw.split("|", 1)
        url = url.strip()
        dest = dest.strip()
        if url and dest:
            entries.append(DownloadEntry(url=url, dest=dest))
    return entries


def apply_env_merge(manifest: Manifest) -> None:
    """Merge download entries from environment variables into the manifest.

    For each entry in env_merge, read the named env var, parse its
    semicolon-separated url|path values, and append to the downloads list.
    """
    for env_var, target in manifest.env_merge.items():
        value = os.environ.get(env_var, "")
        if not value:
            continue
        if target != "downloads":
            log.warning("env_merge target '%s' not supported (only 'downloads')", target)
            continue
        entries = _parse_env_merge_entries(value)
        if entries:
            log.info("env_merge: added %d downloads from $%s", len(entries), env_var)
            manifest.downloads.extend(entries)


def resolve_conditionals(
    manifest: Manifest,
    hf_token_valid: bool,
    civitai_token_valid: bool = False,
) -> None:
    """Resolve conditional_downloads and merge results into downloads."""
    condition_map = {
        "hf_token_valid": hf_token_valid,
        "civitai_token_valid": civitai_token_valid,
    }

    for cond in manifest.conditional_downloads:
        result = condition_map.get(cond.when)
        if result is None:
            log.warning("Unknown condition: '%s' -- skipping", cond.when)
            continue
        if result:
            log.info("Condition '%s' is true: adding %d downloads", cond.when, len(cond.downloads))
            manifest.downloads.extend(cond.downloads)
        else:
            log.info("Condition '%s' is false: adding %d else_downloads", cond.when, len(cond.else_downloads))
            manifest.downloads.extend(cond.else_downloads)

    # Clear conditional_downloads after resolution
    manifest.conditional_downloads = []


def _is_url(source: str) -> bool:
    """Check if a source string looks like an HTTP(S) URL."""
    return source.startswith("http://") or source.startswith("https://")


def resolve_manifest_source(source: str, cache_path: str = _DEFAULT_MANIFEST_CACHE, label: str = "manifest") -> str:
    """Resolve a manifest source (file path or URL) to a local file path.

    If *source* is a URL, download it to *cache_path* and return that path.
    If *source* is already a local path, return it unchanged.

    *label* is used in log messages (e.g. "manifest", "script").

    Raises ``RuntimeError`` on download failure so the caller's retry loop
    can re-attempt.
    """
    if not _is_url(source):
        return source

    log.info("Downloading %s from %s", label, source)
    try:
        req = urllib.request.Request(source, headers={"User-Agent": "provisioner/1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        raise RuntimeError(f"Failed to download {label} from {source}: {e}") from e

    # Write atomically: temp file + rename to avoid partial reads
    cache_dir = os.path.dirname(cache_path) or "."
    try:
        fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".yaml")
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, cache_path)
    except OSError as e:
        raise RuntimeError(f"Failed to write {label} to {cache_path}: {e}") from e

    log.info("%s saved to %s (%d bytes)", label.capitalize(), cache_path, len(data))
    return cache_path


def _parse_env_flat_list(value: str) -> list[str]:
    """Parse a semicolon-separated flat list, stripping whitespace and skipping empties/comments."""
    items = []
    for raw in value.split(";"):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        items.append(raw)
    return items


def _repo_dest_from_url(url: str) -> str:
    """Derive a default clone destination from a git URL.

    ``https://github.com/org/repo`` → ``${WORKSPACE:-/workspace}/repo``
    ``https://github.com/org/repo.git`` → ``${WORKSPACE:-/workspace}/repo``
    """
    import urllib.parse as up
    name = os.path.basename(up.urlparse(url).path)
    if name.endswith(".git"):
        name = name[:-4]
    workspace = os.environ.get("WORKSPACE", "/workspace")
    return f"{workspace}/{name}" if name else ""


def _parse_env_git_repos(value: str) -> list[GitRepo]:
    """Parse semicolon-separated 'url|dest|ref' entries (dest and ref optional).

    When *dest* is omitted the repo is cloned into
    ``${WORKSPACE:-/workspace}/{repo_name}``.
    """
    repos = []
    for raw in value.split(";"):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("|")
        url = parts[0].strip()
        dest = parts[1].strip() if len(parts) > 1 else ""
        ref = parts[2].strip() if len(parts) > 2 else ""
        if url:
            if not dest:
                dest = _repo_dest_from_url(url)
            repos.append(GitRepo(url=url, dest=dest, ref=ref))
    return repos


# Map of PROVISIONING_* env vars to manifest target fields
_ENV_CONVENTIONS = {
    "PROVISIONING_DOWNLOADS": "downloads",
    "PROVISIONING_GIT_REPOS": "git_repos",
    "PROVISIONING_APT": "apt_packages",
    "PROVISIONING_PIP": "pip_packages",
    "PROVISIONING_CONDA": "conda_packages",
    "PROVISIONING_POST_COMMANDS": "post_commands",
}


def apply_env_conventions(manifest: Manifest) -> None:
    """Inject resources from PROVISIONING_* convention env vars into the manifest.

    Each env var maps to a specific manifest field.  Values are parsed
    according to the field type and appended to existing entries.
    """
    for env_var, target in _ENV_CONVENTIONS.items():
        value = os.environ.get(env_var, "")
        if not value:
            continue

        if target == "downloads":
            entries = _parse_env_merge_entries(value)
            if entries:
                log.info("env convention: added %d downloads from $%s", len(entries), env_var)
                manifest.downloads.extend(entries)

        elif target == "git_repos":
            repos = _parse_env_git_repos(value)
            if repos:
                log.info("env convention: added %d git repos from $%s", len(repos), env_var)
                manifest.git_repos.extend(repos)

        elif target == "apt_packages":
            pkgs = _parse_env_flat_list(value)
            if pkgs:
                log.info("env convention: added %d apt packages from $%s", len(pkgs), env_var)
                manifest.apt_packages.extend(pkgs)

        elif target == "pip_packages":
            pkgs = _parse_env_flat_list(value)
            if pkgs:
                log.info("env convention: added %d pip packages from $%s", len(pkgs), env_var)
                manifest.pip_packages.append(PipPackages(packages=pkgs))

        elif target == "conda_packages":
            pkgs = _parse_env_flat_list(value)
            if pkgs:
                log.info("env convention: added %d conda packages from $%s", len(pkgs), env_var)
                manifest.conda_packages.append(CondaPackages(packages=pkgs))

        elif target == "post_commands":
            cmds = _parse_env_flat_list(value)
            if cmds:
                log.info("env convention: added %d post commands from $%s", len(cmds), env_var)
                manifest.post_commands.extend(cmds)


def load_manifest(path: str) -> Manifest:
    """Load a YAML manifest file, expand env vars, and validate.

    Returns a fully populated Manifest dataclass.
    """
    log.info("Loading manifest: %s", path)

    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty manifest: {path}")

    # Expand env vars in all string values before validation
    expanded = _expand_recursive(raw)

    return validate_manifest(expanded)
