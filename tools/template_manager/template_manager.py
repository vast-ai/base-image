"""
Template Manager for Vast.ai API interactions — create templates from local YAML.

Synchronous stdlib client (urllib): the create flow is a serial, self-paced loop
with no concurrency, so it carries no async/httpx machinery. Matches the stdlib
networking already used by test_template.py in this directory. See ADR 0008.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

import yaml

from models import VastTemplate


def _redact_secrets(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a payload with secret-bearing values masked.

    Used for dry-run/log output so a docker registry password (or any
    ``*_pass``/``*_token``/``*_key`` field) is never printed in the clear.
    """
    redacted = dict(payload)
    for key, value in redacted.items():
        if value and key.lower().endswith(("_pass", "_token", "_key", "_secret")):
            redacted[key] = "***redacted***"
    return redacted


class TemplateManager:
    """Manages Vast.ai template creation over the console API."""

    BASE_URL = "https://console.vast.ai/api/v0"
    TIMEOUT = 30.0

    def __init__(self, api_key: str):
        # Auth via header, never as a URL query param — keeps the key out of
        # access logs, proxies, error output and process listings.
        self._auth_header = f"Bearer {api_key}"
        self._user_id: Optional[int] = None

    # No persistent connection to close; the context manager is kept so call
    # sites read the same as a pooled client and can gain teardown later.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def _open(self, method: str, url: str, payload: Optional[Dict[str, Any]] = None):
        """Issue one JSON request and return the parsed response.

        Raises urllib.error.HTTPError on non-2xx and URLError/TimeoutError on
        transport faults — the caller decides what is retryable.
        """
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth_header)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
            body = resp.read().decode()
        return json.loads(body) if body else {}

    @staticmethod
    def build_env_string_from_lists(
        ports: Optional[List[str]] = None,
        env_vars: Optional[Union[List[str], Dict[str, str]]] = None
    ) -> str:
        """Build a Docker env string from the *raw YAML config* shape.

        Use this when reading ports and env from a template.yml on disk:
        ports are bare ``"8080:8080"`` strings (no ``-p`` prefix), env vars
        are either ``["KEY=VAL", ...]`` or ``{"KEY": "VAL"}``.

        Args:
            ports: List of port mappings like ``["8080:8080", "1111:1111"]``
            env_vars: List ``["KEY=VAL"]`` or dict ``{"KEY": "VAL"}``

        Returns:
            Complete env string
        """
        parts = []

        # Add port mappings
        if ports:
            for port in ports:
                parts.append(f'-p {port}')

        # Add environment variables
        if env_vars:
            if isinstance(env_vars, dict):
                # Dict format: {"KEY": "VALUE"}
                for key, value in env_vars.items():
                    escaped_value = str(value).replace('"', '\\"')
                    parts.append(f'-e {key}="{escaped_value}"')
            elif isinstance(env_vars, list):
                # List format: ["KEY=VALUE", "KEY2=VALUE2"]
                for env_item in env_vars:
                    if '=' in env_item:
                        key, value = env_item.split('=', 1)
                        escaped_value = value.replace('"', '\\"')
                        parts.append(f'-e {key}="{escaped_value}"')

        return ' '.join(parts)

    def get_user_id(self) -> int:
        """Get the current user's ID, cached after first call"""
        if self._user_id is not None:
            return self._user_id

        print(f"    [Fetching] Current user info")
        time.sleep(1)

        data = self._open("GET", f"{self.BASE_URL}/users/current/")
        user_id: int = data['id']
        self._user_id = user_id

        print(f"    [Cached] User ID: {user_id}")
        return user_id

    @staticmethod
    def build_referral_url(user_id: int, template_name: str) -> str:
        """Build a self-referral URL for the template"""
        encoded_name = urllib.parse.quote(template_name)
        return f"https://cloud.vast.ai/?ref_id={user_id}&creator_id={user_id}&name={encoded_name}"

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        payload: Dict[str, Any],
        label: str,
        max_retries: int = 10,
    ) -> Dict[str, Any]:
        """Send an HTTP request with exponential backoff on rate limits.

        Args:
            method: HTTP method ("POST" or "PUT").
            url: Request URL.
            payload: JSON body.
            label: Human-readable label for log messages (e.g. template name).
            max_retries: Maximum retry attempts.

        Returns:
            Parsed JSON response. If the response contains a ``template`` key,
            that nested dict is returned directly.
        """
        base_delay = 2.0
        retry_count = 0

        while retry_count <= max_retries:
            if retry_count > 0:
                delay = min(base_delay * (2 ** (retry_count - 1)), 60)
                print(f"    [Retry {retry_count}/{max_retries}] Waiting {delay:.1f}s before retry...")
                time.sleep(delay)
            else:
                time.sleep(base_delay)

            try:
                result = self._open(method, url, payload)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_count += 1
                    retry_after = e.headers.get('Retry-After', 'unknown')
                    print(f"    [Rate limited] HTTP 429. Retry-After: {retry_after}s")
                    if retry_count > max_retries:
                        print(f"    [Max retries] Reached {max_retries} attempts. Giving up.")
                        raise
                    if retry_after != 'unknown':
                        try:
                            wait_time = float(retry_after)
                            print(f"    [Waiting] {wait_time}s as specified by server...")
                            time.sleep(wait_time)
                        except ValueError:
                            pass
                    continue
                # Any other status error is terminal — surface it, don't loop.
                try:
                    body = e.read().decode(errors="replace")
                except Exception:
                    body = "(unable to read response body)"
                print(f"    [Error] HTTP {e.code}: {body}")
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                # Transient transport/timeout faults only: retry with backoff.
                # Other exceptions (e.g. JSON decode, bugs) propagate immediately.
                retry_count += 1
                if retry_count > max_retries:
                    raise
                delay = min(base_delay * (2 ** (retry_count - 1)), 60)
                print(f"    [Transport error] {type(e).__name__}")
                print(f"    [Retry {retry_count}/{max_retries}] Waiting {delay:.1f}s...")
                time.sleep(delay)
                continue

            if retry_count > 0:
                print(f"    [Success] After {retry_count} retr{'y' if retry_count == 1 else 'ies'}")

            if isinstance(result, dict) and 'template' in result:
                return result['template']
            return result

        raise Exception(f"Failed {method} for {label} after {max_retries} retries")

    def create_template(self, template: VastTemplate, max_retries: int = 10) -> Dict[str, Any]:
        """
        Create a new template via API with exponential backoff for rate limiting

        Args:
            template: Template to create
            max_retries: Maximum number of retry attempts

        Returns:
            Template data from API response
        """
        print(f"    [Creating] Template: {template.name}")

        return self._request_with_retry(
            "POST", f"{self.BASE_URL}/template/", payload=template.to_api_dict(),
            label=template.name, max_retries=max_retries,
        )

    def create_or_preview(
        self,
        template: VastTemplate,
        dry_run: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Create a template via API, or preview the payload in dry-run mode.

        Always creates new templates via POST. Updates are disabled due to:
        - HOST-2493: PUT changes hash_id, breaking existing links
        - CLN-927: hash_id excludes creator_id, making hash-based matching
          unreliable across creators

        Args:
            template: Template to create
            dry_run: If True, print payload without calling API

        Returns:
            Result dict with hash_id/id/name on creation, None on dry-run.
        """
        payload = template.to_api_dict()
        if dry_run:
            print(f"    [DRY RUN] [CREATE] {template.name}")
            print(json.dumps(_redact_secrets(payload), indent=2))
            return None

        result = self.create_template(template)
        rid = result.get("hash_id", "N/A")
        tid = result.get("id", "N/A")
        name = result.get("name", template.name)
        print(f"    [Created] {name}  hash_id={rid}  id={tid}")
        result["_action"] = "created"
        return result

    def delete_template(self, template_id: int) -> Dict[str, Any]:
        """Delete a template by its numeric ID (DELETE /template/ {template_id}).

        Scoped teardown for a throwaway QA template. ``template_id`` is the
        numeric ``id``, not the ``hash_id``.
        """
        print(f"    [Deleting] Template id={template_id}")
        # Route through the retry/backoff path so a single 429 on teardown doesn't
        # leak the throwaway template (the reaper only sweeps instances, not templates).
        result = self._request_with_retry(
            "DELETE", f"{self.BASE_URL}/template/",
            payload={"template_id": template_id}, label=f"delete template {template_id}")
        print(f"    [Deleted] Template id={template_id}")
        return result

    @staticmethod
    def load_templates_from_yaml(yaml_path: Path) -> List[VastTemplate]:
        """Load and validate one or more VastTemplates from a YAML file."""
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)

        if data is None:
            raise ValueError(f"Empty YAML file: {yaml_path}")

        entries = data if isinstance(data, list) else [data]

        templates = []
        for i, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                raise ValueError(f"Entry {i} in {yaml_path} is not a mapping")

            # Fold list/dict ports+env into the Docker arg string
            ports_list = entry.pop('ports', None)
            env_value = entry.pop('env', None)

            if ports_list or (env_value and isinstance(env_value, (list, dict))):
                if isinstance(env_value, str) and ports_list:
                    # Mixed: ports as list + env as string — prepend port flags
                    port_flags = ' '.join(f'-p {p}' for p in ports_list)
                    entry['env'] = f"{port_flags} {env_value}" if env_value else port_flags
                else:
                    entry['env'] = TemplateManager.build_env_string_from_lists(
                        ports=ports_list, env_vars=env_value
                    )
            elif env_value is not None:
                entry['env'] = env_value  # already a Docker arg string

            templates.append(VastTemplate(**entry))

        return templates
