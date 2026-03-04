"""Dataclasses for the provisioner manifest schema."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrySettings:
    max_attempts: int = 5
    initial_delay: int = 2
    backoff_multiplier: int = 2


@dataclass
class ConcurrencySettings:
    hf_downloads: int = 3
    wget_downloads: int = 5


@dataclass
class Settings:
    workspace: str = "/workspace"
    venv: str = "/venv/main"
    log_file: str = "/var/log/portal/provisioning.log"
    concurrency: ConcurrencySettings = field(default_factory=ConcurrencySettings)
    retry: RetrySettings = field(default_factory=RetrySettings)


@dataclass
class AuthProvider:
    token_env: str = ""


@dataclass
class Auth:
    huggingface: AuthProvider = field(default_factory=lambda: AuthProvider(token_env="HF_TOKEN"))
    civitai: AuthProvider = field(default_factory=lambda: AuthProvider(token_env="CIVITAI_TOKEN"))


@dataclass
class PipPackages:
    tool: str = "uv"
    packages: list[str] = field(default_factory=list)
    args: str = ""
    requirements: list[str] = field(default_factory=list)
    venv: str = ""
    python: str = ""


@dataclass
class CondaPackages:
    packages: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    args: str = ""
    env: str = ""
    python: str = ""


@dataclass
class GitRepo:
    url: str = ""
    dest: str = ""
    ref: str = ""
    recursive: bool = True
    pull_if_exists: bool = False
    post_commands: list[str] = field(default_factory=list)


@dataclass
class DownloadEntry:
    url: str = ""
    dest: str = ""


@dataclass
class ConditionalDownload:
    when: str = ""
    downloads: list[DownloadEntry] = field(default_factory=list)
    else_downloads: list[DownloadEntry] = field(default_factory=list)


@dataclass
class ServiceEnvVar:
    name: str = ""
    value: str = ""


@dataclass
class Service:
    name: str = ""
    portal_search_term: str = ""
    skip_on_serverless: bool = True
    venv: str = "/venv/main"
    workdir: str = ""
    command: str = ""
    pre_commands: list[str] = field(default_factory=list)
    wait_for_provisioning: bool = True
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class FileWrite:
    path: str = ""
    content: str = ""
    permissions: str = "0644"
    owner: str = ""


@dataclass
class OnFailure:
    action: str = "continue"
    max_retries: int = 3
    webhook: str = ""


@dataclass
class Manifest:
    version: int = 1
    settings: Settings = field(default_factory=Settings)
    auth: Auth = field(default_factory=Auth)
    write_files: list[FileWrite] = field(default_factory=list)
    apt_packages: list[str] = field(default_factory=list)
    pip_packages: list[PipPackages] = field(default_factory=list)
    conda_packages: CondaPackages = field(default_factory=CondaPackages)
    git_repos: list[GitRepo] = field(default_factory=list)
    downloads: list[DownloadEntry] = field(default_factory=list)
    conditional_downloads: list[ConditionalDownload] = field(default_factory=list)
    env_merge: dict[str, str] = field(default_factory=dict)
    services: list[Service] = field(default_factory=list)
    write_files_late: list[FileWrite] = field(default_factory=list)
    post_commands: list[str] = field(default_factory=list)
    on_failure: OnFailure = field(default_factory=OnFailure)


def _build_nested(cls, data):
    """Recursively build a dataclass from a dict, ignoring unknown keys."""
    if data is None:
        return cls()
    if not isinstance(data, dict):
        return data

    import dataclasses
    import typing

    # Resolve string annotations to actual types
    hints = typing.get_type_hints(cls)

    filtered = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        resolved_type = hints.get(f.name, f.type)
        origin = getattr(resolved_type, "__origin__", None)

        if hasattr(resolved_type, "__dataclass_fields__"):
            filtered[f.name] = _build_nested(resolved_type, v)
        elif origin is list and isinstance(v, list):
            args = getattr(resolved_type, "__args__", ())
            if args and hasattr(args[0], "__dataclass_fields__"):
                filtered[f.name] = [_build_nested(args[0], item) for item in v]
            else:
                filtered[f.name] = v
        else:
            filtered[f.name] = v
    return cls(**filtered)


def validate_manifest(data: dict) -> Manifest:
    """Validate and convert a raw dict into a Manifest dataclass.

    Raises ValueError for critical schema violations.
    """
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a YAML mapping")

    version = data.get("version")
    if version != 1:
        raise ValueError(f"Unsupported manifest version: {version} (expected 1)")

    # Backward compat: wrap single pip_packages dict in a list
    pip = data.get("pip_packages")
    if isinstance(pip, dict):
        data = dict(data, pip_packages=[pip])

    return _build_nested(Manifest, data)
