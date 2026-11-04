#!/usr/bin/env python3
"""GRPO reinforcement learning on one GPU with Unsloth.

The model writes several answers per prompt, a scoring function grades each one, and
training nudges the model toward the higher-scoring answers.

What to learn comes from a task file: a small Python file with the data, the prompt and
the scoring. Two are built in (tasks/): "math" (the default, grade-school math) and
"toolcall" (answer a request with JSON function calls). Point --task at your own file or
a URL to train on anything a program can check. See tasks/README.md.

Every setting is a command-line flag, and every flag also reads an environment variable
(GRPO_<FLAG>), so the same script works from a terminal, a notebook, a template's env
vars, or an agent. Each run writes `results.json` (time, peak GPU memory, reward trend,
accuracy before and after) next to the LoRA adapter.

    python train_grpo.py                          # defaults: Qwen3-1.7B, math (GSM8K), 100 steps
    python train_grpo.py --max-steps 20           # quick smoke test
    python train_grpo.py --task toolcall          # built-in tool-calling task
    python train_grpo.py --task ./my_task.py      # your own task
    GRPO_TASK=https://example.com/my_task.py python train_grpo.py
    python train_grpo.py --help                   # every option
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone

# Lets vLLM and training share GPU memory; Unsloth's recipe turns it on (~30% more context).
os.environ.setdefault("UNSLOTH_VLLM_STANDBY", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(HERE, "tasks")


def env(name, default):
    return os.environ.get(f"GRPO_{name}", default)


def env_bool(name, default):
    value = os.environ.get(f"GRPO_{name}")
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    a = p.add_argument
    a("--task", default=env("TASK", "math"),
      help="What to train on: a built-in task (math, toolcall), a task file path, or an "
           "http(s) URL to one. [GRPO_TASK]")
    a("--model", default=env("MODEL", "unsloth/Qwen3-1.7B-Base"),
      help="Hugging Face model id. [GRPO_MODEL]")
    a("--dataset", default=env("DATASET", ""),
      help="Dataset choice, for tasks that offer one. math: gsm8k (default, grade-school "
           "math) or dapo-math (harder competition math). [GRPO_DATASET]")
    a("--max-steps", type=int, default=int(env("MAX_STEPS", 100)),
      help="GRPO training steps. [GRPO_MAX_STEPS]")
    a("--max-seq-length", type=int, default=int(env("MAX_SEQ_LENGTH", 2048)),
      help="Prompt + answer length in tokens. Biggest driver of memory. [GRPO_MAX_SEQ_LENGTH]")
    a("--num-generations", type=int, default=int(env("NUM_GENERATIONS", 4)),
      help="Answers sampled per prompt. Lower if out of memory. [GRPO_NUM_GENERATIONS]")
    a("--batch-size", type=int, default=int(env("BATCH_SIZE", 1)),
      help="Prompts per step. [GRPO_BATCH_SIZE]")
    a("--grad-accum", type=int, default=int(env("GRAD_ACCUM", 1)),
      help="Gradient accumulation steps. [GRPO_GRAD_ACCUM]")
    a("--learning-rate", type=float, default=float(env("LEARNING_RATE", 5e-6)),
      help="[GRPO_LEARNING_RATE]")
    a("--lora-rank", type=int, default=int(env("LORA_RANK", 32)),
      help="LoRA rank. Higher = more capacity, slower. [GRPO_LORA_RANK]")
    a("--load-in-4bit", action=argparse.BooleanOptionalAction,
      default=env_bool("LOAD_IN_4BIT", False),
      help="QLoRA: load the model in 4-bit to fit bigger models. [GRPO_LOAD_IN_4BIT]")
    a("--fast-inference", action=argparse.BooleanOptionalAction,
      default=env_bool("FAST_INFERENCE", True),
      help="Use vLLM to generate answers (much faster). Turn off for models vLLM "
           "can't run, e.g. Qwen3.5. [GRPO_FAST_INFERENCE]")
    a("--gpu-memory-utilization", type=float, default=float(env("GPU_MEMORY_UTILIZATION", 0.9)),
      help="Share of GPU memory vLLM may take. Lower if out of memory. "
           "[GRPO_GPU_MEMORY_UTILIZATION]")
    a("--warmup-sft-steps", type=int, default=int(env("WARMUP_SFT_STEPS", 0)),
      help="Short supervised pass first, to teach the answer format to a model that "
           "can't follow it (tasks with worked answers only, e.g. math on gsm8k). Off by "
           "default: on Qwen3-4B-Base it lowered accuracy. [GRPO_WARMUP_SFT_STEPS]")
    a("--eval-samples", type=int, default=int(env("EVAL_SAMPLES", 200)),
      help="Held-out prompts scored before and after training. 0 to skip. "
           "Needs --fast-inference. [GRPO_EVAL_SAMPLES]")
    a("--output-dir", default=env("OUTPUT_DIR", ""),
      help="Where checkpoints, the LoRA and results.json go. "
           "Default /workspace/grpo-runs/<timestamp>-<task>. [GRPO_OUTPUT_DIR]")
    a("--save-steps", type=int, default=int(env("SAVE_STEPS", 50)),
      help="Checkpoint every N steps. [GRPO_SAVE_STEPS]")
    a("--save-merged", action=argparse.BooleanOptionalAction,
      default=env_bool("SAVE_MERGED", False),
      help="Also save a full merged 16-bit model (large). [GRPO_SAVE_MERGED]")
    a("--hf-repo", default=env("HF_REPO", ""),
      help="Push the LoRA to this Hugging Face repo (needs HF_TOKEN). [GRPO_HF_REPO]")
    a("--report-to", default=env("REPORT_TO", "none"),
      help="none, wandb, tensorboard or trackio. [GRPO_REPORT_TO]")
    a("--price-per-hour", type=float, default=float(env("PRICE_PER_HOUR", 0) or 0),
      help="Your instance's $/hr, to put a cost in results.json. [GRPO_PRICE_PER_HOUR]")
    a("--seed", type=int, default=int(env("SEED", 3407)), help="[GRPO_SEED]")
    args = p.parse_args(argv)
    if not args.output_dir:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = "/workspace" if os.path.isdir("/workspace") else "."
        args.output_dir = os.path.join(root, "grpo-runs", f"{stamp}-{task_name(args.task)}")
    return args


# ---------------------------------------------------------------- tasks

REQUIRED = ("load", "prompt", "rewards", "check")


def task_name(spec):
    base = os.path.basename(spec.split("?")[0].rstrip("/"))
    base = re.sub(r"\.py$", "", base)
    return re.sub(r"[^A-Za-z0-9_.-]", "-", base) or "task"


def resolve_task(spec, dest):
    """Copy the task file named by spec (built-in name, path or URL) to dest, and return
    where it came from. The copy in the run folder is what runs, so a run always records
    the exact task code it trained with."""
    if re.match(r"https?://", spec):
        with urllib.request.urlopen(spec, timeout=60) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return spec
    path = spec
    if os.sep not in spec and not spec.endswith(".py"):
        path = os.path.join(TASKS_DIR, f"{spec}_task.py")
        if not os.path.isfile(path):
            builtins = sorted(f[:-8] for f in os.listdir(TASKS_DIR) if f.endswith("_task.py"))
            raise SystemExit(f"[grpo] unknown task {spec!r}. Built-in tasks: {', '.join(builtins)}; "
                             "or pass a path or URL to a task file.")
    if not os.path.isfile(path):
        raise SystemExit(f"[grpo] task file not found: {path}")
    shutil.copyfile(path, dest)
    return os.path.abspath(path)


def import_task(path):
    """Load a task file as a module and check it has what the trainer needs."""
    # A unique module name, so a task file called math.py can't shadow the stdlib module.
    spec = importlib.util.spec_from_file_location("grpo_task", path)
    task = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(task)
    missing = [name for name in REQUIRED if not callable(getattr(task, name, None))]
    if missing:
        raise SystemExit(f"[grpo] task file {path} is missing: {', '.join(missing)}. "
                         "See tasks/README.md.")
    return task


def load_task(spec, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, "task.py")
    source = resolve_task(spec, dest)
    with open(dest, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return import_task(dest), {"task": task_name(spec), "task_source": source,
                               "task_sha256": digest}


def check_result(task, reply, answer):
    """task.check returns a bool or a dict with "correct" and any other fields to record."""
    result = task.check(reply, answer)
    return dict(result) if isinstance(result, dict) else {"correct": bool(result)}


def summarize(checks):
    """Averages every bool or number field across checks: {"correct": 0.61, ...}."""
    totals = {}
    for c in checks:
        for k, v in c.items():
            if isinstance(v, (bool, int, float)):
                totals.setdefault(k, []).append(float(v))
    return {k: round(sum(v) / len(v), 4) for k, v in totals.items()}


def stop_strings(task, tokenizer):
    return [tokenizer.eos_token] + list(getattr(task, "STOP", []))


# ---------------------------------------------------------------- memory

class GpuMemoryWatcher:
    """Samples nvidia-smi so peak memory includes vLLM's share, which torch can't see."""

    def __init__(self, interval=2.0):
        self.interval = interval
        self.peak_mb = 0
        self.total_mb = 0
        self.name = "unknown"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _query(self):
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
             "--format=csv,noheader,nounits", "-i", "0"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        name, used, total = [x.strip() for x in out.split(",")]
        return name, int(used), int(total)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.name, used, self.total_mb = self._query()
                self.peak_mb = max(self.peak_mb, used)
            except Exception:
                pass
            self._stop.wait(self.interval)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)


# ---------------------------------------------------------------- model and eval

def load_model(settings, task=None, gpu_memory_utilization=None):
    """Base model + LoRA wrapper, from a settings dict (parsed args, or the "settings"
    saved in a run's results.json). Applies the task's chat template if it has one."""
    from unsloth import FastLanguageModel

    kwargs = dict(
        model_name=settings["model"],
        max_seq_length=settings["max_seq_length"],
        load_in_4bit=settings["load_in_4bit"],
        fast_inference=settings["fast_inference"],
        max_lora_rank=settings["lora_rank"],
    )
    if settings["fast_inference"]:
        kwargs["gpu_memory_utilization"] = gpu_memory_utilization or settings["gpu_memory_utilization"]
    model, tokenizer = FastLanguageModel.from_pretrained(**kwargs)
    model = FastLanguageModel.get_peft_model(
        model,
        r=settings["lora_rank"],
        lora_alpha=settings["lora_rank"] * 2,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
        random_state=settings["seed"],
    )
    if getattr(task, "CHAT_TEMPLATE", None):
        tokenizer.chat_template = task.CHAT_TEMPLATE
    return model, tokenizer


def load_trained(run_dir, gpu_memory_utilization=0.6):
    """Reload a finished run for inference: (model, tokenizer, lora_request, task) for
    model.fast_generate. Uses the run's own saved settings and task file, so the base
    model and prompt format match what the LoRA was trained on."""
    with open(os.path.join(run_dir, "results.json")) as f:
        settings = json.load(f)["settings"]
    task = import_task(os.path.join(run_dir, "task.py"))
    model, tokenizer = load_model(settings, task, gpu_memory_utilization)
    return model, tokenizer, model.load_lora(os.path.join(run_dir, "lora")), task


def evaluate(model, tokenizer, task, rows, max_tokens, lora_request=None, save_path=None):
    """Scores held-out rows with task.check (greedy decoding) and returns the averages,
    e.g. {"correct": 0.61}. Writes every prompt, reply and verdict to save_path (JSON
    lines) if given."""
    from vllm import SamplingParams

    prompts = [tokenizer.apply_chat_template(task.prompt(r), tokenize=False,
                                             add_generation_prompt=True) for r in rows]
    params = SamplingParams(temperature=0.0, max_tokens=max_tokens,
                            stop=stop_strings(task, tokenizer))
    outputs = model.fast_generate(prompts, sampling_params=params, lora_request=lora_request,
                                  use_tqdm=False)
    checks, out_rows = [], []
    for r, out in zip(rows, outputs):
        text = out.outputs[0].text
        c = check_result(task, text, r["answer"])
        checks.append(c)
        out_rows.append({"prompt": task.prompt(r)[-1]["content"], "answer": r["answer"],
                         **c, "output": text})
    if save_path:
        with open(save_path, "w") as f:
            f.writelines(json.dumps(x) + "\n" for x in out_rows)
    return summarize(checks)


def record_eval(results, metrics, when):
    """accuracy_<when> for "correct", <field>_<when> for any other field."""
    for k, v in metrics.items():
        results[f"accuracy_{when}" if k == "correct" else f"{k}_{when}"] = v


# ---------------------------------------------------------------- main

def main(argv=None):
    args = parse_args(argv)
    os.makedirs(args.output_dir, exist_ok=True)
    task, task_info = load_task(args.task, args.output_dir)
    print(f"[grpo] task: {json.dumps(task_info)}", flush=True)
    print(f"[grpo] settings: {json.dumps(vars(args))}", flush=True)

    if args.fast_inference:
        try:
            import vllm  # noqa: F401
        except ImportError:
            print("[grpo] vLLM is not installed; falling back to --no-fast-inference (slower).")
            args.fast_inference = False
    if not args.fast_inference and args.eval_samples:
        print("[grpo] eval needs vLLM; skipping before/after accuracy.")
        args.eval_samples = 0

    watcher = GpuMemoryWatcher().start()
    timings = {}
    t0 = time.time()

    import unsloth  # noqa: F401  # side effect: must load before trl/transformers so its patches apply
    import torch
    from trl import GRPOConfig, GRPOTrainer, SFTConfig, SFTTrainer

    model, tokenizer = load_model(vars(args), task)
    timings["load_s"] = round(time.time() - t0, 1)

    settings = vars(args)
    train = task.load("train", settings).shuffle(seed=args.seed)
    test = task.load("test", settings).shuffle(seed=args.seed)

    def prompt_tokens(row):
        return len(tokenizer.apply_chat_template(task.prompt(row), add_generation_prompt=True))

    # Prompt length: cover 90% of prompts, drop the long tail.
    sample = train.select(range(min(5000, len(train))))
    lengths = sorted(prompt_tokens(r) for r in sample)
    max_prompt_length = lengths[int(len(lengths) * 0.9)] + 1
    train = train.filter(lambda r: prompt_tokens(r) <= max_prompt_length)
    max_completion_length = args.max_seq_length - max_prompt_length
    if getattr(task, "MAX_ANSWER_TOKENS", None):
        max_completion_length = min(max_completion_length, task.MAX_ANSWER_TOKENS)
    # The held-out set keeps long prompts (scoring the same questions as published runs);
    # only prompts with no room left for an answer are dropped.
    test = test.filter(lambda r: prompt_tokens(r) <= args.max_seq_length - min(256, max_completion_length))
    print(f"[grpo] {len(train)} training prompts, prompt <= {max_prompt_length} tokens, "
          f"answer <= {max_completion_length} tokens", flush=True)

    results = {
        "model": args.model,
        **task_info,
        "dataset": args.dataset or getattr(task, "DATASET", None),
        "settings": settings,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    eval_set = test.select(range(min(args.eval_samples, len(test)))) if args.eval_samples else None
    if eval_set is not None:
        t = time.time()
        record_eval(results, evaluate(model, tokenizer, task, eval_set, max_completion_length,
                                      save_path=os.path.join(args.output_dir, "eval_before.jsonl")),
                    "before")
        timings["eval_before_s"] = round(time.time() - t, 1)
        print(f"[grpo] accuracy before: {results.get('accuracy_before')}", flush=True)

    # 1. Optional short supervised pass so a base model learns the answer format.
    warmup_reply = getattr(task, "warmup_reply", None)
    if args.warmup_sft_steps and warmup_reply is None:
        print("[grpo] this task has no warmup_reply(); skipping --warmup-sft-steps.")
    elif args.warmup_sft_steps:
        t = time.time()
        sft_rows = train.select(range(min(len(train), args.warmup_sft_steps * 2)))
        sft_data = sft_rows.map(lambda r: {"text": tokenizer.apply_chat_template(
            task.prompt(r) + [{"role": "assistant", "content": warmup_reply(r) or ""}],
            tokenize=False)}).filter(lambda r: bool(warmup_reply(r)))
        if len(sft_data):
            SFTTrainer(
                model=model,
                tokenizer=tokenizer,
                train_dataset=sft_data,
                args=SFTConfig(
                    dataset_text_field="text",
                    per_device_train_batch_size=1,
                    gradient_accumulation_steps=1,
                    max_steps=args.warmup_sft_steps,
                    warmup_steps=5,
                    learning_rate=2e-4,
                    logging_steps=10,
                    optim="adamw_8bit",
                    seed=args.seed,
                    output_dir=os.path.join(args.output_dir, "sft-warmup"),
                    save_strategy="no",
                    report_to="none",
                ),
            ).train()
            timings["warmup_sft_s"] = round(time.time() - t, 1)
            torch.cuda.empty_cache()
        else:
            print("[grpo] no worked answers in this dataset; skipping --warmup-sft-steps.")

    # 2. GRPO. Other columns pass through to the reward functions as keyword arguments.
    grpo_data = train.map(lambda r: {"prompt": task.prompt(r)},
                          remove_columns=[c for c in train.column_names if c == "prompt"])
    config = dict(
        temperature=1.0,
        learning_rate=args.learning_rate,
        weight_decay=0.001,
        warmup_ratio=0.1,
        lr_scheduler_type="linear",
        optim="adamw_8bit",
        logging_steps=1,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_generations=args.num_generations,
        max_prompt_length=max_prompt_length,
        max_completion_length=max_completion_length,
        max_steps=args.max_steps,
        save_steps=args.save_steps,
        report_to=args.report_to,
        output_dir=os.path.join(args.output_dir, "checkpoints"),
        seed=args.seed,
    )
    if args.fast_inference:
        from vllm import SamplingParams
        config["vllm_sampling_params"] = SamplingParams(
            min_p=0.1, top_p=1.0, top_k=-1, seed=args.seed,
            stop=stop_strings(task, tokenizer), include_stop_str_in_output=True,
        )
    trainer = GRPOTrainer(
        model=model,
        processing_class=tokenizer,
        reward_funcs=task.rewards(tokenizer),
        args=GRPOConfig(**config),
        train_dataset=grpo_data,
    )
    t = time.time()
    trainer.train()
    timings["grpo_s"] = round(time.time() - t, 1)

    rewards = [h["reward"] for h in trainer.state.log_history if "reward" in h]
    window = max(1, min(10, len(rewards) // 4))
    if rewards:
        results["reward_first_steps"] = round(sum(rewards[:window]) / window, 3)
        results["reward_last_steps"] = round(sum(rewards[-window:]) / window, 3)
        results["reward_window_steps"] = window
    with open(os.path.join(args.output_dir, "log_history.json"), "w") as f:
        json.dump(trainer.state.log_history, f, indent=1)

    # 3. Save.
    lora_dir = os.path.join(args.output_dir, "lora")
    model.save_pretrained(lora_dir)
    tokenizer.save_pretrained(lora_dir)
    results["lora_dir"] = lora_dir
    if args.save_merged:
        merged_dir = os.path.join(args.output_dir, "merged-16bit")
        model.save_pretrained_merged(merged_dir, tokenizer, save_method="merged_16bit")
        results["merged_dir"] = merged_dir
    if args.hf_repo:
        model.push_to_hub(args.hf_repo, token=os.environ.get("HF_TOKEN"))
        tokenizer.push_to_hub(args.hf_repo, token=os.environ.get("HF_TOKEN"))
        results["hf_repo"] = args.hf_repo

    if eval_set is not None:
        t = time.time()
        record_eval(results, evaluate(model, tokenizer, task, eval_set, max_completion_length,
                                      lora_request=model.load_lora(lora_dir),
                                      save_path=os.path.join(args.output_dir, "eval_after.jsonl")),
                    "after")
        timings["eval_after_s"] = round(time.time() - t, 1)
        print(f"[grpo] accuracy after: {results.get('accuracy_after')}", flush=True)

    watcher.stop()
    timings["total_s"] = round(time.time() - t0, 1)
    results.update({
        "gpu": watcher.name,
        "gpu_memory_total_gb": round(watcher.total_mb / 1024, 1),
        "gpu_memory_peak_gb": round(watcher.peak_mb / 1024, 1),
        "timings": timings,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
    if args.price_per_hour:
        results["cost_usd"] = round(args.price_per_hour * timings["total_s"] / 3600, 3)
    with open(os.path.join(args.output_dir, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("[grpo] results: " + json.dumps({k: v for k, v in results.items() if k != "settings"}),
          flush=True)
    print(f"[grpo] done. Output in {args.output_dir}", flush=True)
    return results


if __name__ == "__main__":
    main()
