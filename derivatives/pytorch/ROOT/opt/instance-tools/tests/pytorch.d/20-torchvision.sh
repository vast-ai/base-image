#!/bin/bash
# Test: torchvision — import, transforms, model inference, image I/O, ops.
# Runs against all venvs in /venv/ that have torch installed.
source "$(dirname "$0")/../lib.sh"

# ── Discover torch venvs ──────────────────────────────────────────────

TORCH_VENVS=()
for venv_dir in /venv/*/; do
    [[ -f "${venv_dir}bin/activate" ]] || continue
    if "${venv_dir}bin/python3" -c "import torch" 2>/dev/null; then
        TORCH_VENVS+=("$venv_dir")
    fi
done

[[ ${#TORCH_VENVS[@]} -gt 0 ]] || test_skip "no venvs with torch found in /venv/"
echo "  found ${#TORCH_VENVS[@]} torch venv(s): ${TORCH_VENVS[*]}"

# ── Per-venv validation ───────────────────────────────────────────────

test_venv() {
    local venv_dir="$1"
    local venv_name
    venv_name=$(basename "$venv_dir")
    local py="${venv_dir}bin/python3"
    local label="[${venv_name}]"

    echo ""
    echo "  ${label} ── venv: ${venv_dir}"

    # torchvision must be importable — it's a required companion
    local tv_version
    tv_version=$("$py" -c "import torchvision; print(torchvision.__version__)" 2>&1) \
        || { fail_later "${venv_name}-import" "torchvision not importable in ${venv_dir}: ${tv_version}"; return; }

    local cuda_available
    cuda_available=$("$py" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null)

    echo "  ${label} torchvision: ${tv_version}"

    # Transforms pipeline
    "$py" -c "
from torchvision import transforms
t = transforms.Compose([
    transforms.Resize(224),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])
print('  ${label} transforms pipeline: ok')
" 2>&1 || fail_later "${venv_name}-transforms" "transforms pipeline failed"

    # Model load
    "$py" -c "
import torch, torchvision
model = torchvision.models.resnet18(weights=None)
print('  ${label} ResNet-18 load (no weights): ok')
" 2>&1 || fail_later "${venv_name}-model-load" "ResNet-18 load failed"

    # GPU inference
    if has_gpu && [[ "$cuda_available" == "True" ]]; then
        "$py" -c "
import torch, torchvision
model = torchvision.models.resnet18(weights=None).cuda()
dummy = torch.randn(1, 3, 224, 224, device='cuda')
with torch.no_grad():
    out = model(dummy)
torch.cuda.synchronize()
assert out.shape == (1, 1000), f'unexpected shape {out.shape}'
print('  ${label} ResNet-18 GPU inference: ok')
" 2>&1 || fail_later "${venv_name}-model-gpu" "ResNet-18 GPU inference failed"
    else
        echo "  ${label} skip: GPU inference (no CUDA)"
    fi

    # Image I/O
    "$py" -c "
import torch
from torchvision.io import read_image, write_png
fake = torch.randint(0, 255, (3, 64, 64), dtype=torch.uint8)
write_png(fake, '/tmp/test_tv_${venv_name}.png')
loaded = read_image('/tmp/test_tv_${venv_name}.png')
assert loaded.shape == (3, 64, 64), f'unexpected shape {loaded.shape}'
print('  ${label} image I/O (write_png / read_image): ok')
" 2>&1 || fail_later "${venv_name}-image-io" "image I/O failed"

    # Video backend
    local video_backend
    video_backend=$("$py" -c "
import torchvision
try:
    print(torchvision.get_video_backend())
except Exception as e:
    print(f'error: {e}')
" 2>/dev/null)
    echo "  ${label} video backend: ${video_backend:-unknown}"

    # ops.nms
    "$py" -c "
import torch
from torchvision import ops
boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 11, 11]], dtype=torch.float32)
scores = torch.tensor([0.9, 0.8])
$(has_gpu && [[ "$cuda_available" == "True" ]] && echo "boxes, scores = boxes.cuda(), scores.cuda()")
keep = ops.nms(boxes, scores, iou_threshold=0.5)
assert len(keep) > 0, 'nms returned no boxes'
print(f'  ${label} ops.nms: ok (kept {len(keep)} boxes)')
" 2>&1 || fail_later "${venv_name}-nms" "ops.nms failed"
}

for venv_dir in "${TORCH_VENVS[@]}"; do
    test_venv "$venv_dir"
done

report_failures
test_pass "torchvision verified across ${#TORCH_VENVS[@]} venv(s)"
