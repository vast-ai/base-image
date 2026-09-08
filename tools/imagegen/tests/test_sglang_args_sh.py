"""Tests for the parallelism-arg assembly in external/sglang's sglang.sh.

WHY THIS FILE EXISTS. This block decides `--tensor-parallel-size` and `--ep-size`
for every sglang instance, and both have to agree with each other: sglang computes
`moe_tp_size = tp_size / ep_size`, so an ep that does not divide tp fails the model
load with an arithmetic complaint that names neither flag —
`(moe_intermediate_size=640 / moe_tp_size=4) % weight_block_size_n=128 != 0`.

It has been wrong in both directions. It shipped with ep_size defaulting to 1, which
is what blocked Qwen3.8-Flash-Next-FP8. The first fix (PR 275) sized ep from
$GPU_COUNT, which is correct only when we supplied the tp ourselves — with a
template-pinned `--tensor-parallel-size 2` on an 8-GPU host it produced ep=8 against
tp=2 and reintroduced the same crash from the other side.

Neither direction is reachable by a deploy test on a convenient box: with tp
unpinned, ep == GPU_COUNT == tp and every arrangement looks identical. Only a table
separates them, and the block is pure string assembly, so a table is enough — no
GPU, no container, no sglang.

The block is delimited in the script by `---- unit-tested block ----` markers, and
extracted here rather than reimplemented: the text under test is the text that ships.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / \
    "external/sglang/ROOT/opt/supervisor-scripts/sglang.sh"

_BLOCK = re.compile(
    r"^# ---- unit-tested block:.*?$\n(.*?)^# ---- end unit-tested block ----$",
    re.S | re.M)


def _block() -> str:
    m = _BLOCK.search(SCRIPT.read_text(encoding="utf-8"))
    assert m, ("the marker comments around the parallelism block are gone — either "
               "restore them or move this test to whatever replaced the block")
    return m.group(1)


def run(sglang_args: str, gpu_count: str = "4", auto_parallel: str = "true") -> str:
    """Return the args as the script would interpolate them into `sglang serve`.

    Mirrors the real launch line: `${SGLANG_ARGS} ${AUTO_PARALLEL_ARGS}`, unquoted,
    so the shell's own word splitting normalises the spacing exactly as it does at
    runtime and the result is comparable token-for-token.
    """
    prog = (f"SGLANG_ARGS={sglang_args!r}\n"
            f"GPU_COUNT={gpu_count!r}\n"
            f"AUTO_PARALLEL={auto_parallel!r}\n"
            + _block() +
            "\nset -- ${SGLANG_ARGS} ${AUTO_PARALLEL_ARGS}\necho \"ARGS:$*\"\n")
    p = subprocess.run(["bash", "-c", prog], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    line = [l for l in p.stdout.splitlines() if l.startswith("ARGS:")]
    assert len(line) == 1, p.stdout
    return line[0][len("ARGS:"):].strip()


def says(sglang_args: str, gpu_count: str = "4", auto_parallel: str = "true") -> str:
    """The explanatory output the launcher writes to /var/log/sglang.log."""
    prog = (f"SGLANG_ARGS={sglang_args!r}\n"
            f"GPU_COUNT={gpu_count!r}\n"
            f"AUTO_PARALLEL={auto_parallel!r}\n"
            + _block())
    p = subprocess.run(["bash", "-c", prog], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


# ── the auto-parallel default, unchanged by this work ────────────────

def test_no_args_gets_tp_from_the_gpu_count():
    assert run("", gpu_count="4") == "--tensor-parallel-size 4"


def test_auto_parallel_false_adds_nothing():
    assert run("", gpu_count="4", auto_parallel="false") == ""


def test_a_pinned_tp_alias_suppresses_auto_tp():
    assert run("--tensor-parallel-size 2", gpu_count="8") == "--tensor-parallel-size 2"


def test_a_pinned_short_tp_suppresses_auto_tp():
    """sglang's PRIMARY spelling. The substring guard this replaced matched only
    `parallel-size`, so `--tp-size 2` on an 8-GPU host got `--tensor-parallel-size 8`
    appended beside it — two values for one setting, from one flag the user set."""
    assert run("--tp-size 2", gpu_count="8") == "--tp-size 2"


def test_a_pinned_dp_suppresses_auto_tp():
    assert run("--dp-size 2", gpu_count="8") == "--dp-size 2"


# ── the expert-parallel translation ──────────────────────────────────

def test_the_portable_flag_becomes_a_sized_one():
    """--enable-expert-parallel is vLLM's spelling and is inert on sglang. The FP8
    checkpoint will not load without expert parallelism actually enabled."""
    assert run("--enable-expert-parallel", gpu_count="4") == \
        "--tensor-parallel-size 4 --ep-size 4"


def test_the_portable_flag_does_not_survive_the_translation():
    assert "--enable-expert-parallel" not in run("--enable-expert-parallel")


def test_other_args_are_left_alone():
    assert run("--mem-fraction-static 0.9 --enable-expert-parallel", gpu_count="4") == \
        "--mem-fraction-static 0.9 --tensor-parallel-size 4 --ep-size 4"


def test_ep_follows_a_pinned_tp_not_the_gpu_count():
    """THE regression. Sizing ep from $GPU_COUNT is right only when we supplied the
    tp. Here the template pinned tp=2 on an 8-GPU host: ep=8 gives moe_tp_size=0.25
    and the load dies with the same message the translation exists to prevent."""
    assert run("--tensor-parallel-size 2 --enable-expert-parallel", gpu_count="8") == \
        "--tensor-parallel-size 2 --ep-size 2"


def test_ep_follows_a_pinned_short_tp_too():
    assert run("--tp-size 2 --enable-expert-parallel", gpu_count="8") == \
        "--tp-size 2 --ep-size 2"


def test_an_explicit_ep_size_is_the_users_own_and_is_kept():
    assert run("--enable-expert-parallel --ep-size 2", gpu_count="4") == \
        "--ep-size 2 --tensor-parallel-size 4"


def test_an_explicit_ep_size_alias_is_also_kept():
    out = run("--enable-expert-parallel --expert-parallel-size 2", gpu_count="4")
    assert "--expert-parallel-size 2" in out and "--ep-size" not in out


def test_an_attached_value_leaves_no_stray_token():
    """vLLM accepts --enable-expert-parallel=True. Stripping only the flag name
    leaves a bare `=True` as a positional for sglang to reject."""
    assert run("--enable-expert-parallel=True", gpu_count="4") == \
        "--tensor-parallel-size 4 --ep-size 4"


def test_the_negated_vllm_flag_is_not_treated_as_a_request():
    """--no-enable-expert-parallel asks for the opposite. Translating it into
    --ep-size would turn expert parallelism ON because the user asked for it off."""
    assert "--ep-size" not in run("--no-enable-expert-parallel", gpu_count="4")


def test_no_ep_when_nothing_set_the_tp():
    """AUTO_PARALLEL=false means NOTHING emits a tensor-parallel size, so sglang runs
    at its own default of 1. Sizing ep from $GPU_COUNT here would give ep=4 against
    tp=1 — moe_tp_size 0.25, the original crash with different numbers. At tp=1 there
    is nothing to spread experts across and the honest output is no flag at all."""
    assert run("--enable-expert-parallel", gpu_count="4", auto_parallel="false") == ""
    assert "nothing to spread" in says("--enable-expert-parallel", "4", "false")


def test_no_ep_when_a_dp_pin_suppressed_the_tp():
    """Same hole by a different route: the dp pin suppresses auto-TP, so tp is again
    sglang's default of 1 while $GPU_COUNT is 8."""
    assert run("--dp-size 2 --enable-expert-parallel", gpu_count="8") == "--dp-size 2"


def test_ep_is_sized_from_the_pin_not_the_host_when_auto_parallel_is_off():
    """AUTO_PARALLEL=false disclaims our guess, not the user's own pin."""
    assert run("--tp-size 2 --enable-expert-parallel", gpu_count="8",
               auto_parallel="false") == "--tp-size 2 --ep-size 2"


def test_an_empty_gpu_count_cannot_produce_a_dangling_ep_size():
    """GPU_COUNT is platform-supplied. If it is missing, `--ep-size ` with nothing
    after it swallows the next token or aborts the parse."""
    assert "--ep-size" not in run("--enable-expert-parallel", gpu_count="")


def test_the_rewrite_is_announced():
    """This is the only place a flag the operator wrote does not reach sglang
    verbatim. /var/log/sglang.log is where they will look for why."""
    assert "--ep-size 4" in says("--enable-expert-parallel", "4")


def test_the_negation_survives_a_strip_of_the_positive_flag():
    """Pins the near-miss. `--no-enable-expert-parallel` looks like it contains the
    flag name, but the match needs TWO dashes before `enable` and the negation offers
    one, so neither the guard nor the strip touches it — verified against both an
    anchored and an unanchored strip. It is recorded because the reasoning is easy to
    get backwards, and a future spelling with one dash would silently shred it."""
    out = run("--no-enable-expert-parallel --enable-expert-parallel", gpu_count="4")
    assert "--no-enable-expert-parallel" in out
    assert "--no " not in f"{out} "


@pytest.mark.parametrize("args", ["", "--enable-expert-parallel", "--tp-size 2"])
def test_nothing_emits_a_dangling_flag(args):
    """Every emitted flag must carry a value. A trailing `--ep-size` with nothing
    after it swallows the next token or aborts the parse."""
    out = run(args, gpu_count="4")
    assert not re.search(r"--\S+$", out) or re.search(r"--\S+ \S+$", out), out
    assert "--ep-size --" not in out and "--tensor-parallel-size --" not in out
