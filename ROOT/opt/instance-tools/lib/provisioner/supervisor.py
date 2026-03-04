"""Supervisor service registration.

Generates startup scripts and .conf files matching the conventions
used by existing services in this image.
"""

from __future__ import annotations

import logging
import os
import subprocess

from .schema import Service

log = logging.getLogger("provisioner")

_STARTUP_SCRIPT_TEMPLATE = """\
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${{utils}}/logging.sh"
. "${{utils}}/cleanup_generic.sh"
. "${{utils}}/environment.sh"
{exit_serverless}{exit_portal}
# Activate virtual environment
. {venv}/bin/activate

{wait_block}# Set environment variables
{env_exports}

# Launch application
cd {workdir}
{pre_commands}{command}
"""

_SUPERVISOR_CONF_TEMPLATE = """\
[program:{name}]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/{name}.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
"""


def _generate_startup_script(service: Service) -> str:
    """Generate the startup script content for a service."""
    # Conditional exit scripts
    exit_serverless = ""
    if service.skip_on_serverless:
        exit_serverless = '. "${utils}/exit_serverless.sh"\n'

    exit_portal = ""
    if service.portal_search_term:
        exit_portal = f'. "${{utils}}/exit_portal.sh" "{service.portal_search_term}"\n'

    # Wait for provisioning block
    wait_block = ""
    if service.wait_for_provisioning:
        wait_block = (
            '# Wait for provisioning to complete\n'
            'while [ -f "/.provisioning" ]; do\n'
            '    echo "$PROC_NAME startup paused until instance provisioning '
            'has completed (/.provisioning present)"\n'
            '    sleep 5\n'
            'done\n\n'
        )

    # Environment variable exports
    env_lines = []
    for key, value in service.environment.items():
        env_lines.append(f'export {key}="{value}"')
    env_exports = "\n".join(env_lines) if env_lines else "# (none)"

    # Pre-launch commands
    pre_commands = ""
    if service.pre_commands:
        pre_commands = "\n".join(service.pre_commands) + "\n"

    return _STARTUP_SCRIPT_TEMPLATE.format(
        venv=service.venv,
        exit_serverless=exit_serverless,
        exit_portal=exit_portal,
        wait_block=wait_block,
        env_exports=env_exports,
        pre_commands=pre_commands,
        workdir=service.workdir,
        command=service.command,
        name=service.name,
    )


def _generate_supervisor_conf(service: Service) -> str:
    """Generate the supervisor .conf file content."""
    return _SUPERVISOR_CONF_TEMPLATE.format(name=service.name)


def register_services(services: list[Service], dry_run: bool = False) -> None:
    """Register supervisor services by generating scripts and configs.

    For each service:
    1. Writes startup script to /opt/supervisor-scripts/{name}.sh
    2. Writes supervisor config to /etc/supervisor/conf.d/{name}.conf
    3. Runs supervisorctl reread && update
    """
    if not services:
        log.info("No supervisor services to register")
        return

    for service in services:
        script_path = f"/opt/supervisor-scripts/{service.name}.sh"
        conf_path = f"/etc/supervisor/conf.d/{service.name}.conf"

        script_content = _generate_startup_script(service)
        conf_content = _generate_supervisor_conf(service)

        if dry_run:
            log.info("[DRY RUN] Would write startup script: %s", script_path)
            log.info("[DRY RUN] Script content:\n%s", script_content)
            log.info("[DRY RUN] Would write supervisor config: %s", conf_path)
            log.info("[DRY RUN] Config content:\n%s", conf_content)
            continue

        # Write startup script
        os.makedirs(os.path.dirname(script_path), exist_ok=True)
        with open(script_path, "w") as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)
        log.info("Wrote startup script: %s", script_path)

        # Write supervisor config
        os.makedirs(os.path.dirname(conf_path), exist_ok=True)
        with open(conf_path, "w") as f:
            f.write(conf_content)
        log.info("Wrote supervisor config: %s", conf_path)

    if not dry_run:
        log.info("Reloading supervisor configuration")
        subprocess.run(
            ["supervisorctl", "reread"],
            check=False,
        )
        subprocess.run(
            ["supervisorctl", "update"],
            check=False,
        )
        log.info("Supervisor services registered")
