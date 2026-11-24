"""An image is identified by its repository NAME, not the namespace it was pushed to.

QA-gate cells launch `<staging namespace>/<repo>`, not `vastai/<repo>`. Every table
keyed by image repository (the contract's engine table, the engine route checks, the
*_TEST_ENDPOINT variable, the default engine log) must resolve the same row for both,
or a gate cell runs no engine check at all and reports "no OpenAI engine in image".
"""
import pytest

import contract
import external_checks as ec
import test_template as tt

ENGINES = ("vllm", "vllm-omni", "sglang", "llama-cpp")
NAMESPACES = ("vastai", "somestaging", "registry.example/team")


@pytest.mark.parametrize("ns", NAMESPACES)
@pytest.mark.parametrize("name", ENGINES)
def test_engine_tables_ignore_the_namespace(ns, name):
    image = f"{ns}/{name}:v1-cuda-13.0"
    canonical = f"vastai/{name}"
    assert contract.image_repo(image) == canonical
    assert contract.set_engine(image) is contract.ENGINES[canonical]
    assert ec.endpoint_test_var(image) == ec.TEST_ENDPOINT_VARS[canonical]
    caps, source, problems = ec.resolve_capabilities({"image": image, "env": ""})
    assert caps == {"chat", "completions"} and not problems
    assert "pyworker default" in source
    paths, _ = tt.select_logs(None, False, image)
    assert paths == list(tt.ENGINE_LOGS[canonical])
    for images in (ec.OPENAI_ENGINE, ec.CONTRACT):
        assert ec.produced_on(images, image)


@pytest.mark.parametrize("image", ["somestaging/pytorch:x", "somestaging/base-image", ""])
def test_a_non_engine_image_is_still_not_an_engine(image):
    assert contract.set_engine(image) is None
    caps, source, _ = ec.resolve_capabilities({"image": image, "env": ""})
    assert caps == set() and "no OpenAI engine" in source
    assert not ec.produced_on(ec.OPENAI_ENGINE, image)


def test_the_message_names_the_image_as_given():
    _, source, _ = ec.resolve_capabilities({"image": "somestaging/pytorch:x", "env": ""})
    assert "somestaging/pytorch" in source


@pytest.mark.parametrize("image,expected", [
    ("vastai/vllm", "vastai/vllm"),
    ("ns/vllm@sha256:abc", "vastai/vllm"),
    ("vllm:tag", "vastai/vllm"),
    ("", ""), (None, ""),
])
def test_image_repo(image, expected):
    assert contract.image_repo(image) == expected
