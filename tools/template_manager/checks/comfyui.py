"""ComfyUI workflows: every provisioned workflow runs through the public API wrapper
and every output node delivers what it is built to produce."""

from __future__ import annotations

import base64
import json
import re
import struct
import zlib

from . import (ANSWER, COMFYUI, ROUTE, STRUCTURAL, ResultName, TestType, _deadline_result,
               _guarded, _no_response)

COMFYUI_CAPABILITY = "comfyui-workflows"

# ComfyUI results are this name, or this name followed by `/<workflow>` and so on
# (RESULT_NAMES). The bare name and `object_info` are reported only when the workflows
# could not be run or their output nodes could not be read.
COMFYUI_CHECK = "external/comfyui"
COMFYUI_OBJECT_INFO = f"{COMFYUI_CHECK}/object_info"
COMFY_WRAPPER_INTERNAL_PORT = 18288
COMFY_PAYLOADS_DIR = "opt/comfyui-api-wrapper/payloads"
# A `modifier` payload names one of the wrapper's built-in workflows instead of
# carrying one; the wrapper's convention is modifier `Text2Image` ->
# modifiers/text2image.py -> WORKFLOW_JSON "workflows/text2image.json". Modifications
# change inputs (prompt, checkpoint), not which output nodes exist.
COMFY_MODIFIER_WORKFLOWS_DIR = "opt/comfyui-api-wrapper/workflows"
JUPYTER_PORT = 8080
COMFY_GENERATE_TIMEOUT = 1800
# Standard deviation of 0-255 luminance below this is a blank or uniform image.
IMAGE_MIN_STDDEV = 4.0
# Compressed bytes per pixel below this means a near-uniform image (non-PNG formats).
IMAGE_MIN_BYTES_PER_PIXEL = 0.01
IMAGE_MIN_SIDE = 64


def _png_pixels(data):
    """(width, height, luminance samples) for an 8-bit non-interlaced PNG, or None
    when the PNG uses a layout this decoder does not handle."""
    pos, chunks, ihdr = 8, [], None
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            chunks.append(body)
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if not ihdr:
        return None
    w, h, depth, color, _, _, interlace = ihdr
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if depth != 8 or interlace or channels is None:
        return None
    raw = zlib.decompress(b"".join(chunks))
    stride, bpp = w * channels, channels
    prev, lum, off = bytearray(stride), [], 0
    step = max(1, h // 64)                       # sample rows; enough for variance
    for y in range(h):
        ftype, line = raw[off], bytearray(raw[off + 1:off + 1 + stride])
        off += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 255
            elif ftype == 2:
                line[i] = (line[i] + b) & 255
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ftype == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if y % step == 0:
            for x in range(0, w, max(1, w // 64)):
                px = line[x * channels:x * channels + min(channels, 3)]
                lum.append(sum(px) / len(px))
        prev = line
    return w, h, lum


def _image_info(data):
    """(format, width, height) from an image header, or None if not an image."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker, seglen = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpeg", w, h
            i += 2 + seglen
        return "jpeg", 0, 0
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X":
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return "webp", w, h
        if data[12:16] == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return "webp", w & 0x3FFF, h & 0x3FFF
        if data[12:16] == b"VP8L":
            b = data[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return "webp", w, h
        return "webp", 0, 0
    return None


def _media_kind(data):
    """'video', 'audio' or None from a container signature."""
    if data[4:8] == b"ftyp" or data[:4] == b"\x1aE\xdf\xa3" or data[:4] == b"GIF8":
        return "video"
    if (data[:4] == b"RIFF" and data[8:12] == b"WAVE") or data[:3] == b"ID3" or data[:4] == b"fLaC" \
            or data[:4] == b"OggS":
        return "audio"
    return None


def check_output_file(name, data):
    """(structural result, answer result) for one workflow output file."""
    prefix = f"{COMFYUI_CHECK}/{name}"
    info = _image_info(data)
    if info:
        fmt, w, h = info
        if min(w, h) < IMAGE_MIN_SIDE:
            return ({"name": prefix, "state": "failed", "kind": "structural",
                     "detail": f"{fmt} is {w}x{h}, below {IMAGE_MIN_SIDE}px"},
                    {"name": prefix + ":content", "state": "skipped", "kind": "answer",
                     "detail": "structural check failed"})
        structural = {"name": prefix, "state": "passed", "kind": "structural",
                      "detail": f"{fmt} {w}x{h}, {len(data)} bytes"}
        pixels = _png_pixels(data) if fmt == "png" else None
        if pixels:
            lum = pixels[2]
            mean = sum(lum) / len(lum)
            sd = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
            ok = sd >= IMAGE_MIN_STDDEV
            detail = f"luminance stddev {sd:.1f} (min {IMAGE_MIN_STDDEV})"
        else:
            bpp = len(data) / (w * h)
            ok = bpp >= IMAGE_MIN_BYTES_PER_PIXEL
            detail = f"{bpp:.3f} bytes/pixel (min {IMAGE_MIN_BYTES_PER_PIXEL}; compression proxy)"
        return structural, {"name": prefix + ":content", "kind": "answer",
                            "state": "passed" if ok else "failed",
                            "detail": detail if ok else "blank or uniform image: " + detail}
    kind = _media_kind(data)
    if kind:
        return ({"name": prefix, "state": "passed", "kind": "structural",
                 "detail": f"{kind} container, {len(data)} bytes"},
                {"name": prefix + ":content", "state": "skipped", "kind": "answer",
                 "detail": f"{kind} content checks are not written yet"})
    return ({"name": prefix, "state": "failed", "kind": "structural",
             "detail": f"not a recognised image, video or audio file (starts {data[:12]!r})"},
            {"name": prefix + ":content", "state": "skipped", "kind": "answer",
             "detail": "structural check failed"})


COMFY_INTERNAL_PORT = 18188

# What each known output node writes. Output nodes are DISCOVERED from ComfyUI's own
# /object_info (`output_node: true`), so a node missing here is never silently
# passed: it is reported as unclassified, by class name, so the table can grow.
_OUTPUT_KINDS = {
    "SaveImage": "image",
    "SaveImageWebsocket": "image",
    "SaveAnimatedWEBP": "animation",
    "SaveAnimatedPNG": "animation",
    "SaveVideo": "video",
    "SaveWEBM": "video",
    "SaveAudio": "audio",
    "SaveAudioMP3": "audio",
    "SaveAudioOpus": "audio",
    "SaveGLB": "3d",
}
# Nodes whose output the wrapper deliberately does not return (temp/preview files).
_PREVIEW_PREFIX = "Preview"


def _vhs_kind(inputs):
    """VideoHelperSuite's VHS_VideoCombine picks its container from `format`."""
    fmt = str(inputs.get("format", ""))
    if fmt.startswith("video/"):
        return "video"
    if fmt.startswith("image/"):
        return "animation"
    return None


def _is_link(value):
    """In API-format workflows an input wired to another node is [node_id, slot]."""
    return isinstance(value, list) and len(value) == 2 and isinstance(value[1], int)


def _expects_audio_track(workflow, node):
    """True when a video save node is fed audio: an `audio` input wired on the node
    itself (VHS_VideoCombine) or on the node feeding its video (CreateVideo)."""
    inputs = node.get("inputs") or {}
    if _is_link(inputs.get("audio")):
        return True
    for key in ("video", "images"):
        src = inputs.get(key)
        if _is_link(src):
            upstream = workflow.get(str(src[0])) or {}
            if _is_link((upstream.get("inputs") or {}).get("audio")):
                return True
    return False


def expected_outputs(workflow, output_node_classes):
    """{node_id: (class_type, kind, wants_audio)} for every output node in an
    API-format workflow. kind is image|animation|video|audio|3d|preview|unclassified.

    `output_node_classes` is the set of class names ComfyUI reports as output
    nodes, or None when /object_info could not be read (then only the known table
    is used, and the result says so).
    """
    out = {}
    for node_id, node in (workflow or {}).items():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type", "")
        known = cls in _OUTPUT_KINDS or cls == "VHS_VideoCombine" or cls.startswith(_PREVIEW_PREFIX)
        is_output = (cls in output_node_classes) if output_node_classes is not None else known
        if not is_output:
            continue
        if cls.startswith(_PREVIEW_PREFIX):
            kind = "preview"
        elif cls == "VHS_VideoCombine":
            kind = _vhs_kind(node.get("inputs") or {}) or "unclassified"
        else:
            kind = _OUTPUT_KINDS.get(cls, "unclassified")
        wants_audio = kind == "video" and _expects_audio_track(workflow, node)
        out[str(node_id)] = (cls, kind, wants_audio)
    return out


def _mp4_has_audio(data):
    """An MP4/MOV with a sound track carries an `hdlr` box with handler type `soun`."""
    return re.search(rb"hdlr.{8}soun", data, re.S) is not None


def _file_kind(data):
    """What a file actually is, from its signature."""
    info = _image_info(data)
    if info:
        return "image"
    if data[:4] == b"glTF":
        return "3d"
    return _media_kind(data)


# Which file kinds satisfy each expected node kind.
_ACCEPTS = {"image": {"image"}, "animation": {"image", "video"}, "video": {"video"},
            "audio": {"audio"}, "3d": {"3d"}}


class NoResponse(str):
    """An error string that means no HTTP response at all (status 0), so the caller
    reports `error` (could not check) rather than `failed`."""


def _unreached_state(err):
    """`error` when a Jupyter read got no HTTP response, else `failed`."""
    return "error" if isinstance(err, NoResponse) else "failed"


def comfy_output_node_classes(comfy):
    """The set of output-node class names from ComfyUI's /object_info, or None."""
    if comfy is None:
        return None
    status, info = comfy.call("/object_info")
    if status != 200 or not isinstance(info, dict):
        return None
    return {name for name, spec in info.items()
            if isinstance(spec, dict) and spec.get("output_node")}


def _check_node(wf, node_id, cls, kind, wants_audio, files):
    """Results for one expected output node and the files it produced."""
    name = f"{COMFYUI_CHECK}/{wf}/{cls}#{node_id}"
    if kind == "preview":
        return [{"name": name, "state": "skipped", "kind": "structural",
                 "detail": "preview node: the wrapper does not return preview files"}]
    if kind == "unclassified":
        return [{"name": name, "state": "error", "kind": "structural",
                 "detail": f"unclassified output node {cls}: add it to the known output "
                           f"kinds so its output can be checked"}]
    if not files:
        return [{"name": name, "state": "failed", "kind": "structural",
                 "detail": f"expected {kind} output, the node produced no files"}]
    results = []
    for i, (label, data, err) in enumerate(files, 1):
        fname = f"{name}/{i}"
        if not data:
            results.append({"name": fname, "state": _unreached_state(err), "kind": "structural",
                            "detail": f"{label}: output bytes unavailable: {err}"})
            continue
        got = _file_kind(data)
        if got not in _ACCEPTS[kind]:
            results.append({"name": fname, "state": "failed", "kind": "structural",
                            "detail": f"{label}: expected {kind}, got "
                                      f"{got or 'an unrecognised file'} ({len(data)} bytes)"})
            continue
        if wants_audio:
            if data[4:8] == b"ftyp" and not _mp4_has_audio(data):
                results.append({"name": fname, "state": "failed", "kind": "structural",
                                "detail": f"{label}: the node is fed audio but the video has "
                                          f"no sound track"})
                continue
        structural, content = check_output_file(f"{wf}/{cls}#{node_id}/{i}", data)
        structural["name"], content["name"] = fname, fname + ":content"
        if structural["state"] == "passed":
            structural["detail"] = f"{label}: {kind}" + (
                " with audio" if wants_audio else "") + f"; {structural['detail']}"
        results.extend([structural, content])
    return results


def _check_workflow(name, wf, wrapper, jupyter, output_classes, timeout, results):
    """Run one provisioned workflow and check its outputs, appending to `results`."""
    text, err = jupyter.read_text(f"{COMFY_PAYLOADS_DIR}/{name}")
    try:
        payload = json.loads(text) if text is not None else None
    except ValueError as e:
        payload, err = None, f"not valid JSON: {e}"
    if payload is None:
        results.append({"name": f"{COMFYUI_CHECK}/{wf}", "state": _unreached_state(err),
                        "kind": "structural", "detail": f"cannot read payload: {err}"})
        return
    inp = payload.get("input") or {}
    workflow = inp.get("workflow_json") or {}
    if not workflow and inp.get("modifier"):
        base = f"{COMFY_MODIFIER_WORKFLOWS_DIR}/{str(inp['modifier']).lower()}.json"
        text, err = jupyter.read_text(base)
        try:
            workflow = json.loads(text) if text is not None else None
        except ValueError as e:
            workflow, err = None, f"not valid JSON: {e}"
        if not workflow:
            results.append({"name": f"{COMFYUI_CHECK}/{wf}", "state": "error",
                            "kind": "structural",
                            "detail": f"modifier {inp['modifier']!r}: cannot read its base "
                                      f"workflow {base} ({err}), so its output nodes are "
                                      f"unknown"})
            return
    expected = expected_outputs(workflow, output_classes)
    status, r = wrapper.call_raw_with_headers(
        "/generate/sync", json.dumps(payload).encode(), "application/json",
        {"x-return-outputs-as-base64": "true"}, timeout=timeout)
    if status == 0:
        results.append(_no_response(f"{COMFYUI_CHECK}/{wf}", "/generate/sync", r,
                                    kind="structural"))
        return
    if status != 200 or not isinstance(r, dict) or r.get("status") != "completed":
        msg = r.get("message") if isinstance(r, dict) else r
        results.append({"name": f"{COMFYUI_CHECK}/{wf}", "state": "failed",
                        "kind": "structural",
                        "detail": f"HTTP {status}, status="
                                  f"{r.get('status') if isinstance(r, dict) else '?'}: "
                                  f"{str(msg)[:200]}"})
        return
    outputs = r.get("output") or []
    if not expected:
        results.append({"name": f"{COMFYUI_CHECK}/{wf}", "state": "failed",
                        "kind": "structural",
                        "detail": "the workflow has no output node to verify"})
        return
    results.append({"name": f"{COMFYUI_CHECK}/{wf}", "state": "passed", "kind": "structural",
                    "detail": f"completed; {len(outputs)} file(s) from "
                              f"{len(expected)} output node(s); generation "
                              f"{(r.get('timings') or {}).get('generation_ms', '?')} ms"})
    by_node = {}
    for o in outputs:
        data, ferr = None, None
        if o.get("data"):
            data = base64.b64decode(o["data"])
        elif o.get("local_path"):                # too large to inline (video)
            data, ferr = jupyter.read_bytes(o["local_path"].lstrip("/"))
        else:
            ferr = o.get("error") or "no data and no local_path"
        by_node.setdefault(str(o.get("node_id")), []).append(
            (o.get("filename", "?"), data, ferr))
    for node_id, (cls, kind, wants_audio) in sorted(expected.items()):
        files = by_node.pop(node_id, [])
        results.extend(_guarded(
            f"{COMFYUI_CHECK}/{wf}/{cls}#{node_id}",
            lambda: _check_node(wf, node_id, cls, kind, wants_audio, files)))
    for node_id, files in sorted(by_node.items()):
        results.append({"name": f"{COMFYUI_CHECK}/{wf}/#{node_id}", "state": "error",
                        "kind": "structural",
                        "detail": f"{len(files)} file(s) from node {node_id}, which is not a "
                                  f"recognised output node in this workflow"})


def check_comfyui_workflows(wrapper, jupyter, comfy=None, timeout=COMFY_GENERATE_TIMEOUT,
                            deadline=None):
    """Run EVERY provisioned workflow through the public API wrapper and check that
    every output node delivered what it is built to produce (ADR 0059: if we ship a
    workflow, we verify it).

    Output nodes are discovered from ComfyUI's /object_info, so custom packs are
    covered; their expected kind comes from a known table (unclassified nodes are an
    error, never a pass). Each node must deliver at least one file of its kind; a
    video node fed audio must carry a sound track. Image content is checked for
    blank or uniform output; video, audio and 3D content checks are not written yet.

    A request that gets no HTTP response is an `error` (not checked), not a failure.
    Once `deadline` (contract.Deadline) has passed, each workflow not yet run is
    reported by name as an `error`. A workflow, or one node's check, that raises
    unexpectedly is one `error` result under its own name; the rest still run.
    """
    names, err = jupyter.list_dir(COMFY_PAYLOADS_DIR)
    if names is None:
        return [{"name": COMFYUI_CHECK, "state": _unreached_state(err), "kind": "structural",
                 "detail": f"cannot list {COMFY_PAYLOADS_DIR}: {err}"}]
    names = [n for n in names if n.endswith(".json")]
    if not names:
        return [{"name": COMFYUI_CHECK, "state": "failed", "kind": "structural",
                 "detail": f"no workflow payloads in {COMFY_PAYLOADS_DIR}"}]
    output_classes = comfy_output_node_classes(comfy)
    results = []
    if output_classes is None:
        results.append({"name": COMFYUI_OBJECT_INFO, "state": "error",
                        "kind": "structural",
                        "detail": "ComfyUI /object_info unavailable: output nodes found from "
                                  "the known table only, so custom output nodes may be missed"})
    for name in names:
        wf = name[:-5]
        if deadline is not None and deadline.expired():
            results.append(_deadline_result(f"{COMFYUI_CHECK}/{wf}", deadline))
            continue
        mark = len(results)
        try:
            _check_workflow(name, wf, wrapper, jupyter, output_classes, timeout, results)
        except Exception as e:                                    # noqa: BLE001
            # One result for the workflow, not a half-written set of them.
            del results[mark:]
            results.append({"name": f"{COMFYUI_CHECK}/{wf}", "state": "error",
                            "kind": "structural",
                            "detail": f"the check raised {type(e).__name__}: {str(e)[:300]}"})
    return results


def _run(ctx):
    return check_comfyui_workflows(ctx.wrapper, ctx.jupyter, comfy=ctx.comfy,
                                   timeout=ctx.generate_timeout, deadline=ctx.deadline)


TYPE = TestType(
    name="comfyui",
    axis=ROUTE,
    requires=(COMFYUI_CAPABILITY,),
    images=COMFYUI,
    results=(
        ResultName(COMFYUI_CHECK, STRUCTURAL, COMFYUI, False),
        ResultName(COMFYUI_OBJECT_INFO, STRUCTURAL, COMFYUI, False),
        ResultName(f"{COMFYUI_CHECK}/<workflow>", STRUCTURAL, COMFYUI, True),
        # A node's own name carries only a skip or a failure; each file it delivered is
        # reported under /<n>. `<class>` is empty for a node that is not an output node.
        ResultName(f"{COMFYUI_CHECK}/<workflow>/<class>#<node>", STRUCTURAL, COMFYUI, False),
        ResultName(f"{COMFYUI_CHECK}/<workflow>/<class>#<node>/<n>", STRUCTURAL, COMFYUI, True),
        ResultName(f"{COMFYUI_CHECK}/<workflow>/<class>#<node>/<n>:content", ANSWER, COMFYUI, True),
    ),
    unrun=(COMFYUI_CHECK,),
    order=70,
    run=_run,
)
