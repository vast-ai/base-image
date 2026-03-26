#!/bin/bash
# Test: torchaudio — import, codec backends, encode/decode round-trips, GPU transforms.
# Runs against all venvs in /venv/ that have torch installed.
#
# Compatibility notes:
#   - torchaudio 2.6–2.8 (torch 2.6–2.8): FFmpeg-based, has list_audio_backends/ffmpeg_utils
#   - torchaudio 2.9+ (torch 2.9+): TorchCodec-based, StreamWriter/StreamReader and
#     ffmpeg_utils removed. torchaudio.save/load still works (delegates to TorchCodec).
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
    local tmp_prefix="/tmp/test_audio_${venv_name}"

    echo ""
    echo "  ${label} ── venv: ${venv_dir}"

    # torchaudio must be importable — it's a required companion
    local ta_version
    ta_version=$("$py" -c "import torchaudio; print(torchaudio.__version__)" 2>/dev/null) \
        || { fail_later "${venv_name}-import" "torchaudio not importable in ${venv_dir}"; return; }

    local cuda_available
    cuda_available=$("$py" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null)

    echo "  ${label} torchaudio: ${ta_version}"

    # Audio backends / codec engine
    "$py" -c "
import torchaudio, os

class SuppressStderr:
    def __enter__(self):
        self._fd = os.dup(2)
        self._devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self._devnull, 2)
    def __exit__(self, *a):
        os.dup2(self._fd, 2)
        os.close(self._fd)
        os.close(self._devnull)

if hasattr(torchaudio, 'list_audio_backends'):
    backends = torchaudio.list_audio_backends()
    print(f'  ${label} audio backends: {\", \".join(backends) if backends else \"none\"}')
else:
    with SuppressStderr():
        try:
            import torchcodec
            if hasattr(torchcodec, '_core') and hasattr(torchcodec._core, 'ops'):
                from torchcodec._core.ops import load_torchcodec_shared_libraries
                load_torchcodec_shared_libraries()
            ver = getattr(torchcodec, '__version__', 'unknown')
            print(f'  ${label} codec engine: TorchCodec v{ver}')
        except (ImportError, OSError) as e:
            print(f'  ${label} codec engine: TorchCodec not loadable ({e})')
" 2>&1

    # Codec round-trip: WAV (torchaudio.save/load works across all versions)
    "$py" -c "
import torch, torchaudio
sr = 16000
waveform = torch.sin(2 * 3.14159 * 440 * torch.linspace(0, 1.0, sr)).unsqueeze(0)
path = '${tmp_prefix}.wav'
torchaudio.save(path, waveform, sr)
loaded, loaded_sr = torchaudio.load(path)
assert loaded.shape[0] > 0, f'empty waveform after load'
assert loaded_sr == sr, f'sample rate mismatch: expected {sr}, got {loaded_sr}'
print('  ${label} WAV round-trip: ok')
" 2>&1 || fail_later "${venv_name}-wav" "WAV codec round-trip failed"

    # Codec: MP3 encode
    "$py" -c "
import torch, torchaudio
sr = 16000
waveform = torch.sin(2 * 3.14159 * 440 * torch.linspace(0, 1.0, sr)).unsqueeze(0)
torchaudio.save('${tmp_prefix}.mp3', waveform, sr)
print('  ${label} MP3 encode: ok')
" 2>&1 || fail_later "${venv_name}-mp3" "MP3 encode failed"

    # Codec: FLAC round-trip
    "$py" -c "
import torch, torchaudio
sr = 16000
waveform = torch.sin(2 * 3.14159 * 440 * torch.linspace(0, 1.0, sr)).unsqueeze(0)
torchaudio.save('${tmp_prefix}.flac', waveform, sr)
loaded, _ = torchaudio.load('${tmp_prefix}.flac')
assert loaded.shape[0] > 0, 'empty waveform after FLAC load'
print('  ${label} FLAC round-trip: ok')
" 2>&1 || fail_later "${venv_name}-flac" "FLAC round-trip failed"

    # MelSpectrogram
    if has_gpu && [[ "$cuda_available" == "True" ]]; then
        "$py" -c "
import torch, torchaudio
sr = 16000
waveform = torch.sin(2 * 3.14159 * 440 * torch.linspace(0, 1.0, sr)).unsqueeze(0)
mel = torchaudio.transforms.MelSpectrogram(sample_rate=sr, n_mels=64).cuda()
spec = mel(waveform.cuda())
torch.cuda.synchronize()
print(f'  ${label} MelSpectrogram on GPU: ok (shape {tuple(spec.shape)})')
" 2>&1 || fail_later "${venv_name}-mel-gpu" "MelSpectrogram on GPU failed"
    else
        "$py" -c "
import torch, torchaudio
sr = 16000
waveform = torch.sin(2 * 3.14159 * 440 * torch.linspace(0, 1.0, sr)).unsqueeze(0)
mel = torchaudio.transforms.MelSpectrogram(sample_rate=sr, n_mels=64)
spec = mel(waveform)
print(f'  ${label} MelSpectrogram on CPU: ok (shape {tuple(spec.shape)})')
" 2>&1 || fail_later "${venv_name}-mel-cpu" "MelSpectrogram on CPU failed"
    fi
}

for venv_dir in "${TORCH_VENVS[@]}"; do
    test_venv "$venv_dir"
done

report_failures
test_pass "torchaudio verified across ${#TORCH_VENVS[@]} venv(s)"
