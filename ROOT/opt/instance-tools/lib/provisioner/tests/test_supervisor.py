"""Tests for provisioner.supervisor -- startup script and conf generation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from provisioner.schema import Service
from provisioner.supervisor import (
    _generate_startup_script,
    _generate_supervisor_conf,
    register_services,
)


class TestGenerateStartupScript:
    @pytest.fixture
    def basic_service(self):
        return Service(
            name="my-app",
            portal_search_term="My App",
            skip_on_serverless=True,
            venv="/venv/main",
            workdir="/workspace/my-app",
            command="python app.py --port 7860",
            wait_for_provisioning=True,
            environment={"GRADIO_SERVER_NAME": "127.0.0.1"},
        )

    def test_contains_shebang(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert script.startswith("#!/bin/bash")

    def test_sources_utils(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert 'logging.sh' in script
        assert 'cleanup_generic.sh' in script
        assert 'environment.sh' in script

    def test_exit_serverless(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert 'exit_serverless.sh' in script

    def test_no_exit_serverless(self):
        svc = Service(name="a", command="echo", workdir="/", skip_on_serverless=False)
        script = _generate_startup_script(svc)
        assert 'exit_serverless.sh' not in script

    def test_exit_portal(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert 'exit_portal.sh' in script
        assert '"My App"' in script

    def test_no_exit_portal(self):
        svc = Service(name="a", command="echo", workdir="/", portal_search_term="")
        script = _generate_startup_script(svc)
        assert 'exit_portal.sh' not in script

    def test_activates_venv(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert '/venv/main/bin/activate' in script

    def test_custom_venv(self):
        svc = Service(name="a", command="echo", workdir="/", venv="/venv/custom")
        script = _generate_startup_script(svc)
        assert '/venv/custom/bin/activate' in script

    def test_wait_for_provisioning(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert '/.provisioning' in script
        assert 'sleep 5' in script

    def test_no_wait_for_provisioning(self):
        svc = Service(name="a", command="echo", workdir="/", wait_for_provisioning=False)
        script = _generate_startup_script(svc)
        assert '/.provisioning' not in script

    def test_environment_exports(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert 'export GRADIO_SERVER_NAME="127.0.0.1"' in script

    def test_multiple_env_vars(self):
        svc = Service(
            name="a", command="echo", workdir="/",
            environment={"A": "1", "B": "2"},
        )
        script = _generate_startup_script(svc)
        assert 'export A="1"' in script
        assert 'export B="2"' in script

    def test_env_var_shell_escaping(self):
        """Values with shell-special characters must be escaped."""
        svc = Service(
            name="a", command="echo", workdir="/",
            environment={
                "TRICKY": 'has "quotes" and $VAR and `cmd` and back\\slash',
            },
        )
        script = _generate_startup_script(svc)
        assert r'export TRICKY="has \"quotes\" and \$VAR and \`cmd\` and back\\slash"' in script

    def test_no_env_vars(self):
        svc = Service(name="a", command="echo", workdir="/")
        script = _generate_startup_script(svc)
        assert "# (none)" in script

    def test_command(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert "python app.py --port 7860" in script

    def test_workdir(self, basic_service):
        script = _generate_startup_script(basic_service)
        assert "cd /workspace/my-app" in script

    def test_pre_commands(self):
        svc = Service(
            name="a",
            command="python app.py",
            workdir="/workspace/app",
            pre_commands=[
                "git config --global safe.directory '*'",
                "ln -sf /models /workspace/app/models",
            ],
        )
        script = _generate_startup_script(svc)
        assert "git config --global safe.directory '*'" in script
        assert "ln -sf /models /workspace/app/models" in script
        # Pre commands should appear before the main command
        pre_pos = script.index("git config")
        cmd_pos = script.index("python app.py")
        assert pre_pos < cmd_pos

    def test_no_pre_commands(self):
        svc = Service(name="a", command="echo hi", workdir="/")
        script = _generate_startup_script(svc)
        # Should not have any leftover pre_commands artifacts
        lines = script.strip().split("\n")
        # The last non-empty line should be the command
        assert lines[-1] == "echo hi"


class TestGenerateSupervisorConf:
    def test_program_section(self):
        svc = Service(name="my-app", command="echo")
        conf = _generate_supervisor_conf(svc)
        assert "[program:my-app]" in conf

    def test_command_path(self):
        svc = Service(name="my-app", command="echo")
        conf = _generate_supervisor_conf(svc)
        assert "command=/opt/supervisor-scripts/my-app.sh" in conf

    def test_autostart(self):
        svc = Service(name="x", command="echo")
        conf = _generate_supervisor_conf(svc)
        assert "autostart=true" in conf

    def test_stdout_to_devstdout(self):
        svc = Service(name="x", command="echo")
        conf = _generate_supervisor_conf(svc)
        assert "stdout_logfile=/dev/stdout" in conf

    def test_proc_name_env(self):
        svc = Service(name="x", command="echo")
        conf = _generate_supervisor_conf(svc)
        assert 'PROC_NAME="%(program_name)s"' in conf

    def test_stop_signal(self):
        svc = Service(name="x", command="echo")
        conf = _generate_supervisor_conf(svc)
        assert "stopsignal=TERM" in conf
        assert "stopasgroup=true" in conf
        assert "killasgroup=true" in conf


class TestRegisterServices:
    def test_empty_list(self):
        register_services([])

    def test_dry_run(self):
        services = [
            Service(
                name="app",
                command="echo hi",
                workdir="/tmp",
                portal_search_term="App",
            )
        ]
        register_services(services, dry_run=True)

    @patch("provisioner.supervisor._assert_registered")
    @patch("provisioner.supervisor._wait_for_supervisord")
    @patch("provisioner.supervisor.run_cmd")
    def test_writes_files(self, mock_run, mock_wait, mock_assert, tmp_path):
        script_dir = tmp_path / "supervisor-scripts"
        conf_dir = tmp_path / "supervisor" / "conf.d"

        services = [
            Service(name="app", command="echo", workdir="/tmp"),
        ]

        with patch(
            "provisioner.supervisor._generate_startup_script",
            return_value="#!/bin/bash\necho test",
        ), patch(
            "provisioner.supervisor._generate_supervisor_conf",
            return_value="[program:app]\n",
        ), patch("builtins.open", create=True) as mock_open, patch(
            "provisioner.supervisor.os.makedirs",
        ), patch(
            "provisioner.supervisor.os.chmod",
        ):
            register_services(services)
            # supervisorctl reread + update
            assert mock_run.call_count == 2

    @patch("provisioner.supervisor._assert_registered")
    @patch("provisioner.supervisor._wait_for_supervisord")
    @patch("provisioner.supervisor.run_cmd")
    @patch("provisioner.supervisor.os.chmod")
    @patch("provisioner.supervisor.os.makedirs")
    def test_writes_correct_paths(self, mock_makedirs, mock_chmod, mock_run,
                                  mock_wait, mock_assert, tmp_path):
        svc = Service(name="test-svc", command="echo", workdir="/tmp")
        written = {}

        def fake_open(path, mode="r"):
            from io import StringIO
            buf = StringIO()
            buf.close = lambda: None
            buf.__enter__ = lambda s: s
            buf.__exit__ = lambda *a: None
            written[path] = buf
            return buf

        with patch("builtins.open", side_effect=fake_open):
            register_services([svc])

        assert "/opt/supervisor-scripts/test-svc.sh" in written
        assert "/etc/supervisor/conf.d/test-svc.conf" in written
        mock_chmod.assert_called_once()


class TestSupervisordReadiness:
    """Registration must not run against a supervisord that cannot hear it.

    Phase 7 lands at boot stage 75, inside the window where supervisord has
    forked (stage 65) but its RPC socket is not yet listening. Every earlier
    phase is a no-op for a services-only manifest, so nothing separates the two.

    The old code ran `reread`/`update` with check=False and discarded the result:
    the conf files were on disk, so it logged "Supervisor services registered",
    the caller wrote the stage hash (so it is never retried), and the boot set
    /.provisioning_complete. A customer's declared service silently never
    started while the instance reported provisioning successful.
    """

    @patch("provisioner.supervisor.subprocess.run")
    def test_a_late_socket_is_waited_for(self, mock_run):
        from provisioner.supervisor import _wait_for_supervisord
        mock_run.side_effect = [
            type("R", (), {"returncode": 4})(),   # ECONNREFUSED / ENOENT
            type("R", (), {"returncode": 4})(),
            type("R", (), {"returncode": 3})(),   # reached it; some not running
        ]
        with patch("provisioner.supervisor.time.sleep"):
            _wait_for_supervisord(timeout=30)
        assert mock_run.call_count == 3

    @patch("provisioner.supervisor.subprocess.run")
    def test_a_socket_that_never_answers_RAISES(self, mock_run):
        """Raising is the point: the caller marks the stage complete on return,
        and a stage marked complete is never retried."""
        from provisioner.supervisor import _wait_for_supervisord
        mock_run.return_value = type("R", (), {"returncode": 4})()
        with patch("provisioner.supervisor.time.sleep"):
            with pytest.raises(RuntimeError, match="did not answer"):
                _wait_for_supervisord(timeout=2)

    @patch("provisioner.supervisor.subprocess.run")
    def test_an_unclassified_error_is_not_treated_as_ready(self, mock_run):
        """Only 0 and 3 mean the socket was reached. Exit 1 is supervisorctl's
        generic error — a readiness probe must not fail OPEN on it."""
        from provisioner.supervisor import _wait_for_supervisord
        mock_run.return_value = type("R", (), {"returncode": 1})()
        with patch("provisioner.supervisor.time.sleep"):
            with pytest.raises(RuntimeError):
                _wait_for_supervisord(timeout=2)

    @patch("provisioner.supervisor.subprocess.run")
    @patch("provisioner.supervisor._wait_for_supervisord")
    @patch("provisioner.supervisor.run_cmd")
    @patch("provisioner.supervisor.os.chmod")
    @patch("provisioner.supervisor.os.makedirs")
    def test_a_service_supervisord_never_picked_up_RAISES(self, _mk, _ch, mock_run,
                                                          _wait, mock_sp, tmp_path):
        """The positive assertion: the conf being on disk is not the same as the
        service existing. This is what the old check=False code skipped — it
        logged success, the caller wrote the stage hash (so it is never retried)
        and the boot set /.provisioning_complete, while the service had never
        started."""
        from provisioner.supervisor import register_services
        mock_run.return_value = type("R", (), {"returncode": 0})()
        mock_sp.return_value = type("R", (), {
            "stdout": "app: ERROR (no such process)", "stderr": ""})()
        with patch("builtins.open", create=True):
            with pytest.raises(RuntimeError, match="did not pick up"):
                register_services([Service(name="app", command="echo", workdir="/tmp")])

    @patch("provisioner.supervisor.subprocess.run")
    @patch("provisioner.supervisor._wait_for_supervisord")
    @patch("provisioner.supervisor.run_cmd")
    @patch("provisioner.supervisor.os.chmod")
    @patch("provisioner.supervisor.os.makedirs")
    def test_someone_elses_broken_conf_does_not_fail_our_phase(self, _mk, _ch, mock_run,
                                                               _wait, mock_sp, tmp_path):
        """`reread` parses EVERY file in conf.d, so CANT_REREAD from a derivative's
        unit or a customer's own write_files is not a statement about our
        services. Failing on it aborted late file writes, post_commands and
        PROVISIONING_SCRIPT for a file unrelated to this manifest."""
        from provisioner.supervisor import register_services
        mock_run.return_value = type("R", (), {"returncode": 1})()   # CANT_REREAD
        mock_sp.return_value = type("R", (), {
            "stdout": "app RUNNING pid 1, uptime 0:00:05", "stderr": ""})()
        with patch("builtins.open", create=True):
            register_services([Service(name="app", command="echo", workdir="/tmp")])


class TestGeneratedConfConventions:
    def test_it_matches_the_hand_written_units(self):
        """autorestart=true restarted a service even on a clean exit 0, so the
        self-skip idiom (`sleep 6; exit 0`) became a permanent restart loop; and
        startsecs=0 marked it RUNNING the instant it forked, so a crash-looping
        app reported RUNNING to supervisorctl, the portal and every health check.
        """
        from provisioner.supervisor import _generate_supervisor_conf
        conf = _generate_supervisor_conf(Service(name="app", command="echo", workdir="/tmp"))
        assert "autorestart=unexpected" in conf
        assert "autorestart=true" not in conf
        assert "startsecs=5" in conf
        assert "startsecs=0" not in conf
