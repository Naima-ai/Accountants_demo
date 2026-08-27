"""
slm_client.py

In-process local SLM inference via llama-cpp-python, replacing the old
Ollama daemon client. Loads one quantized GGUF model directly into the
FastAPI process and serves every classify/extract/categorize/narrate
call from that single instance -- no separate daemon, no HTTP round
trip to localhost, and none of the "is the service up" / model-swap
cold-load latency that came with Ollama.

Model: Qwen2.5-1.5B-Instruct (GGUF, Q4_K_M) by default -- small enough
to run comfortably on CPU-only hardware, offloadable to an on-prem
NVIDIA GPU via SLM_N_GPU_LAYERS with no code change, and strong at
Italian/multilingual instruction-following for its size (the demo
dataset is Italian invoices/receipts). Swap to a larger quant (e.g.
Qwen2.5-3B-Instruct-GGUF) via SLM_MODEL_REPO/SLM_MODEL_FILE alone.

JSON-producing callers pass `schema` (a JSON Schema dict, see SCHEMAS
below) so llama.cpp's grammar-constrained decoding guarantees
syntactically valid JSON output -- parse_json_object()'s repair path
becomes a last-resort safety net instead of the routine case.

Usage:
    from src.llm.slm_client import call_llm, parse_json_object, warm_up, SCHEMAS

    warm_up()  # once, before real work -- forces the model to load
    raw = call_llm(prompt, num_predict=150, schema=SCHEMAS["classification"])
    parsed = parse_json_object(raw)
"""

import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.config import (
    SLM_MODEL_REPO, SLM_MODEL_FILE, SLM_MODELS_DIR,
    SLM_N_CTX, SLM_N_THREADS, SLM_N_GPU_LAYERS,
)

logger = logging.getLogger("slm_client")
logging.basicConfig(level=logging.INFO)

_load_lock = threading.Lock()
_llm = None  # lazy singleton llama_cpp.Llama, loaded once per process


def _get_llm():
    """Lazily loads (and caches) the local model. Thread-safe -- only
    the first caller pays the load cost, everyone else reuses it."""
    global _llm
    if _llm is not None:
        return _llm
    with _load_lock:
        if _llm is None:
            from llama_cpp import Llama
            from huggingface_hub import hf_hub_download

            model_path = hf_hub_download(
                repo_id=SLM_MODEL_REPO, filename=SLM_MODEL_FILE, local_dir=SLM_MODELS_DIR,
            )
            logger.info(
                f"Loading local SLM '{SLM_MODEL_FILE}' from {model_path} "
                f"(n_ctx={SLM_N_CTX}, n_threads={SLM_N_THREADS}, n_gpu_layers={SLM_N_GPU_LAYERS})..."
            )
            start = time.time()
            _llm = Llama(
                model_path=model_path,
                n_ctx=SLM_N_CTX,
                n_threads=SLM_N_THREADS,
                n_gpu_layers=SLM_N_GPU_LAYERS,
                verbose=False,
            )
            logger.info(f"SLM loaded in {time.time() - start:.1f}s.")
    return _llm


def _compile_grammar(schema: Optional[Dict[str, Any]]):
    """Best-effort JSON-schema -> grammar compile. Falls back to
    unconstrained decoding (never raises) if the schema can't be
    compiled -- parse_json_object()'s repair path still catches
    whatever comes back, so a grammar failure degrades gracefully
    instead of breaking the call."""
    if schema is None:
        return None
    try:
        from llama_cpp import LlamaGrammar
        return LlamaGrammar.from_json_schema(json.dumps(schema))
    except Exception as e:
        logger.warning(f"Grammar compilation failed ({e}) -- falling back to unconstrained decoding.")
        return None


def call_llm(
    prompt: str,
    num_predict: int = 300,
    temperature: float = 0.1,
    schema: Optional[Dict[str, Any]] = None,
) -> str:
    """Send a prompt to the local SLM, return the raw text response.

    Pass `schema` (a JSON Schema dict, see SCHEMAS below) for any
    JSON-producing call -- grammar-constrained decoding then guarantees
    the output parses as valid JSON matching that shape.
    """
    llm = _get_llm()
    grammar = _compile_grammar(schema)
    result = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=num_predict,
        temperature=temperature,
        grammar=grammar,
    )
    return result["choices"][0]["message"]["content"] or ""


def parse_json_object(raw: str) -> Dict[str, Any]:
    """Pull the first {...} block out of a model response and parse it.
    Kept as a safety net for calls that skip grammar constraints (or in
    case grammar compilation fell back to unconstrained decoding) --
    models can still wrap JSON in markdown fences or add stray text.

    If direct parsing fails (a missing comma, truncated output hitting
    the token limit mid-generation, etc.), fall back to json_repair
    before giving up entirely."""
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


def warm_up() -> bool:
    """
    Force the local model to load into memory with a trivial request,
    BEFORE any real classify/extract/validate call needs it. Call this
    once at API startup, not per-document.

    Returns True if the model responded (now warm), False if the load
    or call failed outright -- callers should treat False as "don't
    bother trying model calls".
    """
    logger.info(f"Warming up local SLM ({SLM_MODEL_REPO}/{SLM_MODEL_FILE})...")
    start = time.time()
    try:
        call_llm("Reply with one word: ready", num_predict=5)
        logger.info(f"Model warm after {time.time() - start:.1f}s.")
        return True
    except Exception as e:
        logger.warning(f"Warm-up failed after {time.time() - start:.1f}s: {e}")
        return False


# ----------------------------------------------------------------------
# JSON Schemas for grammar-constrained decoding -- one per JSON-shaped
# call site in classifier.py / extractor.py / validator.py. Kept here
# (rather than duplicated in each caller) so the schema and the prompt
# describing that same shape stay easy to compare side by side.
# ----------------------------------------------------------------------

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "classification": {
        "type": "object",
        "properties": {
            "document_type": {"type": "string"},
            "confidence": {"type": "number"},
            "reasoning": {"type": "string"},
        },
        "required": ["document_type", "confidence"],
    },
    "extraction": {
        "type": "object",
        "properties": {
            "supplier_name": {"type": ["string", "null"]},
            "supplier_vat": {"type": ["string", "null"]},
            "customer_name": {"type": ["string", "null"]},
            "document_number": {"type": ["string", "null"]},
            "document_date": {"type": ["string", "null"]},
            "due_date": {"type": ["string", "null"]},
            "currency": {"type": ["string", "null"]},
            "subtotal": {"type": ["string", "null"]},
            "vat_amount": {"type": ["string", "null"]},
            "total_amount": {"type": ["string", "null"]},
            "iban": {"type": ["string", "null"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": ["string", "null"]},
                        "quantity": {"type": ["string", "null"]},
                        "unit_price": {"type": ["string", "null"]},
                        "total": {"type": ["string", "null"]},
                        "vat_rate": {"type": ["string", "null"]},
                    },
                },
            },
            "confidence": {"type": "number"},
        },
        "required": ["confidence"],
    },
    "categorization": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["code", "confidence"],
    },
}


# ----------------------------------------------------------------------
# Quick manual test: loads the model and runs one classification-shaped
# call. Downloads the GGUF on first run (one-time, ~1GB).
# Run: python src/llm/slm_client.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    ok = warm_up()
    print(f"warm_up() -> {ok}")
    if ok:
        raw = call_llm(
            'Classify this as a JSON object: {"document_type": "invoice", "confidence": 0.9, "reasoning": "test"}. '
            "Respond with only that exact JSON object.",
            num_predict=100,
            schema=SCHEMAS["classification"],
        )
        print("raw:", raw)
        print("parsed:", parse_json_object(raw))
