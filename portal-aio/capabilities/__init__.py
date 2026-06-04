"""Machine-readable capability surface for the instance.

This package is the single owner of the logic that describes what an instance
can do for an AI agent: installed tools, python environments, hardware, running
services (with their externally-reachable URLs and OpenAI ``/v1`` endpoints),
how to provision more, and the auth model.

It is deliberately import-clean: it never imports the portal app, so it can be
called both from the live portal endpoint (which injects freshly-collected
supervisor/metrics data) and from the boot script / ``vast-capabilities`` CLI
to write a static snapshot without a running web server.
"""

from .manifest import (
    SCHEMA_VERSION,
    assemble,
    assemble_live,
    assemble_static,
    load_fragments,
    parse_portal_config,
)

__all__ = [
    "SCHEMA_VERSION",
    "assemble",
    "assemble_live",
    "assemble_static",
    "load_fragments",
    "parse_portal_config",
]
