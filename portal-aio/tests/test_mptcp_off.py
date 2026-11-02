"""mptcp_off: the portal-release route to plain-TCP Caddy and Syncthing (ADR 0061).

The fake binary echoes its GODEBUG and arguments, so the wrapper is checked by
running it, not by reading it.
"""

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "caddy_manager"))

import mptcp_off  # noqa: E402

LAUNCH = "#!/bin/bash\n/opt/portal-aio/caddy_manager/caddy run\n"


def _fake(tmp_path, name="caddy", script_text=LAUNCH):
    target = tmp_path / name
    target.write_text('#!/bin/sh\necho "GODEBUG=$GODEBUG ARGS=$*"\n')
    target.chmod(0o755)
    script = tmp_path / f"{name}.sh"
    script.write_text(script_text)
    return target, script


def _run(path, env_godebug=None):
    env = {"PATH": "/usr/bin:/bin"}
    if env_godebug is not None:
        env["GODEBUG"] = env_godebug
    return subprocess.run([str(path), "run", "--x=1"], capture_output=True, text=True, env=env).stdout.strip()


def test_wraps_and_keeps_original(tmp_path):
    target, script = _fake(tmp_path)
    status = mptcp_off.wrap(str(target), str(script))
    assert status.startswith("installed"), status
    assert _run(target) == "GODEBUG=multipathtcp=0 ARGS=run --x=1"
    assert _run(tmp_path / ".mptcp_off" / "caddy") == "GODEBUG= ARGS=run --x=1"


def test_running_program_keeps_its_name(tmp_path):
    """Older images' tests find Caddy by name (pgrep -x caddy, `(^|/)caddy run`)."""
    target, script = _fake(tmp_path)
    target.write_text('#!/bin/sh\necho "$0"\n')
    mptcp_off.wrap(str(target), str(script))
    assert os.path.basename(subprocess.run([str(target)], capture_output=True, text=True).stdout.strip()) == "caddy"


def test_keeps_user_godebug_and_puts_ours_last(tmp_path):
    target, script = _fake(tmp_path)
    mptcp_off.wrap(str(target), str(script))
    assert _run(target, "http2debug=1,multipathtcp=1") == \
        "GODEBUG=http2debug=1,multipathtcp=1,multipathtcp=0 ARGS=run --x=1"


def test_second_wrap_is_noop_and_never_wraps_the_wrapper(tmp_path):
    target, script = _fake(tmp_path)
    mptcp_off.wrap(str(target), str(script))
    assert mptcp_off.wrap(str(target), str(script)).startswith("ok")
    assert _run(tmp_path / ".mptcp_off" / "caddy") == "GODEBUG= ARGS=run --x=1"


def test_image_with_the_fix_is_left_alone(tmp_path):
    target, script = _fake(
        tmp_path, script_text='#!/bin/bash\nexport GODEBUG="${GODEBUG:+${GODEBUG},}multipathtcp=0"\n')
    assert mptcp_off.wrap(str(target), str(script)).startswith("ok")
    assert not (tmp_path / ".mptcp_off" / "caddy").exists()
    assert _run(target) == "GODEBUG= ARGS=run --x=1"


def test_absent_binary_is_a_skip(tmp_path):
    assert mptcp_off.wrap(str(tmp_path / "absent"), str(tmp_path / "absent.sh")).startswith("skip")


def test_one_failure_does_not_stop_the_other(tmp_path, monkeypatch, capsys):
    caddy, caddy_sh = _fake(tmp_path, "caddy")
    st, st_sh = _fake(tmp_path, "syncthing")
    real = mptcp_off.wrap

    def flaky(target, script):
        if target == str(caddy):
            raise RuntimeError("boom")
        return real(target, script)

    monkeypatch.setattr(mptcp_off, "wrap", flaky)
    mptcp_off.apply((("Caddy", str(caddy), str(caddy_sh)), ("Syncthing", str(st), str(st_sh))))
    assert "Caddy: skip: boom" in capsys.readouterr().out
    assert _run(st) == "GODEBUG=multipathtcp=0 ARGS=run --x=1"


def test_symlinked_target_is_skipped_and_still_runs(tmp_path):
    """Hard-linking a relative symlink into .mptcp_off/ would leave a wrapper that cannot run."""
    real, script = _fake(tmp_path, "real")
    (tmp_path / "caddy").symlink_to("real")
    assert mptcp_off.wrap(str(tmp_path / "caddy"), str(script)).startswith("skip")
    assert _run(tmp_path / "caddy") == "GODEBUG= ARGS=run --x=1"


def test_planted_orig_dir_symlink_is_refused(tmp_path):
    """Root wraps Syncthing inside a directory `user` owns; never follow a planted symlink."""
    target, script = _fake(tmp_path)
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    (victim_dir / "caddy").write_text("keep")
    (tmp_path / ".mptcp_off").symlink_to(victim_dir)
    assert mptcp_off.wrap(str(target), str(script)).startswith("skip")
    assert (victim_dir / "caddy").read_text() == "keep"
    assert _run(target) == "GODEBUG= ARGS=run --x=1"


def test_a_comment_mentioning_the_setting_does_not_skip_the_wrap(tmp_path):
    target, script = _fake(tmp_path, script_text="#!/bin/bash\n# TODO: multipathtcp=0\n" + LAUNCH)
    assert mptcp_off.wrap(str(target), str(script)).startswith("installed")


def test_config_manager_wraps_before_it_publishes_portal_yaml(monkeypatch):
    """Syncthing starts as soon as /etc/portal.yaml exists; wrapping after that is too late."""
    import types
    import caddy_config_manager as ccm

    calls = []
    monkeypatch.setitem(sys.modules, "mptcp_off", types.SimpleNamespace(apply=lambda: calls.append("wrap")))

    def load_config():
        calls.append("publish")
        raise RuntimeError("stop here")

    monkeypatch.setattr(ccm, "load_config", load_config)
    ccm.main()
    assert calls == ["wrap", "publish"]
