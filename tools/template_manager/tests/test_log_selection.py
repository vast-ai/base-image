"""Default log streaming: a plain `test_template.py <hash>` streams the engine log.

The engine log is chosen from the template's image; --log overrides it; --no-logs
streams nothing. Paths must be files the image actually writes, because the runner
tails files only (/var/log/portal/<program>.log from utils/logging.sh).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_template import ENGINE_LOGS, select_logs

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("image,expected", [
    ("vastai/vllm", ["/var/log/portal/vllm.log"]),
    ("vastai/vllm:v0.32.0-cuda-13.0", ["/var/log/portal/vllm.log"]),
    ("vastai/vllm-omni", ["/var/log/portal/vllm-omni.log"]),
    ("vastai/sglang", ["/var/log/portal/sglang.log"]),
    ("vastai/llama-cpp", ["/var/log/portal/llama.log"]),
    ("vastai/comfy", ["/var/log/portal/comfyui.log", "/var/log/portal/api-wrapper.log"]),
])
def test_the_engine_log_is_chosen_from_the_image(image, expected):
    paths, note = select_logs([], False, image)
    assert paths == expected and "engine log" in note


def test_an_unknown_image_streams_nothing_and_says_how_to_choose():
    paths, note = select_logs([], False, "vastai/base-image")
    assert paths == [] and "--log" in note


def test_explicit_log_wins():
    assert select_logs(["/var/log/x.log"], False, "vastai/vllm")[0] == ["/var/log/x.log"]


def test_no_logs_wins_over_everything():
    assert select_logs(["/var/log/x.log"], True, "vastai/vllm") == ([], "logs off (--no-logs)")


# The supervisor program each default log is named after must exist in that image,
# or the default streams a file nobody writes.
_IMAGE_DIRS = {
    "vastai/vllm": "external/vllm",
    "vastai/vllm-omni": "external/vllm-omni",
    "vastai/sglang": "external/sglang",
    "vastai/llama-cpp": "derivatives/llama-cpp",
    "vastai/comfy": "derivatives/pytorch/derivatives/comfyui",
}


@pytest.mark.parametrize("image", sorted(ENGINE_LOGS))
def test_each_default_log_names_a_real_supervisor_program(image):
    conf_dir = REPO / _IMAGE_DIRS[image] / "ROOT/etc/supervisor/conf.d"
    programs = {p.stem for p in conf_dir.glob("*.conf")}
    for path in ENGINE_LOGS[image]:
        assert Path(path).stem in programs, f"{image}: {path} names no supervisor program"
