"""
Data model for a Vast.ai template.

Models only the fields the create flow sends to ``POST /template/``. Unknown
keys in a ``template.yml`` are rejected (``extra="forbid"``) so a typo'd or
attacker-supplied field surfaces as an error instead of being forwarded to the
API verbatim.
"""
import json
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator


class VastTemplate(BaseModel):
    """A Vast.ai template to create. Maps to the POST /template/ request body."""

    model_config = ConfigDict(extra="forbid")

    # Required
    name: str

    # Optional create fields
    image: Optional[str] = None
    tag: Optional[str] = None
    href: Optional[str] = None
    repo: Optional[str] = None
    env: Optional[str] = None  # Docker arg string: "-e KEY=VALUE -p 8080:8080"
    ports: Optional[List[str]] = None  # load-only: folded into env, never POSTed
    onstart: Optional[str] = None
    jup_direct: bool = False
    ssh_direct: bool = False
    use_jupyter_lab: bool = False
    runtype: str = "args"
    use_ssh: bool = False
    jupyter_dir: Optional[str] = None
    docker_login_repo: Optional[str] = ""
    docker_login_user: Optional[str] = ""
    docker_login_pass: Optional[str] = ""
    extra_filters: Optional[Union[str, Dict[str, Any]]] = None  # dict from config, JSON string passthrough
    recommended_disk_space: Optional[float] = None
    readme: Optional[str] = None
    readme_visible: bool = True
    desc: Optional[str] = None
    private: bool = True
    vm: Optional[bool] = None         # only sent when explicitly set (VM templates)
    autoscaler: Optional[bool] = None  # only sent when explicitly set

    @field_validator('extra_filters', mode='before')
    @classmethod
    def parse_extra_filters(cls, v):
        """extra_filters may be a JSON string or a dict; normalise to a dict."""
        if v is None or isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v  # leave malformed JSON as-is for the API to reject
        return v

    def to_api_dict(self) -> Dict[str, Any]:
        """Build the POST /template/ payload.

        ``ports`` is load-only (already folded into ``env``). ``vm`` and
        ``autoscaler`` are only sent when explicitly set, so a plain template
        does not pin them.
        """
        exclude_fields = {'ports'}
        if self.vm is None:
            exclude_fields.add('vm')
        if self.autoscaler is None:
            exclude_fields.add('autoscaler')
        return self.model_dump(exclude_none=False, exclude=exclude_fields)
