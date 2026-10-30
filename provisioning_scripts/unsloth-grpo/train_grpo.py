#!/usr/bin/env python3
"""GRPO reinforcement learning on one GPU with Unsloth.

Teaches a small model to solve math word problems: it writes several answers per
question, a scoring function grades each one, and training nudges the model toward the
higher-scoring answers.

Every setting is a command-line flag, and every flag also reads an environment variable
(GRPO_<FLAG>), so the same script works from a terminal, a notebook, a template's env
vars, or an agent. Each run writes `results.json` (time, peak GPU memory, reward before
and after, accuracy before and after) next to the LoRA adapter.

    python train_grpo.py                          # defaults: Qwen3-1.7B, GSM8K, 100 steps
    python train_grpo.py --max-steps 20           # quick smoke test
    GRPO_MODEL=unsloth/Qwen3-8B-Base python train_grpo.py
    python train_grpo.py --help                   # every option
"""

import argparse
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

# Lets vLLM and training share GPU memory; Unsloth's recipe turns it on (~30% more context).
os.environ.setdefault("UNSLOTH_VLLM_STANDBY", "1")

REASONING_START = "<start_working_out>"
REASONING_END = "<end_working_out>"
SOLUTION_START = "<SOLUTION>"
SOLUTION_END = "</SOLUTION>"
SYSTEM_PROMPT = (
    "You are given a problem.\n"
    "Think about the problem and provide your working out.\n"
    f"Place it between {REASONING_START} and {REASONING_END}.\n"
    f"Then, provide your solution between {SOLUTION_START}{SOLUTION_END}"
)


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
    a("--model", default=env("MODEL", "unsloth/Qwen3-1.7B-Base"),
      help="Hugging Face model id. [GRPO_MODEL]")
    a("--dataset", default=env("DATASET", "gsm8k"), choices=["gsm8k", "dapo-math"],
      help="Training questions. gsm8k = grade-school math (fast gains); "
           "dapo-math = harder competition math. [GRPO_DATASET]")
    a("--max-steps", type=int, default=int(env("MAX_STEPS", 100)),
      help="GRPO training steps. [GRPO_MAX_STEPS]")
    a("--max-seq-length", type=int, default=int(env("MAX_SEQ_LENGTH", 2048)),
      help="Prompt + answer length in tokens. Biggest driver of memory. [GRPO_MAX_SEQ_LENGTH]")
    a("--num-generations", type=int, default=int(env("NUM_GENERATIONS", 4)),
      help="Answers sampled per question. Lower if out of memory. [GRPO_NUM_GENERATIONS]")
    a("--batch-size", type=int, default=int(env("BATCH_SIZE", 1)),
      help="Questions per step. [GRPO_BATCH_SIZE]")
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
           "can't follow it. Off by default: on Qwen3-4B-Base it lowered accuracy. "
           "[GRPO_WARMUP_SFT_STEPS]")
    a("--eval-samples", type=int, default=int(env("EVAL_SAMPLES", 200)),
      help="Held-out questions scored before and after training. 0 to skip. "
           "Needs --fast-inference. [GRPO_EVAL_SAMPLES]")
    a("--output-dir", default=env("OUTPUT_DIR", ""),
      help="Where checkpoints, the LoRA and results.json go. "
           "Default /workspace/grpo-runs/<timestamp>. [GRPO_OUTPUT_DIR]")
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
        args.output_dir = os.path.join(root, "grpo-runs", stamp)
    return args


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


# ---------------------------------------------------------------- data

def extract_gsm8k_answer(text):
    return text.split("####")[-1].strip().replace(",", "")


def load_questions(name, split):
    """Returns a dataset with `question`, `answer` (number as text), and `reasoning`."""
    from datasets import load_dataset

    if name == "gsm8k":
        ds = load_dataset("openai/gsm8k", "main", split=split)
        return ds.map(lambda x: {
            "question": x["question"],
            "reasoning": x["answer"].split("####")[0].strip(),
            "answer": extract_gsm8k_answer(x["answer"]),
        })
    # dapo-math has no test split and no worked solutions; hold out the tail for eval.
    ds = load_dataset("open-r1/DAPO-Math-17k-Processed", "en", split="train")
    ds = ds.select(range(len(ds) - 500, len(ds))) if split == "test" else ds.select(range(len(ds) - 500))
    return ds.map(lambda x: {"question": x["prompt"], "reasoning": "", "answer": x["solution"].strip()})


def chat_template():
    # Unsloth's GRPO recipe: the prompt ends with the reasoning tag, so the model
    # always starts by thinking.
    return (
        "{% if messages[0]['role'] == 'system' %}"
        "{{ messages[0]['content'] + eos_token }}"
        "{% set loop_messages = messages[1:] %}"
        "{% else %}"
        f"{{{{ '{SYSTEM_PROMPT}' + eos_token }}}}"
        "{% set loop_messages = messages %}"
        "{% endif %}"
        "{% for message in loop_messages %}"
        "{% if message['role'] == 'user' %}{{ message['content'] }}"
        "{% elif message['role'] == 'assistant' %}{{ message['content'] + eos_token }}"
        "{% endif %}{% endfor %}"
        f"{{% if add_generation_prompt %}}{{{{ '{REASONING_START}' }}}}{{% endif %}}"
    )


def prompt_messages(question):
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]


# ---------------------------------------------------------------- rewards

def make_reward_functions(eos_token):
    match_format = re.compile(
        rf"{REASONING_END}.*?{SOLUTION_START}(.+?){SOLUTION_END}[\s]*"
        rf"(?:{re.escape(eos_token)})?[\s]*$",
        flags=re.MULTILINE | re.DOTALL,
    )
    match_number = re.compile(SOLUTION_START + r".*?([-]?[\d\.\,]+)", flags=re.MULTILINE | re.DOTALL)

    def format_exact(completions, **kwargs):
        """+3 when the answer follows the format exactly."""
        return [3.0 if match_format.search(c[0]["content"]) else 0.0 for c in completions]

    def format_partial(completions, **kwargs):
        """+0.5 per tag used exactly once, -1 otherwise."""
        scores = []
        for c in completions:
            text = c[0]["content"]
            scores.append(sum(0.5 if text.count(tag) == 1 else -1.0
                              for tag in (REASONING_END, SOLUTION_START, SOLUTION_END)))
        return scores

    def correct_answer(completions, answer, **kwargs):
        """+5 for the right number, -1.5 for a wrong one, -2.5 for no answer."""
        scores = []
        for c, truth in zip(completions, answer):
            found = match_number.search(c[0]["content"])
            if not found:
                scores.append(-2.5)
                continue
            try:
                guess = float(found.group(1).replace(",", "").rstrip("."))
                scores.append(5.0 if abs(guess - float(truth)) < 1e-6 else -1.5)
            except ValueError:
                scores.append(0.0)
        return scores

    return [format_exact, format_partial, correct_answer], match_number


# ---------------------------------------------------------------- eval

def load_model(settings, gpu_memory_utilization=None):
    """Base model + LoRA wrapper + chat template, from a settings dict (parsed args, or the
    "settings" saved in a run's results.json)."""
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
    tokenizer.chat_template = chat_template()
    return model, tokenizer


def load_trained(run_dir, gpu_memory_utilization=0.6):
    """Reload a finished run for inference: (model, tokenizer, lora_request) for
    model.fast_generate. Uses the run's own saved settings, so the base model matches the
    one the LoRA was trained on."""
    with open(os.path.join(run_dir, "results.json")) as f:
        settings = json.load(f)["settings"]
    model, tokenizer = load_model(settings, gpu_memory_utilization)
    return model, tokenizer, model.load_lora(os.path.join(run_dir, "lora"))


def evaluate(model, tokenizer, questions, match_number, max_tokens, lora_request=None,
             save_path=None):
    """Share of held-out questions answered with the right number (greedy decoding).
    Writes every question, answer and verdict to save_path (JSON lines) if given."""
    from vllm import SamplingParams

    prompts = [tokenizer.apply_chat_template(prompt_messages(q), tokenize=False,
                                             add_generation_prompt=True)
               for q in questions["question"]]
    params = SamplingParams(temperature=0.0, max_tokens=max_tokens)
    outputs = model.fast_generate(prompts, sampling_params=params, lora_request=lora_request,
                                  use_tqdm=False)
    correct = 0
    rows = []
    for q, out, truth in zip(questions["question"], outputs, questions["answer"]):
        text = out.outputs[0].text
        found = match_number.search(text)
        ok = False
        try:
            ok = found is not None and abs(
                float(found.group(1).replace(",", "").rstrip(".")) - float(truth)) < 1e-6
        except ValueError:
            pass
        correct += ok
        rows.append({"question": q, "answer": truth, "correct": ok,
                     "extracted": found.group(1) if found else None, "output": text})
    if save_path:
        with open(save_path, "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
    return round(correct / len(prompts), 4)


# ---------------------------------------------------------------- main

def main(argv=None):
    args = parse_args(argv)
    os.makedirs(args.output_dir, exist_ok=True)
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
    from datasets import Dataset
    from trl import GRPOConfig, GRPOTrainer, SFTConfig, SFTTrainer

    model, tokenizer = load_model(vars(args))
    timings["load_s"] = round(time.time() - t0, 1)

    train = load_questions(args.dataset, "train").shuffle(seed=args.seed)
    test = load_questions(args.dataset, "test").shuffle(seed=args.seed)
    reward_funcs, match_number = make_reward_functions(tokenizer.eos_token)

    # Prompt length: cover 90% of questions, drop the long tail.
    lengths = [len(tokenizer.apply_chat_template(prompt_messages(q), add_generation_prompt=True))
               for q in train["question"]]
    max_prompt_length = int(sorted(lengths)[int(len(lengths) * 0.9)]) + 1
    keep = [i for i, n in enumerate(lengths) if n <= max_prompt_length]
    train = train.select(keep)
    max_completion_length = args.max_seq_length - max_prompt_length
    print(f"[grpo] {len(train)} training questions, prompt <= {max_prompt_length} tokens, "
          f"answer <= {max_completion_length} tokens", flush=True)

    results = {
        "model": args.model,
        "dataset": args.dataset,
        "settings": vars(args),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    eval_set = test.select(range(min(args.eval_samples, len(test)))) if args.eval_samples else None
    if eval_set is not None:
        t = time.time()
        results["accuracy_before"] = evaluate(
            model, tokenizer, eval_set, match_number, max_completion_length,
            save_path=os.path.join(args.output_dir, "eval_before.jsonl"))
        timings["eval_before_s"] = round(time.time() - t, 1)
        print(f"[grpo] accuracy before: {results['accuracy_before']}", flush=True)

    # 1. Short supervised pass so a base model learns the answer format.
    has_reasoning = any(r for r in train.select(range(min(50, len(train))))["reasoning"])
    if args.warmup_sft_steps and has_reasoning:
        t = time.time()

        def to_text(x):
            messages = prompt_messages(x["question"]) + [{
                "role": "assistant",
                "content": f"{REASONING_START}{x['reasoning']}{REASONING_END}"
                           f"{SOLUTION_START}{x['answer']}{SOLUTION_END}",
            }]
            return {"text": tokenizer.apply_chat_template(messages, tokenize=False)}

        sft_data = train.select(range(min(len(train), args.warmup_sft_steps * 2))).map(to_text)
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

    # 2. GRPO.
    grpo_data = train.map(lambda x: {"prompt": prompt_messages(x["question"]), "answer": x["answer"]})
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
            stop=[tokenizer.eos_token], include_stop_str_in_output=True,
        )
    trainer = GRPOTrainer(
        model=model,
        processing_class=tokenizer,
        reward_funcs=reward_funcs,
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
        results["accuracy_after"] = evaluate(model, tokenizer, eval_set, match_number,
                                             max_completion_length,
                                             lora_request=model.load_lora(lora_dir),
                                             save_path=os.path.join(args.output_dir,
                                                                    "eval_after.jsonl"))
        timings["eval_after_s"] = round(time.time() - t, 1)
        print(f"[grpo] accuracy after: {results['accuracy_after']}", flush=True)

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
