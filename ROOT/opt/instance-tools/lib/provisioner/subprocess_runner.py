"""Subprocess runner that streams output through the provisioner logger."""

from __future__ import annotations

import codecs
import errno
import fcntl
import logging
import os
import re
import select
import struct
import subprocess
import termios
import threading

log = logging.getLogger("provisioner")

# Lock for raw writes from PTY output across parallel threads.
_write_lock = threading.Lock()

# ANSI CSI sequences and bare ESC sequences.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b[^\[[]")

# --- Parallel progress tracker ---
# When active, \r-based progress lines from concurrent run_cmd threads
# are collected here instead of written directly.  A timer thread
# periodically flushes them as a cursor-up block so multiple progress
# bars display cleanly.
_parallel_tracker: "ProgressTracker | None" = None


class ProgressTracker:
    """Coordinates \r progress bars from concurrent download threads."""

    def __init__(self, streams: list[tuple], interval: float = 0.5):
        self._lines: dict[int, str] = {}  # thread_id -> latest progress text
        self._lock = threading.Lock()
        self._streams = streams
        self._interval = interval
        self._prev_count = 0
        self._timer: threading.Timer | None = None
        self._stopped = False

    def start(self) -> None:
        global _parallel_tracker
        _parallel_tracker = self
        self._schedule()

    def stop(self) -> None:
        global _parallel_tracker
        self._stopped = True
        if self._timer:
            self._timer.cancel()
        # Final flush then clear the block from display
        self.flush()
        with _write_lock:
            self.clear_block()
        _parallel_tracker = None

    def update(self, text: str) -> None:
        """Store the latest \r progress line for the calling thread."""
        tid = threading.get_ident()
        with self._lock:
            self._lines[tid] = text

    def remove_thread(self) -> None:
        tid = threading.get_ident()
        with self._lock:
            self._lines.pop(tid, None)

    def clear_block(self) -> None:
        """Erase the progress block from display and reset cursor tracking.

        Must be called (under _write_lock) before any direct write to the
        streams so that the cursor position stays in sync.
        """
        if self._prev_count > 0:
            for stream, is_portal in self._streams:
                try:
                    if is_portal:
                        stream.write(
                            f"\x1b[{self._prev_count}A"
                            + ("\x1b[2K\n" * self._prev_count)
                            + f"\x1b[{self._prev_count}A"
                        )
                        stream.flush()
                except OSError:
                    pass
            self._prev_count = 0

    def flush(self) -> None:
        with self._lock:
            snapshot = list(self._lines.values())
        if not snapshot and self._prev_count == 0:
            return
        with _write_lock:
            for stream, is_portal in self._streams:
                try:
                    if is_portal:
                        # Cursor up to overwrite previous block
                        if self._prev_count > 0:
                            stream.write(f"\x1b[{self._prev_count}A")
                        for line in snapshot:
                            stream.write(f"\x1b[1G{line}\x1b[K\n")
                        # Clear leftover lines from a shrinking block
                        for _ in range(max(0, self._prev_count - len(snapshot))):
                            stream.write("\x1b[2K\n")
                    else:
                        for line in snapshot:
                            cleaned = _clean_for_terminal(line)
                            if cleaned.strip():
                                stream.write(f"\r{cleaned}")
                    stream.flush()
                except OSError:
                    pass
        self._prev_count = len(snapshot)

    def _schedule(self) -> None:
        if not self._stopped:
            self._timer = threading.Timer(self._interval, self._tick)
            self._timer.daemon = True
            self._timer.start()

    def _tick(self) -> None:
        self.flush()
        self._schedule()

# Environment variables that leak the provisioner's own virtualenv into
# child processes.  Stripping them ensures that child scripts (especially
# legacy provisioning scripts that `. /venv/main/bin/activate`) start with
# a clean slate and their own venv activation takes effect.
_VENV_ENV_VARS = frozenset({
    "VIRTUAL_ENV",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "UV_PROJECT_ENVIRONMENT",
})


def _clean_env(env: dict[str, str] | None) -> dict[str, str]:
    """Return an environment dict with venv-related vars stripped.

    When *env* is None (the default), starts from ``os.environ``.
    """
    base = dict(env if env is not None else os.environ)
    for var in _VENV_ENV_VARS:
        base.pop(var, None)
    return base


def _clean_for_terminal(text: str) -> str:
    """Strip ANSI escapes and convert lone \\r to \\n for clean terminal output.

    Order: strip ANSI CSI → collapse \\r\\n → \\n → convert remaining \\r → \\n.
    """
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    return text


def _get_log_streams() -> list[tuple]:
    """Return (stream, is_portal) tuples from the provisioner logger's handlers.

    Portal FileHandler streams (tagged with handler.portal=True) get raw output
    (portal needs ANSI/\\r for progress bars).  All other streams (stdout
    StreamHandler, clean FileHandler) get cleaned output.
    """
    streams = []
    for handler in log.handlers:
        stream = getattr(handler, "stream", None)
        if stream and hasattr(stream, "write"):
            is_portal = getattr(handler, "portal", False)
            streams.append((stream, is_portal))
    return streams


def run_cmd(
    cmd: list[str] | str,
    label: str = "cmd",
    check: bool = True,
    cwd: str | None = None,
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command, streaming its output to all logger destinations.

    Creates a PTY for the child's stdout so that programs which check
    ``isatty()`` (tqdm, hf download, rich, etc.) enable progress bars.
    Raw PTY output (including ``\\r`` for in-place updates) is written
    directly to the provisioner logger's streams (stdout + log file)
    so the portal can handle ``\\r`` for live progress bar rendering.
    Falls back to a plain pipe if PTY creation fails.

    The provisioner's own virtualenv environment variables (VIRTUAL_ENV,
    CONDA_PREFIX, etc.) are stripped from the child environment so that
    child processes are not polluted by the provisioner's venv.

    Returns a :class:`subprocess.CompletedProcess` with the captured output.
    Raises :class:`subprocess.CalledProcessError` when *check* is True and
    the process exits non-zero (same semantics as ``subprocess.run``).
    """
    child_env = _clean_env(env)

    # Try to create a PTY so the child sees isatty()=True on stdout,
    # enabling progress bars in tools like hf download, pip, tqdm, etc.
    master_fd = slave_fd = None
    try:
        master_fd, slave_fd = os.openpty()
        # Set a reasonable terminal width for progress bars
        winsize = struct.pack("HHHH", 24, 120, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except OSError:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        master_fd = slave_fd = None

    use_pty = master_fd is not None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=slave_fd if use_pty else subprocess.PIPE,
            stderr=slave_fd if use_pty else subprocess.STDOUT,
            text=not use_pty,
            cwd=cwd,
            shell=shell,
            env=child_env,
        )
    except Exception:
        if use_pty:
            os.close(master_fd)
            os.close(slave_fd)
        raise

    if use_pty:
        os.close(slave_fd)

    lines: list[str] = []

    if use_pty:
        # Write raw PTY output to all logger streams (stdout + log file),
        # preserving \r for in-place progress bar updates.  The portal's
        # _process_chunk handles \r and cursor-up sequences for live rendering.
        streams = _get_log_streams()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        line_buf = b""
        # Buffer a trailing \r across reads so \r\n split across chunks
        # doesn't produce a spurious extra newline on stdout.
        pending_cr = False
        while True:
            # Use select with a timeout so we can detect when the main
            # process has exited even if a backgrounded child inherited
            # the PTY slave fd and is keeping it open.
            ready, _, _ = select.select([master_fd], [], [], 1.0)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master_fd, 4096)
                if not data:
                    break
            except OSError as e:
                if e.errno in (errno.EIO, errno.EBADF):
                    break
                raise
            # Incremental decode handles multi-byte chars split across reads
            text = decoder.decode(data)
            # Re-assemble \r\n that may have been split across reads
            clean_text = text
            if pending_cr:
                clean_text = "\r" + clean_text
                pending_cr = False
            if clean_text.endswith("\r"):
                clean_text = clean_text[:-1]
                pending_cr = True
            # When a parallel progress tracker is active and this chunk
            # is a \r-only update (no newline), route it to the tracker
            # so multiple threads' progress bars render as a clean block.
            tracker = _parallel_tracker
            if tracker and "\n" not in text:
                # Extract the visible text for the tracker (strip ANSI)
                progress_text = _ANSI_RE.sub("", clean_text).rstrip()
                if progress_text:
                    tracker.update(progress_text)
            else:
                # Normal write path: completed lines or single-threaded mode
                cleaned = None
                with _write_lock:
                    if tracker:
                        # Clear the progress block before writing so the
                        # cursor-up count stays in sync with actual output.
                        tracker.clear_block()
                        tracker.remove_thread()
                    for stream, is_portal in streams:
                        try:
                            if is_portal:
                                stream.write(text)
                            else:
                                if cleaned is None:
                                    cleaned = _clean_for_terminal(clean_text)
                                stream.write(cleaned)
                            stream.flush()
                        except OSError:
                            pass
            # Collect \n-delimited lines for the return value
            line_buf += data
            while b"\n" in line_buf:
                raw, line_buf = line_buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                if line:
                    lines.append(line)
        # Flush any remaining partial UTF-8 bytes from the decoder
        remaining = decoder.decode(b"", final=True)
        if pending_cr:
            remaining = "\r" + remaining
        if remaining:
            cleaned = None
            with _write_lock:
                for stream, is_portal in streams:
                    try:
                        if is_portal:
                            stream.write(remaining)
                        else:
                            if cleaned is None:
                                cleaned = _clean_for_terminal(remaining)
                            stream.write(cleaned)
                        stream.flush()
                    except OSError:
                        pass
        if line_buf:
            line = line_buf.decode("utf-8", errors="replace").rstrip("\r")
            if line:
                lines.append(line)
        os.close(master_fd)
        # Remove this thread from the progress tracker so completed
        # downloads don't show stale progress lines.
        tracker = _parallel_tracker
        if tracker:
            tracker.remove_thread()
    else:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            lines.append(line)
            log.info("[%s] %s", label, line)

    proc.wait()
    output = "\n".join(lines)

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=output,
        )

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=output, stderr="")
