"""Pydantic models for the capability routes.

These exist mainly to make ``/openapi.json`` self-describing for agents. The
full ``/capabilities`` manifest is returned as a free-form object (it is
intentionally extensible via fragments), but the actionable, stable-shaped
pieces — the services list, the OpenAI-endpoint rollup, and the provision
request body — are typed here.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AuthDescriptor(BaseModel):
    type: str = "bearer"
    token_env: List[str] = Field(default_factory=lambda: ["OPEN_BUTTON_TOKEN", "WEB_PASSWORD"])


class OpenAIEndpoint(BaseModel):
    service: str = Field(..., description="Portal service label this endpoint belongs to")
    base_url: Optional[str] = Field(
        None, description="Externally callable OpenAI base, e.g. https://<ip>:<mapped_port>/v1"
    )
    internal_base_url: Optional[str] = Field(
        None, description="In-container base reachable without auth"
    )
    capabilities: List[str] = Field(default_factory=list)
    models_path: Optional[str] = None
    auth: AuthDescriptor = Field(default_factory=AuthDescriptor)


class ServiceInfo(BaseModel):
    name: Optional[str] = None
    hostname: Optional[str] = None
    internal_port: Optional[int] = None
    external_port: Optional[int] = None
    open_path: Optional[str] = None
    mapped_port: Optional[str] = Field(
        None, description="Host-reachable port (VAST_TCP_PORT_<external_port>)"
    )
    internal_url: Optional[str] = None
    direct_url: Optional[str] = Field(
        None, description="Externally reachable URL via PUBLIC_IPADDR + mapped_port"
    )
    supervisor_process: Optional[str] = None
    state: Optional[str] = Field(None, description="RUNNING / STOPPED / unknown")
    openai_v1_base: Optional[str] = None
    capabilities: Optional[List[str]] = None


class ProvisionRequest(BaseModel):
    """Ask the provisioner to install more dependencies at runtime."""

    manifest_url: Optional[str] = Field(None, description="URL of a provisioning YAML manifest")
    inline_yaml: Optional[str] = Field(None, description="Inline provisioning YAML manifest")
    pip: Optional[List[str]] = Field(None, description="pip packages to install into /venv/main")
    git_repos: Optional[List[str]] = Field(None, description="git repo URLs to clone")
    downloads: Optional[List[str]] = Field(None, description="file URLs to download")


class ProvisionResponse(BaseModel):
    status: str
    detail: Optional[str] = None
    log_file: str = "/var/log/portal/provisioning.log"
