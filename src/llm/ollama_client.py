"""
ollama_client.py

Shared helper for talking to a local Ollama instance. Used by
classifier.py, extractor.py, and validator.py so the call/timeout/
JSON-parsing logic lives in one place instead of three copies.
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("ollama_client")
logging.basicConfig(level=logging.INFO)

# Configurable via environment variables, defaulting to 7B (or whatever model is active)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

# 7B on CPU-only hardware genuinely needs this much room -- 240s caused
# real timeouts in testing on documents with several line items. Don't
# lower this back down without re-testing on CPU-only hardware first.
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "420"))
WARMUP_TIMEOUT_S = int(os.getenv("WARMUP_TIMEOUT_S", "420"))


def call_ollama(
    prompt: str,
    model: str = OLLAMA_MODEL,
    host: str = OLLAMA_HOST,
    num_predict: int = 300,
    temperature: float = 0.1,
    timeout: int = OLLAMA_TIMEOUT_S,
) -> str:
    """Send a prompt to Ollama, return the raw text response."""
    resp = requests.post(
        f"{host}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def parse_json_object(raw: str) -> Dict[str, Any]:
    """Pull the first {...} block out of a model response and parse it.
    Models sometimes wrap JSON in markdown fences or add stray text --
    don't assume raw is clean JSON on its own.

    If direct parsing fails (a missing comma, truncated output hitting the
    token limit mid-generation, etc.), fall back to json_repair before
    giving up entirely -- seen in practice with real SROIE accuracy runs
    (e.g. "Expecting ',' delimiter"), where the alternative was throwing
    away a document's entire extraction over one malformed character."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output: {raw!r}")
    candidate = match.group(0)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        logger.warning(f"Direct JSON parse failed ({e}), attempting repair...")
        try:
            from json_repair import repair_json
            repaired = repair_json(candidate, return_objects=True)
        except Exception as repair_err:
            raise ValueError(f"Could not parse or repair JSON: {candidate!r}") from repair_err
        if not isinstance(repaired, dict):
            raise ValueError(f"Repair produced non-dict result: {repaired!r}")
        logger.info("JSON repair succeeded.")
        return repaired


def warm_up(model: str = OLLAMA_MODEL, host: str = OLLAMA_HOST, timeout: int = WARMUP_TIMEOUT_S) -> bool:
    """
    Force Ollama to load the model into memory with a trivial request,
    BEFORE any real classify/extract/validate call needs it. Call this
    once at the start of a script or service, not per-document.

    Returns True if the model responded (now warm), False if the call
    failed outright (Ollama not running, wrong model name, etc.) --
    callers should treat False as "don't bother trying model calls".
    """
    logger.info(f"Warming up '{model}' on {host} (this can take a while)...")
    start = time.time()
    try:
        call_ollama("Reply with one word: ready", model=model, host=host, num_predict=5, timeout=timeout)
        logger.info(f"Model warm after {time.time() - start:.1f}s.")
        return True
    except Exception as e:
        logger.warning(f"Warm-up failed after {time.time() - start:.1f}s: {e}")
        return False