## AI Toolkit — ostris (this image)

The PyTorch image plus a preinstalled **ostris AI Toolkit** (upstream `ostris/ai-toolkit`) — a
diffusion-model **training** suite (LoRA / fine-tunes for FLUX and friends). Everything in
base.md and pytorch.md applies unchanged (torch is in `/venv/main`); this file covers what it
adds. It is a training tool, **not** an inference/OpenAI endpoint — the deliverable is a trained
model file. Get the externally callable URL + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### The app — web UI (service "ai-toolkit")

A Next.js web UI, supervisor service **`ai-toolkit`** (`npm run start`, override via
**`AI_TOOLKIT_START_CMD`**), internal `127.0.0.1:8675`. You build a job in the UI and click
train; the UI worker spawns `run.py` training jobs as subprocesses. One UI service + transient
trainer processes — there is no always-on training API.

### Driving a run headlessly (preferred for automation)

The UI just wraps a CLI an agent can call directly. From `${WORKSPACE}/ai-toolkit` in
`/venv/main`, training is a **YAML job config** fed to `run.py`:
```
cd ${WORKSPACE}/ai-toolkit && . /venv/main/bin/activate
python run.py config/my_job.yaml
```
Start from a template in **`config/examples/*.yaml`** (e.g. `train_lora_flux_24gb.yaml`), copy it,
and edit the `datasets[].folder_path`, `training_folder`, model, and hyperparameters.

### Data, models, outputs

Under `${WORKSPACE}/ai-toolkit` (persisted on the workspace):
- **Datasets** — `datasets/` (images + matching `.txt` captions); referenced by `folder_path` in
  the job YAML.
- **Outputs** — `output/<job_name>/`: trained LoRA/checkpoints + sample images. This is what you
  pull back.
- **Base weights** — **download on first use** into the HF cache; gated models (e.g. FLUX) need
  **`HF_TOKEN`** set in the instance env (not pre-wired here).

The app runs in `/venv/main` and **starts only after provisioning completes** (it waits for the
portal config / `/.provisioning`), so during boot it may be intentionally down. This image is built
**amd64-only** (torchcodec has no aarch64 wheels) and pins torch 2.9.1.
