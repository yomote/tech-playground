"""Optional official System One Adapter integration; no credentials or raw traces leave this module."""

import asyncio
from contextlib import contextmanager
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
from threading import Lock
from time import perf_counter
from urllib.parse import urlsplit

_ENV_PATH = Path(__file__).resolve().parent / ".env"
_ENV_KEYS = ("DEMO_LLM_PROVIDER", "DEMO_LLM_MODEL", "DEMO_LLM_API_KEY", "DEMO_LLM_BASE_URL")
_ENV_LOCK = Lock()
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_TIMEOUT_SECONDS = 60
_WARNING = (
    "System One Adapter uses the configured LLM, not Jev. Scores are probabilities "
    "reported by that LLM, not calibrated correctness probabilities or model logits. "
    "They are not directly comparable to local-model softmax scores."
)


def _configuration():
    """Read only this demo's settings; do not import credentials from other .env files."""
    file_values = {}
    if _ENV_PATH.is_file() and importlib.util.find_spec("dotenv") is not None:
        from dotenv import dotenv_values

        # Disable ${...} expansion so unrelated environment credentials cannot be imported.
        file_values = dotenv_values(_ENV_PATH, interpolate=False)
    return {name: (os.environ.get(name, file_values.get(name)) or "").strip() for name in _ENV_KEYS}


def _configuration_problem(config):
    if config["DEMO_LLM_PROVIDER"] not in {"openai", "anthropic", "codex"}:
        return "Set DEMO_LLM_PROVIDER to codex, openai, or anthropic in this demo's .env."
    if not _MODEL_PATTERN.fullmatch(config["DEMO_LLM_MODEL"]):
        return "Set DEMO_LLM_MODEL to an explicit provider model identifier."
    if config["DEMO_LLM_PROVIDER"] != "codex" and not config["DEMO_LLM_API_KEY"]:
        return "Set DEMO_LLM_API_KEY; local endpoints also require an explicitly configured placeholder."
    endpoint = config["DEMO_LLM_BASE_URL"]
    if endpoint:
        if config["DEMO_LLM_PROVIDER"] != "openai":
            return "DEMO_LLM_BASE_URL is supported only for OpenAI-compatible providers."
        try:
            url = urlsplit(endpoint)
            if (url.scheme not in {"http", "https"} or not url.hostname
                    or url.username is not None or url.password is not None or url.query or url.fragment):
                return "Set a valid HTTP(S) endpoint without credentials, query, or fragment in DEMO_LLM_BASE_URL."
        except ValueError:
            return "DEMO_LLM_BASE_URL is not a valid endpoint URL."
    return None


def _missing_dependencies(provider):
    modules = ["system_one_adapter", "dotenv"]
    if provider in {"openai", "anthropic"}:
        modules.append(provider)
    return [name for name in modules if importlib.util.find_spec(name) is None]


def adapter_status():
    """Report setup without constructing a provider or making any network request."""
    status = {"id": "llm-adapter", "name": "System One Adapter", "available": False, "detail": ""}
    try:
        config = _configuration()
        problem = _configuration_problem(config)
        missing = _missing_dependencies(config["DEMO_LLM_PROVIDER"])
        if missing:
            status["detail"] = "Optional dependencies missing. Install requirements-adapter.txt in the demo .venv."
        elif problem:
            status["detail"] = problem
        elif config["DEMO_LLM_PROVIDER"] == "codex" and shutil.which("codex") is None:
            status["detail"] = "Codex CLI was not found on PATH. Install it and sign in with codex login."
        else:
            status.update(
                name=f"System One Adapter / {config['DEMO_LLM_PROVIDER']} / {config['DEMO_LLM_MODEL']}",
                available=True,
                detail=("Uses the Codex CLI's saved login; run codex login if needed. This check does not inspect auth files or verify login."
                        if config["DEMO_LLM_PROVIDER"] == "codex" else
                        "Configured LLM API; running a comparison sends the input to that provider. No API call made by this status check."),
            )
    except Exception:
        status["detail"] = "Unable to read adapter configuration; check this demo's .env and restart."
    return status


def make_choice_input(task):
    """Create only the document, instructions, and exact id-to-description mapping."""
    if not isinstance(task, dict) or task.get("mode") not in {"classification", "criteria", "sufficiency"}:
        raise ValueError("Invalid decision mode.")
    if task.get("language") not in {"ja", "en"}:
        raise ValueError("Invalid decision language.")
    text, question, criteria = task.get("text"), task.get("question"), task.get("criteria", "")
    if isinstance(criteria, list) and all(isinstance(item, str) for item in criteria):
        criteria = "\n".join(criteria)
    if not all(isinstance(item, str) for item in (text, question, criteria)) or not question.strip():
        raise ValueError("Provide text, a question, and string criteria.")
    options = task.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        raise ValueError("Provide two to four choices.")
    choice_criteria = {}
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("Invalid choice.")
        option_id, label, description = option.get("id"), option.get("label"), option.get("description", "")
        if (not isinstance(option_id, str) or not option_id.strip() or option_id in choice_criteria
                or not isinstance(label, str) or not label.strip() or not isinstance(description, str)):
            raise ValueError("Each choice requires a unique id, a label, and a string description.")
        choice_criteria[option_id] = label + (": " + description if description else "")
    instructions = f"QUESTION: {question}\nCRITERIA: {criteria}"
    return {"state": text, "instructions": instructions, "criteria": choice_criteria}


def parse_choice_response(response, option_ids):
    """Validate the SDK's ChoiceAnswer without inventing missing scores or renormalizing."""
    if not isinstance(response, dict):
        raise ValueError("Adapter response is not an object.")
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != {"decision"}:
        raise ValueError("Adapter did not return exactly the requested decision.")
    answer = answers["decision"]
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("Adapter returned an unsupported answer type.")
    selected, probabilities = answer.get("choice"), answer.get("probabilities")
    if not isinstance(selected, str) or selected not in option_ids:
        raise ValueError("Adapter selected an unknown choice id.")
    if not isinstance(probabilities, dict) or set(probabilities) != set(option_ids):
        raise ValueError("Adapter probabilities do not cover exactly the requested choices.")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values()):
        raise ValueError("Adapter probabilities must be finite numbers between zero and one.")
    if not math.isclose(math.fsum(probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-6):
        raise ValueError("Adapter probabilities must sum to one; normalization is disabled.")
    if probabilities[selected] < max(probabilities.values()):
        raise ValueError("Adapter choice is inconsistent with its reported probabilities.")
    return {
        "selectedOptionId": selected,
        "rawAnswer": selected,
        "scores": [{"optionId": option_id, "score": float(probabilities[option_id]), "rawScore": None}
                   for option_id in option_ids],
    }


@contextmanager
def _anthropic_credentials(api_key):
    """Official 0.2.0 Anthropic factory reads environment settings at construction."""
    changes = {"ANTHROPIC_API_KEY": api_key, "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
               "ANTHROPIC_AUTH_TOKEN": None}
    with _ENV_LOCK:
        previous = {key: os.environ.get(key) for key in changes}
        try:
            for key, value in changes.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


async def _request(config, payload):
    from system_one_adapter import AsyncSystemOneAdapterClient, Choice, RetryPolicy

    questions = payload.get("questions")
    if questions is None:
        questions = {"decision": {"instructions": payload["instructions"], "criteria": payload["criteria"]}}
    if config["DEMO_LLM_PROVIDER"] == "codex":
        from codex_provider import make_codex_provider

        provider = make_codex_provider(config["DEMO_LLM_MODEL"])
    elif config["DEMO_LLM_PROVIDER"] == "openai":
        from system_one_adapter.providers.openai import AsyncOpenAIProvider

        provider = AsyncOpenAIProvider(
            config["DEMO_LLM_MODEL"], api_key=config["DEMO_LLM_API_KEY"],
            base_url=config["DEMO_LLM_BASE_URL"] or "https://api.openai.com/v1",
        )
    else:
        from system_one_adapter.providers.anthropic import AsyncAnthropicProvider

        with _anthropic_credentials(config["DEMO_LLM_API_KEY"]):
            provider = AsyncAnthropicProvider(config["DEMO_LLM_MODEL"], max_tokens=max(1024, 256 * len(questions)))
    try:
        async with AsyncSystemOneAdapterClient(
            structured_outputs=True, llm_answer_mode="probabilities", normalize_probabilities=False,
            n_retry_malformed_structure=0, retry=RetryPolicy(max_retries=0, timeout=_TIMEOUT_SECONDS),
        ) as client:
            response = await client.system_one(
                state=payload["state"],
                questions={name: Choice(instructions=question["instructions"], criteria=question["criteria"])
                           for name, question in questions.items()},
                model=provider,
            )
            return response.model_dump()
    finally:
        # Caller-provided provider instances are caller-owned in the official adapter.
        await provider.aclose()


def _safe_usage(response):
    usage = response.get("usage", {})
    if not isinstance(usage, dict):
        return {}
    keys = ("input_tokens", "output_tokens", "input_tokens_total", "output_tokens_total",
            "n_retries", "n_retries_malformed_structure", "latency")
    return {key: value for key in keys if isinstance((value := usage.get(key)), (int, float))
            and not isinstance(value, bool) and math.isfinite(value) and value >= 0}


def _reported_model(response, configured_model, secret):
    """Keep only provider-reported model identity, never raw requests, headers, or debug traces."""
    candidate = response.get("model", configured_model)
    debug = response.get("debug", {})
    attempts = debug.get("llm_attempts", []) if isinstance(debug, dict) else []
    for attempt in reversed(attempts if isinstance(attempts, list) else []):
        raw = attempt.get("llm_response") if isinstance(attempt, dict) else None
        if isinstance(raw, dict) and isinstance(raw.get("model"), str):
            candidate = raw["model"]
            break
    if not isinstance(candidate, str) or not _MODEL_PATTERN.fullmatch(candidate) or (secret and secret in candidate):
        return configured_model
    return candidate


def infer_adapter(task):
    """Make at most one LLM request; return only validated results and sanitized errors."""
    result = {
        "model": "llm-adapter", "modelName": "System One Adapter", "status": "error",
        "selectedOptionId": None, "scores": [], "scoreKind": "llm-reported-probabilities",
        "prompt": "", "loadMs": 0.0, "inferenceMs": 0.0, "warning": _WARNING,
    }
    started = None
    try:
        config = _configuration()
        problem = _configuration_problem(config)
        if problem:
            result["error"] = problem
            return result
        if _missing_dependencies(config["DEMO_LLM_PROVIDER"]):
            result["error"] = "Install requirements-adapter.txt in this demo's .venv."
            return result
        if config["DEMO_LLM_PROVIDER"] == "codex" and shutil.which("codex") is None:
            result["error"] = "Codex CLI was not found on PATH; install it and sign in with codex login."
            return result
        result["modelName"] = f"System One Adapter / {config['DEMO_LLM_PROVIDER']} / {config['DEMO_LLM_MODEL']}"
        payload = make_choice_input(task)
        # Show the supplied document and Choice schema, not the provider's raw request/response traces.
        result["prompt"] = json.dumps(payload, ensure_ascii=False, indent=2)
        started = perf_counter()

        async def bounded_request():
            return await asyncio.wait_for(_request(config, payload), timeout=_TIMEOUT_SECONDS)

        response = asyncio.run(bounded_request())
        result.update(parse_choice_response(response, list(payload["criteria"])))
        provider_model = _reported_model(response, config["DEMO_LLM_MODEL"], config["DEMO_LLM_API_KEY"])
        result.update(
            status="ok", modelName=f"System One Adapter / {config['DEMO_LLM_PROVIDER']} / {provider_model}",
            provider=config["DEMO_LLM_PROVIDER"], providerModel=provider_model,
            requestedModel=config["DEMO_LLM_MODEL"], usage=_safe_usage(response),
        )
    except TimeoutError:
        result["error"] = "LLM request exceeded the 60-second limit; no automatic retry was made."
    except Exception:
        # Provider exception messages/debug may contain endpoint URLs, request data, or credentials.
        result["error"] = "Adapter request or response validation failed. Check provider/model access and structured-output support. No fallback or retry was made."
    finally:
        if started is not None:
            result["inferenceMs"] = round((perf_counter() - started) * 1000, 2)
    return result
