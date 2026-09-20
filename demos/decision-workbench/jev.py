"""Direct Jev inference through the official TypeSafe SDK; no LLM adapter."""

import asyncio
import importlib.util
import json
import math
import os
from pathlib import Path
from time import perf_counter

from adapter import make_choice_input, parse_choice_response, _MODEL_PATTERN, _safe_usage

_ENV_PATH = Path(__file__).resolve().parent / ".env"
_TIMEOUT_SECONDS = 30


def _configuration():
    values = {}
    if _ENV_PATH.is_file() and importlib.util.find_spec("dotenv") is not None:
        from dotenv import dotenv_values

        values = dotenv_values(_ENV_PATH, interpolate=False)
    return {
        "api_key": (os.environ.get("TYPESAFE_API_KEY", values.get("TYPESAFE_API_KEY")) or "").strip(),
        "model": (os.environ.get("TYPESAFE_MODEL", values.get("TYPESAFE_MODEL")) or "jev-latest").strip(),
    }


def _problem(config):
    if not config["api_key"]:
        return "Set TYPESAFE_API_KEY in demos/decision-workbench/.env. Keep your Codex settings."
    if not _MODEL_PATTERN.fullmatch(config["model"]):
        return "Set TYPESAFE_MODEL to a valid Jev model identifier, for example jev-latest."
    if any(importlib.util.find_spec(module) is None for module in ("typesafe_sdk", "dotenv")):
        return "Install requirements-jev.txt in this demo's .venv."
    return None


def jev_status():
    status = {"id": "jev", "name": "Jev / TypeSafe API", "available": False, "detail": ""}
    try:
        config = _configuration()
        problem = _problem(config)
        status.update(available=problem is None, detail=problem or
                      "Direct Jev API configured. Account access is verified only when you run; requests use your TypeSafe API quota.",
                      name=f"Jev / {config['model']}" if _MODEL_PATTERN.fullmatch(config['model']) else status['name'])
    except Exception:
        status["detail"] = "Unable to read Jev setup. Check the demo .env."
    return status


async def _request(config, payload):
    from typesafe_sdk import AsyncTypeSafeClient, Choice, RetryPolicy

    questions = payload.get("questions")
    if questions is None:
        questions = {"decision": {"instructions": payload["instructions"], "criteria": payload["criteria"]}}
    # Explicit endpoint keeps unrelated environment settings from redirecting this key.
    async with AsyncTypeSafeClient(api_key=config["api_key"], model=config["model"],
                                   base_url="https://api.typesafe.ai", timeout=_TIMEOUT_SECONDS,
                                   retry=RetryPolicy(max_retries=0)) as client:
        response = await client.system_one(
            state=payload["state"],
            questions={name: Choice(instructions=question["instructions"], criteria=question["criteria"])
                       for name, question in questions.items()},
        )
        return response.model_dump()


def infer_jev(task):
    result = {"model": "jev", "modelName": "Jev / TypeSafe API", "status": "error",
              "selectedOptionId": None, "scores": [], "scoreKind": "jev-probabilities",
              "prompt": "", "loadMs": 0.0, "inferenceMs": 0.0,
              "warning": "Direct Jev API. Time includes client and network overhead; it is not pure model compute time. Probabilities and confidence are separate provider outputs, not a guarantee of correctness."}
    started = None
    try:
        config = _configuration()
        problem = _problem(config)
        if problem:
            result["error"] = problem
            return result
        payload = make_choice_input(task)
        result.update(modelName=f"Jev / {config['model']}", provider="typesafe", requestedModel=config["model"],
                      prompt=json.dumps(payload, ensure_ascii=False, indent=2))
        # SDK/client setup is deliberately included: this is observed request latency.
        started = perf_counter()

        async def bounded_request():
            return await asyncio.wait_for(_request(config, payload), timeout=_TIMEOUT_SECONDS)

        response = asyncio.run(bounded_request())
        parsed = parse_choice_response(response, list(payload["criteria"]))
        confidence = response["answers"]["decision"].get("confidence")
        if type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Invalid Jev confidence.")
        model = response.get("model")
        if not isinstance(model, str) or not _MODEL_PATTERN.fullmatch(model) or config["api_key"] in model:
            raise ValueError("Invalid returned model identity.")
        result.update(parsed, status="ok", modelName=f"Jev / {model}", providerModel=model,
                      confidence=confidence, usage=_safe_usage(response))
    except TimeoutError:
        result["error"] = "Jev request exceeded 30 seconds. No retry or fallback was made."
    except Exception:
        # Never propagate raw SDK responses/errors or credentials to the browser/run files.
        result["error"] = "Jev request or response validation failed. Check your TypeSafe key, account access and model. No retry or fallback was made."
    finally:
        if started is not None:
            result["inferenceMs"] = round((perf_counter() - started) * 1000, 2)
    return result
