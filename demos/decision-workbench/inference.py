"""Local, lazy CPU inference for two public general-purpose classifiers.

ModernBERT format follows its official model card and AnswerDotAI cookbook.
GLiClass uses the official pipeline's classification logits (get_embeddings),
then applies its single-label softmax. This is not embedding-neighbor routing.
"""

import gc
import importlib.util
import os
from pathlib import Path
from threading import RLock
from time import perf_counter

CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "huggingface"
MODELS = {
    "modernbert": {
        "name": "ModernBERT-Large-Instruct",
        "repo": "answerdotai/ModernBERT-Large-Instruct",
        "revision": "9943452941e79c8c35ede72e78a38a8175a79bb5",
        "dependencies": ("torch", "transformers"),
    },
    "gliclass": {
        "name": "GLiClass-Instruct Base",
        "repo": "knowledgator/gliclass-instruct-base-v1.0",
        "revision": "4f6a108b08a5537f395521d19b5073e197923dd3",
        "dependencies": ("torch", "transformers", "gliclass"),
    },
}
_loaded = {}
_load_errors = {}
_lock = RLock()
_WARNING = (
    "Scores are softmax values relative to these choices, not calibrated confidence "
    "or probability of being correct. Raw scores are logits and are not comparable "
    "across models. Neither model is guaranteed to know a fact or recognize missing information. "
    "Compare with English; Japanese performance is exploratory."
)


def _snapshot(model_id):
    spec = MODELS[model_id]
    return CACHE_DIR / ("models--" + spec["repo"].replace("/", "--")) / "snapshots" / spec["revision"]


def model_status():
    """No heavyweight imports or network: report dependencies and local snapshots."""
    statuses = []
    for model_id, spec in MODELS.items():
        missing = [name for name in spec["dependencies"] if importlib.util.find_spec(name) is None]
        files_missing = [name for name in ("config.json", "tokenizer.json", "model.safetensors")
                         if not (_snapshot(model_id) / name).is_file()]
        available = not missing and not files_missing and model_id not in _load_errors
        if missing:
            detail = "Missing Python dependencies: " + ", ".join(missing) + ". Install requirements.txt in the demo .venv."
        elif files_missing:
            detail = f"Model not downloaded. Run python setup_models.py --model {model_id}."
        elif model_id in _load_errors:
            detail = "Previous model load failed; fix setup and restart this demo: " + _load_errors[model_id]
        elif model_id in _loaded:
            detail = "Loaded locally on CPU. No remote inference."
        else:
            detail = "Local snapshot ready; first inference loads model into CPU memory."
        statuses.append({"id": model_id, "name": spec["name"], "available": available, "detail": detail})
    from adapter import adapter_status
    return statuses + [adapter_status()]


def _load(model_id):
    if model_id in _loaded:
        return _loaded[model_id], 0.0
    started = perf_counter()
    # The demo alternates models on small laptops; never keep both weight sets resident.
    _loaded.clear()
    gc.collect()
    # Enforce offline operation even for nested library calls. Setup is a separate process.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HOME"] = str(CACHE_DIR)
    import torch
    from transformers import AutoModelForMaskedLM, AutoTokenizer

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    path = str(_snapshot(model_id))
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    if model_id == "modernbert":
        model = AutoModelForMaskedLM.from_pretrained(
            path, local_files_only=True, trust_remote_code=False,
            dtype=torch.float32, attn_implementation="eager",
        ).eval()
        loaded = (model, tokenizer)
    else:
        from gliclass import GLiClassModel, ZeroShotClassificationPipeline

        model = GLiClassModel.from_pretrained(path, local_files_only=True).eval()
        pipeline = ZeroShotClassificationPipeline(
            model, tokenizer, classification_type="single-label", device="cpu",
            max_length=512, progress_bar=False,
        )
        loaded = (pipeline, tokenizer)
    _loaded[model_id] = loaded
    _load_errors.pop(model_id, None)
    return loaded, (perf_counter() - started) * 1000


def _task_parts(task):
    if not isinstance(task, dict) or task.get("mode") not in {"classification", "criteria", "sufficiency"}:
        raise ValueError("Choose classification, criteria, or sufficiency mode.")
    if task.get("language") not in {"ja", "en"}:
        raise ValueError("language must be ja or en.")
    options = task.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        raise ValueError("Provide between two and four options.")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
           or not item["id"].strip() or not isinstance(item.get("label"), str)
           or not item["label"].strip() for item in options):
        raise ValueError("Every option needs a nonempty id and label.")
    if len({item["id"] for item in options}) != len(options):
        raise ValueError("Option ids must be unique.")
    text = task.get("text", "")
    question = task.get("question", "")
    criteria = task.get("criteria", "")
    if isinstance(criteria, list) and all(isinstance(item, str) for item in criteria):
        criteria = "\n".join(criteria)
    if not all(isinstance(item, str) for item in (text, question, criteria)) or not question.strip():
        raise ValueError("Text and criteria must be strings; question must be nonempty.")
    labels = []
    for item in options:
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ValueError("Option descriptions must be strings.")
        labels.append(item["label"].strip() + (": " + description.strip() if description.strip() else ""))
    if len(set(labels)) != len(labels):
        raise ValueError("Option labels and descriptions must distinguish each choice.")
    instruction = (
        "次の問いと判断基準に従って、最も適切な選択肢を1つ選んでください。"
        if task["language"] == "ja" else
        "Follow the question and decision criteria. Select the single best option."
    )
    prompt = f"{instruction}\nQUESTION: {question.strip()}"
    if criteria.strip():
        prompt += f"\nCRITERIA: {criteria.strip()}"
    return options, labels, text, prompt


def infer(model_id, task):
    """Return JSON-safe results; missing setup and model errors never crash the portal."""
    if model_id == 'llm-adapter':
        from adapter import infer_adapter
        return infer_adapter(task)
    result = {
        "model": model_id, "modelName": MODELS.get(model_id, {}).get("name", str(model_id)),
        "status": "error", "selectedOptionId": None, "scores": [],
        "scoreKind": "option-softmax", "prompt": "", "loadMs": 0.0, "inferenceMs": 0.0,
        "warning": _WARNING,
    }
    try:
        if model_id not in MODELS:
            raise ValueError("Unknown model id.")
        options, labels, text, prompt = _task_parts(task)
        result["prompt"] = prompt + f"\nTEXT: {text}"
        with _lock:
            missing = [name for name in MODELS[model_id]["dependencies"] if importlib.util.find_spec(name) is None]
            if missing or not (_snapshot(model_id) / "model.safetensors").is_file():
                raise RuntimeError(next(item["detail"] for item in model_status() if item["id"] == model_id))
            try:
                loaded, result["loadMs"] = _load(model_id)
            except Exception as error:
                _load_errors[model_id] = str(error)
                raise
            import torch

            started = perf_counter()
            with torch.inference_mode():
                if model_id == "modernbert":
                    model, tokenizer = loaded
                    letters = "ABCD"[:len(options)]
                    choices = "\n".join(f"- {letter}: {label}" for letter, label in zip(letters, labels))
                    result["prompt"] = f"{prompt}\nTEXT: {text}\nCHOICES:\n{choices}\nANSWER: [unused0] [MASK]"
                    inputs = tokenizer(result["prompt"], return_tensors="pt", truncation=False)
                    if inputs.input_ids.shape[1] > 1024:
                        raise ValueError("ModernBERT input exceeds this demo's 1024-token CPU limit; shorten it.")
                    mask_positions = (inputs.input_ids[0] == tokenizer.mask_token_id).nonzero()
                    # Use the final answer mask, even when the user's input itself contains [MASK].
                    logits = model(**inputs).logits[0, mask_positions[-1, 0]].float()
                    token_ids = [tokenizer.encode(letter, add_special_tokens=False) for letter in letters]
                    if any(len(ids) != 1 for ids in token_ids):
                        raise RuntimeError("Choice letters are not single tokens in this tokenizer snapshot.")
                    choice_logits = logits[[ids[0] for ids in token_ids]]
                    full_argmax = int(logits.argmax())
                    raw_answer = tokenizer.decode([full_argmax]).strip()
                    result["rawAnswer"] = raw_answer
                    selected = letters.find(raw_answer) if len(raw_answer) == 1 else -1
                    result["status"] = "ok" if selected >= 0 else "invalid"
                    if selected < 0:
                        result["warning"] += " Full-vocabulary prediction was outside the offered letters; no option was selected."
                else:
                    pipeline, tokenizer = loaded
                    gliclass_prompt = prompt + "\nTEXT: "
                    # Official uni-encoder formatting includes labels, task prompt, then input text.
                    result["prompt"] = pipeline.pipe.prepare_input(text, labels, prompt=gliclass_prompt)
                    if len(tokenizer(result["prompt"], truncation=False)["input_ids"]) > 512:
                        raise ValueError("GLiClass input exceeds this demo's 512-token limit; shorten it.")
                    # get_embeddings exposes the same classification logits used by the public pipeline.
                    output = pipeline.get_embeddings(text, labels, prompt=gliclass_prompt)[0]
                    choice_logits = torch.as_tensor(output["logits"][:len(options)]).float()
                    selected = int(choice_logits.argmax())
                    result["rawAnswer"] = labels[selected]
                    result["status"] = "ok"
                scores = torch.softmax(choice_logits, dim=-1)
                if not torch.isfinite(scores).all() or not torch.isfinite(choice_logits).all():
                    raise RuntimeError("Model returned non-finite scores.")
                result["scores"] = [
                    {"optionId": option["id"], "score": float(scores[index]), "rawScore": float(choice_logits[index])}
                    for index, option in enumerate(options)
                ]
                result["selectedOptionId"] = options[selected]["id"] if selected >= 0 else None
            result["inferenceMs"] = (perf_counter() - started) * 1000
    except Exception as error:
        result["status"] = "error"
        result["selectedOptionId"] = None
        result["error"] = str(error)
        result["warning"] = f"{type(error).__name__}: {error} {_WARNING}"
    result["loadMs"] = round(result["loadMs"], 2)
    result["inferenceMs"] = round(result["inferenceMs"], 2)
    return result
