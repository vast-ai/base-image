"""Token validation for HuggingFace and CivitAI."""

from __future__ import annotations

import logging
import os
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

log = logging.getLogger("provisioner")


def validate_hf_token(token_env: str = "HF_TOKEN") -> bool:
    """Check if the HuggingFace token is valid via the whoami API.

    Returns True if valid, False otherwise.
    """
    token = os.environ.get(token_env, "")
    if not token:
        log.info("No HuggingFace token found ($%s not set)", token_env)
        return False

    try:
        req = Request(
            "https://huggingface.co/api/whoami-v2",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                log.info("HuggingFace token is valid")
                return True
    except (HTTPError, URLError, OSError) as e:
        log.warning("HuggingFace token validation failed: %s", e)

    log.warning("HuggingFace token is invalid")
    return False


def validate_civitai_token(token_env: str = "CIVITAI_TOKEN") -> bool:
    """Check if the CivitAI token is valid.

    Returns True if valid, False otherwise.
    """
    token = os.environ.get(token_env, "")
    if not token:
        log.info("No CivitAI token found ($%s not set)", token_env)
        return False

    try:
        req = Request(
            "https://civitai.com/api/v1/models?hidden=1&limit=1",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                log.info("CivitAI token is valid")
                return True
    except (HTTPError, URLError, OSError) as e:
        log.warning("CivitAI token validation failed: %s", e)

    log.warning("CivitAI token is invalid")
    return False
