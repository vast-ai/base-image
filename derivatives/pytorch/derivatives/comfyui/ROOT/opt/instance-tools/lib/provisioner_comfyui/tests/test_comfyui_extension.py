"""Tests for the provisioner_comfyui extension."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from provisioner.schema import DownloadEntry, FileWrite, GitRepo

import provisioner_comfyui as ext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_GUI_WORKFLOW = {
    "nodes": [
        {
            "type": "CheckpointLoaderSimple",
            "properties": {
                "cnr_id": "comfy-core",
                "models": [
                    {
                        "name": "sd_xl_base_1.0.safetensors",
                        "url": "https://huggingface.co/stabilityai/sdxl/resolve/main/sd_xl_base_1.0.safetensors?download=true",
                        "directory": "checkpoints",
                    }
                ],
            },
        },
        {
            "type": "LoraLoader",
            "properties": {
                "cnr_id": "comfy-core",
                "models": [
                    {
                        "name": "my_lora.safetensors",
                        "url": "https://civitai.com/api/download/models/12345",
                        "directory": "loras",
                    }
                ],
            },
        },
        {
            "type": "IPAdapterAdvanced",
            "properties": {
                "cnr_id": "comfyui-ipadapter-plus",
                "models": [],
            },
        },
        {
            "type": "KSampler",
            "properties": {
                "cnr_id": "comfy-core",
            },
        },
        {
            "type": "CLIPTextEncode",
            "properties": {
                "cnr_id": "comfyui-impact-pack",
            },
        },
    ],
    "extra": {
        "node_versions": {
            "comfy-core": "0.3.12",
            "comfyui-ipadapter-plus": "2.5.2",
            "comfyui-impact-pack": "1.0.0",
        }
    },
}

SAMPLE_SUBGRAPH_WORKFLOW = {
    "nodes": [
        {
            "id": 30,
            "type": "932f407c-4ab6-4cd4-8567-d338b0eb6e18",
            "properties": {
                "cnr_id": "comfy-core",
                "ver": "0.16.0",
                "proxyWidgets": [],
            },
        },
        {
            "id": 76,
            "type": "SaveImage",
            "properties": {
                "cnr_id": "comfy-core",
                "ver": "0.3.64",
            },
        },
    ],
    "definitions": {
        "subgraphs": [
            {
                "id": "932f407c-4ab6-4cd4-8567-d338b0eb6e18",
                "name": "Z-Image-Turbo",
                "nodes": [
                    {
                        "id": 46,
                        "type": "UNETLoader",
                        "properties": {
                            "cnr_id": "comfy-core",
                            "models": [
                                {
                                    "name": "z_image_turbo_bf16.safetensors",
                                    "url": "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors",
                                    "directory": "diffusion_models",
                                }
                            ],
                        },
                    },
                    {
                        "id": 39,
                        "type": "CLIPLoader",
                        "properties": {
                            "cnr_id": "comfy-core",
                            "models": [
                                {
                                    "name": "qwen_3_4b.safetensors",
                                    "url": "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors",
                                    "directory": "text_encoders",
                                }
                            ],
                        },
                    },
                    {
                        "id": 50,
                        "type": "SomeCustomNode",
                        "properties": {
                            "cnr_id": "comfyui-custom-ext",
                        },
                    },
                ],
            }
        ]
    },
}

SAMPLE_API_WORKFLOW = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "2": {"class_type": "KSampler", "inputs": {}},
}

COMFYUI_DIR = "/workspace/ComfyUI"


@dataclass
class FakeManifest:
    downloads: list = field(default_factory=list)
    git_repos: list = field(default_factory=list)
    write_files_late: list = field(default_factory=list)


@dataclass
class FakeContext:
    manifest: FakeManifest = field(default_factory=FakeManifest)
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("test"))


# ---------------------------------------------------------------------------
# Unit tests: _parse_workflow_urls
# ---------------------------------------------------------------------------


class TestParseWorkflowUrls:
    def test_semicolon_separated(self):
        result = ext._parse_workflow_urls("https://a.com/wf1.json;https://b.com/wf2.json")
        assert result == ["https://a.com/wf1.json", "https://b.com/wf2.json"]

    def test_whitespace_trimmed(self):
        result = ext._parse_workflow_urls("  https://a.com/wf1.json ; https://b.com/wf2.json  ")
        assert result == ["https://a.com/wf1.json", "https://b.com/wf2.json"]

    def test_multiple_entries(self):
        result = ext._parse_workflow_urls("https://a.com/1.json;https://b.com/2.json;https://c.com/3.json")
        assert result == ["https://a.com/1.json", "https://b.com/2.json", "https://c.com/3.json"]

    def test_deduplicates(self):
        result = ext._parse_workflow_urls("https://a.com/wf.json;https://a.com/wf.json")
        assert result == ["https://a.com/wf.json"]

    def test_empty_entries_skipped(self):
        result = ext._parse_workflow_urls(";;https://a.com/wf.json;;")
        assert result == ["https://a.com/wf.json"]

    def test_empty_string(self):
        assert ext._parse_workflow_urls("") == []

    def test_whitespace_only(self):
        assert ext._parse_workflow_urls("   ") == []


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestCollectAllNodes:
    def test_top_level_only(self):
        nodes = ext._collect_all_nodes(SAMPLE_GUI_WORKFLOW)
        assert len(nodes) == 5

    def test_subgraph_nodes_included(self):
        nodes = ext._collect_all_nodes(SAMPLE_SUBGRAPH_WORKFLOW)
        # 2 top-level + 3 subgraph
        assert len(nodes) == 5
        node_ids = {n["id"] for n in nodes}
        assert {30, 76, 46, 39, 50} == node_ids

    def test_empty_workflow(self):
        assert ext._collect_all_nodes({}) == []

    def test_no_definitions(self):
        data = {"nodes": [{"id": 1}]}
        assert len(ext._collect_all_nodes(data)) == 1

    def test_empty_subgraphs(self):
        data = {"nodes": [{"id": 1}], "definitions": {"subgraphs": []}}
        assert len(ext._collect_all_nodes(data)) == 1


class TestIsGuiFormat:
    def test_gui_format(self):
        assert ext._is_gui_format(SAMPLE_GUI_WORKFLOW) is True

    def test_api_format(self):
        assert ext._is_gui_format(SAMPLE_API_WORKFLOW) is False

    def test_empty_dict(self):
        assert ext._is_gui_format({}) is False


class TestExtractModels:
    def test_extracts_models(self):
        models = ext._extract_models(SAMPLE_GUI_WORKFLOW["nodes"], COMFYUI_DIR)
        assert len(models) == 2
        # First model — URL should be cleaned (no ?download=true)
        assert models[0].url == "https://huggingface.co/stabilityai/sdxl/resolve/main/sd_xl_base_1.0.safetensors"
        assert models[0].dest == f"{COMFYUI_DIR}/models/checkpoints/sd_xl_base_1.0.safetensors"
        # Second model
        assert models[1].url == "https://civitai.com/api/download/models/12345"
        assert models[1].dest == f"{COMFYUI_DIR}/models/loras/my_lora.safetensors"

    def test_skips_invalid_entries(self):
        nodes = [
            {"properties": {"models": [{"name": "", "url": ""}]}},
            {"properties": {"models": "not-a-list"}},
            {"properties": "not-a-dict"},
            {},
        ]
        models = ext._extract_models(nodes, COMFYUI_DIR)
        assert len(models) == 0

    def test_dedup_within_workflow(self):
        nodes = [
            {
                "properties": {
                    "models": [
                        {"name": "m.safetensors", "url": "https://example.com/m.safetensors", "directory": "checkpoints"},
                        {"name": "m.safetensors", "url": "https://example.com/m.safetensors", "directory": "checkpoints"},
                    ]
                }
            }
        ]
        models = ext._extract_models(nodes, COMFYUI_DIR)
        assert len(models) == 1


class TestExtractCustomNodeIds:
    def test_extracts_non_core(self):
        ids = ext._extract_custom_node_ids(SAMPLE_GUI_WORKFLOW["nodes"])
        assert ids == {"comfyui-ipadapter-plus", "comfyui-impact-pack"}

    def test_filters_comfy_core(self):
        ids = ext._extract_custom_node_ids(SAMPLE_GUI_WORKFLOW["nodes"])
        assert "comfy-core" not in ids

    def test_empty_nodes(self):
        assert ext._extract_custom_node_ids([]) == set()


class TestRepoNameFromUrl:
    def test_with_git_suffix(self):
        assert ext._repo_name_from_url("https://github.com/user/repo.git") == "repo"

    def test_without_git_suffix(self):
        assert ext._repo_name_from_url("https://github.com/user/repo") == "repo"

    def test_trailing_slash(self):
        assert ext._repo_name_from_url("https://github.com/user/repo/") == ""


class TestWorkflowFilename:
    def test_basic(self):
        assert ext._workflow_filename("https://example.com/my-workflow.json") == "my-workflow.json"

    def test_no_extension(self):
        assert ext._workflow_filename("https://example.com/workflow") == "workflow.json"

    def test_empty_path(self):
        assert ext._workflow_filename("https://example.com/") == "workflow.json"

    def test_with_query(self):
        assert ext._workflow_filename("https://example.com/wf.json?v=2") == "wf.json"


class TestCleanUrl:
    def test_strips_query(self):
        assert ext._clean_url("https://hf.co/model.safetensors?download=true") == "https://hf.co/model.safetensors"

    def test_no_query_passthrough(self):
        url = "https://example.com/model.safetensors"
        assert ext._clean_url(url) == url


class TestResolveNodeRepo:
    def test_success(self):
        response_data = json.dumps({"repository": "https://github.com/user/ComfyUI-IPAdapter-Plus"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        log = logging.getLogger("test")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ext._resolve_node_repo("comfyui-ipadapter-plus", log)
        assert result == "https://github.com/user/ComfyUI-IPAdapter-Plus.git"

    def test_already_has_git_suffix(self):
        response_data = json.dumps({"repository": "https://github.com/user/repo.git"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        log = logging.getLogger("test")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ext._resolve_node_repo("some-node", log)
        assert result == "https://github.com/user/repo.git"

    def test_network_error(self):
        log = logging.getLogger("test")
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = ext._resolve_node_repo("bad-node", log)
        assert result is None

    def test_no_repository_field(self):
        response_data = json.dumps({"name": "some-node"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        log = logging.getLogger("test")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ext._resolve_node_repo("some-node", log)
        assert result is None


# ---------------------------------------------------------------------------
# Integration tests: run()
# ---------------------------------------------------------------------------


def _make_urlopen_mock(workflow_data, registry_responses=None):
    """Create a mock urlopen that returns workflow data or registry responses."""
    registry_responses = registry_responses or {}
    call_count = [0]

    def side_effect(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        if "api.comfy.org" in url:
            # Extract cnr_id from URL
            cnr_id = url.split("/nodes/")[-1]
            data = registry_responses.get(cnr_id, {})
            mock_resp.read.return_value = json.dumps(data).encode()
        else:
            mock_resp.read.return_value = json.dumps(workflow_data).encode()

        return mock_resp

    return side_effect


class TestRun:
    def test_full_integration(self):
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/my-workflow.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        registry = {
            "comfyui-ipadapter-plus": {"repository": "https://github.com/user/ComfyUI-IPAdapter-Plus"},
            "comfyui-impact-pack": {"repository": "https://github.com/user/ComfyUI-Impact-Pack.git"},
        }
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(SAMPLE_GUI_WORKFLOW, registry)):
            ext.run(config, ctx)

        # 2 model downloads
        assert len(ctx.manifest.downloads) == 2
        assert ctx.manifest.downloads[0].dest == f"{COMFYUI_DIR}/models/checkpoints/sd_xl_base_1.0.safetensors"

        # 2 custom node repos
        assert len(ctx.manifest.git_repos) == 2
        repo_dests = {r.dest for r in ctx.manifest.git_repos}
        assert f"{COMFYUI_DIR}/custom_nodes/ComfyUI-IPAdapter-Plus" in repo_dests
        assert f"{COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Pack" in repo_dests

        # 1 workflow file saved via write_files_late
        assert len(ctx.manifest.write_files_late) == 1
        assert ctx.manifest.write_files_late[0].path == f"{COMFYUI_DIR}/user/default/workflows/my-workflow.json"

    def test_dry_run_no_network(self):
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/wf.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        # urlopen should NOT be called in dry-run
        with patch("urllib.request.urlopen", side_effect=AssertionError("should not be called")):
            ext.run(config, ctx, dry_run=True)

        assert len(ctx.manifest.downloads) == 0
        assert len(ctx.manifest.git_repos) == 0

    def test_api_format_skipped(self):
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/api-wf.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(SAMPLE_API_WORKFLOW)):
            ext.run(config, ctx)

        assert len(ctx.manifest.downloads) == 0
        assert len(ctx.manifest.git_repos) == 0

    def test_dedup_existing_downloads(self):
        ctx = FakeContext()
        # Pre-populate a download that matches one in the workflow
        ctx.manifest.downloads.append(
            DownloadEntry(
                url="https://huggingface.co/stabilityai/sdxl/resolve/main/sd_xl_base_1.0.safetensors",
                dest="/existing/path",
            )
        )
        config = {
            "workflows": ["https://example.com/wf.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        registry = {
            "comfyui-ipadapter-plus": {"repository": "https://github.com/user/ComfyUI-IPAdapter-Plus"},
            "comfyui-impact-pack": {"repository": "https://github.com/user/ComfyUI-Impact-Pack"},
        }
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(SAMPLE_GUI_WORKFLOW, registry)):
            ext.run(config, ctx)

        # Only the lora should be added (the checkpoint was already present)
        assert len(ctx.manifest.downloads) == 2  # 1 existing + 1 new
        urls = [d.url for d in ctx.manifest.downloads]
        assert urls.count("https://huggingface.co/stabilityai/sdxl/resolve/main/sd_xl_base_1.0.safetensors") == 1

    def test_dedup_existing_repos(self):
        ctx = FakeContext()
        ctx.manifest.git_repos.append(
            GitRepo(url="https://github.com/user/ComfyUI-IPAdapter-Plus.git", dest="/existing")
        )
        config = {
            "workflows": ["https://example.com/wf.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        registry = {
            "comfyui-ipadapter-plus": {"repository": "https://github.com/user/ComfyUI-IPAdapter-Plus.git"},
            "comfyui-impact-pack": {"repository": "https://github.com/user/ComfyUI-Impact-Pack"},
        }
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(SAMPLE_GUI_WORKFLOW, registry)):
            ext.run(config, ctx)

        # Only impact-pack should be added
        assert len(ctx.manifest.git_repos) == 2  # 1 existing + 1 new

    def test_fetch_failure_raises(self):
        """Workflow fetch failure should raise RuntimeError."""
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/bad.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            with pytest.raises(RuntimeError, match="workflow fetch"):
                ext.run(config, ctx)

        assert len(ctx.manifest.downloads) == 0

    def test_no_workflows(self):
        ctx = FakeContext()
        config = {"comfyui_dir": COMFYUI_DIR}
        ext.run(config, ctx)
        assert len(ctx.manifest.downloads) == 0

    def test_registry_failure_raises(self):
        """Registry lookup failure should collect models but raise RuntimeError."""
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/wf.json"],
            "comfyui_dir": COMFYUI_DIR,
        }

        def side_effect(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)

            if "api.comfy.org" in url:
                raise Exception("404 Not Found")
            mock_resp.read.return_value = json.dumps(SAMPLE_GUI_WORKFLOW).encode()
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with pytest.raises(RuntimeError, match="node resolution"):
                ext.run(config, ctx)

        # Models should still be extracted even if registry fails
        assert len(ctx.manifest.downloads) == 2
        assert len(ctx.manifest.git_repos) == 0

    def test_workflow_saved_to_write_files_late(self):
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/path/cool-workflow.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        simple_wf = {"nodes": []}
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(simple_wf)):
            ext.run(config, ctx)

        assert len(ctx.manifest.write_files_late) == 1
        fw = ctx.manifest.write_files_late[0]
        assert fw.path == f"{COMFYUI_DIR}/user/default/workflows/cool-workflow.json"
        assert json.loads(fw.content) == simple_wf

    def test_default_comfyui_dir_uses_expand_env(self, monkeypatch):
        """When comfyui_dir is not in config, expand_env resolves the default."""
        monkeypatch.setenv("WORKSPACE", "/data")
        ctx = FakeContext()
        config = {"workflows": ["https://example.com/wf.json"]}
        simple_wf = {"nodes": []}
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(simple_wf)):
            ext.run(config, ctx)
        # Workflow dest should use expanded WORKSPACE
        assert ctx.manifest.write_files_late[0].path.startswith("/data/ComfyUI/")

    def test_default_comfyui_dir_fallback(self, monkeypatch):
        """When WORKSPACE is unset, the default /workspace is used."""
        monkeypatch.delenv("WORKSPACE", raising=False)
        ctx = FakeContext()
        config = {"workflows": ["https://example.com/wf.json"]}
        simple_wf = {"nodes": []}
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(simple_wf)):
            ext.run(config, ctx)
        assert ctx.manifest.write_files_late[0].path.startswith("/workspace/ComfyUI/")

    def test_subgraph_workflow(self):
        """Models and custom nodes inside subgraph definitions are extracted."""
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/z-image-turbo.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        registry = {
            "comfyui-custom-ext": {"repository": "https://github.com/user/ComfyUI-Custom-Ext"},
        }
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(SAMPLE_SUBGRAPH_WORKFLOW, registry)):
            ext.run(config, ctx)

        # 2 models from subgraph nodes
        assert len(ctx.manifest.downloads) == 2
        urls = {d.url for d in ctx.manifest.downloads}
        assert "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors" in urls
        assert "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors" in urls

        # 1 custom node from subgraph (comfy-core filtered out)
        assert len(ctx.manifest.git_repos) == 1
        assert ctx.manifest.git_repos[0].dest == f"{COMFYUI_DIR}/custom_nodes/ComfyUI-Custom-Ext"

        # Workflow file saved
        assert len(ctx.manifest.write_files_late) == 1

    def test_env_var_appends_workflows(self, monkeypatch):
        """PROVISIONING_COMFYUI_WORKFLOWS adds URLs to the workflows list."""
        monkeypatch.setenv(
            "PROVISIONING_COMFYUI_WORKFLOWS",
            "https://example.com/env-wf.json",
        )
        ctx = FakeContext()
        config = {"comfyui_dir": COMFYUI_DIR}  # no workflows in config
        simple_wf = {"nodes": []}
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(simple_wf)):
            ext.run(config, ctx)

        assert len(ctx.manifest.write_files_late) == 1
        assert "env-wf.json" in ctx.manifest.write_files_late[0].path

    def test_env_var_deduplicates_with_config(self, monkeypatch):
        """URLs already in config are not added again from the env var."""
        monkeypatch.setenv(
            "PROVISIONING_COMFYUI_WORKFLOWS",
            "https://example.com/wf.json;https://example.com/extra.json",
        )
        ctx = FakeContext()
        config = {
            "workflows": ["https://example.com/wf.json"],
            "comfyui_dir": COMFYUI_DIR,
        }
        simple_wf = {"nodes": []}
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(simple_wf)):
            ext.run(config, ctx)

        # wf.json from config + extra.json from env = 2 workflows processed
        assert len(ctx.manifest.write_files_late) == 2
        paths = [fw.path for fw in ctx.manifest.write_files_late]
        assert sum(1 for p in paths if "wf.json" in p) == 1
        assert sum(1 for p in paths if "extra.json" in p) == 1

    def test_env_var_unset_no_effect(self, monkeypatch):
        """When env var is unset, only config workflows are used."""
        monkeypatch.delenv("PROVISIONING_COMFYUI_WORKFLOWS", raising=False)
        ctx = FakeContext()
        config = {"comfyui_dir": COMFYUI_DIR}
        ext.run(config, ctx)
        assert len(ctx.manifest.write_files_late) == 0
