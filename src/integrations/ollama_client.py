"""
ollama_client.py

Shared helper for talking to a local Ollama instance.

Used by:
    - classifier.py
    - extractor.py
    - validator.py
    - reminder_agent.py

The Ollama host and model can be configured through environment
variables so the same code works locally and in Docker/server
deployments.
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict

import requests


logger = logging.getLogger("ollama_client")
logging.basicConfig(level=logging.INFO)


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    "http://localhost:11434",
)

# Use a model that is available in the current local Ollama setup.
# Can be overridden with OLLAMA_MODEL.
OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "llama3.2:latest",
)

OLLAMA_TIMEOUT_S = int(
    os.getenv(
        "OLLAMA_TIMEOUT_S",
        "420",
    )
)

WARMUP_TIMEOUT_S = int(
    os.getenv(
        "WARMUP_TIMEOUT_S",
        "420",
    )
)


# ----------------------------------------------------------------------
# Ollama API
# ----------------------------------------------------------------------


def call_ollama(
    prompt: str,
    model: str = OLLAMA_MODEL,
    host: str = OLLAMA_HOST,
    num_predict: int = 300,
    temperature: float = 0.1,
    timeout: int = OLLAMA_TIMEOUT_S,
) -> str:
    """
    Send a prompt to Ollama and return the raw text response.

    Args:
        prompt: Prompt sent to the model.
        model: Ollama model name.
        host: Ollama server URL.
        num_predict: Maximum number of generated tokens.
        temperature: Sampling temperature.
        timeout: HTTP request timeout in seconds.

    Returns:
        Raw model response text.

    Raises:
        requests.RequestException:
            If the Ollama request fails.
        ValueError:
            If Ollama returns an unexpected response.
    """

    url = f"{host.rstrip('/')}/api/generate"

    logger.debug(
        "Calling Ollama model '%s' at %s",
        model,
        url,
    )

    response = requests.post(
        url,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        },
        timeout=timeout,
    )

    response.raise_for_status()

    data = response.json()

    result = data.get("response")

    if result is None:
        raise ValueError(
            f"Ollama response did not contain 'response': {data!r}"
        )

    return result


# ----------------------------------------------------------------------
# JSON parsing
# ----------------------------------------------------------------------


def parse_json_object(
    raw: str,
) -> Dict[str, Any]:
    """
    Extract the first JSON object from a model response.

    Models may return JSON wrapped in markdown or surrounded by
    additional text. This helper extracts the JSON object and
    attempts to repair malformed JSON when necessary.
    """

    match = re.search(
        r"\{.*\}",
        raw,
        re.DOTALL,
    )

    if not match:
        raise ValueError(
            f"No JSON object found in model output: {raw!r}"
        )

    candidate = match.group(0)

    try:
        return json.loads(candidate)

    except json.JSONDecodeError as exc:

        logger.warning(
            "Direct JSON parse failed (%s), "
            "attempting repair...",
            exc,
        )

        try:
            from json_repair import repair_json

            repaired = repair_json(
                candidate,
                return_objects=True,
            )

        except Exception as repair_error:

            raise ValueError(
                f"Could not parse or repair JSON: "
                f"{candidate!r}"
            ) from repair_error

        if not isinstance(repaired, dict):

            raise ValueError(
                "JSON repair produced a non-dict result: "
                f"{repaired!r}"
            )

        logger.info(
            "JSON repair succeeded."
        )

        return repaired


# ----------------------------------------------------------------------
# Model warm-up
# ----------------------------------------------------------------------


def warm_up(
    model: str = OLLAMA_MODEL,
    host: str = OLLAMA_HOST,
    timeout: int = WARMUP_TIMEOUT_S,
) -> bool:
    """
    Load the configured model into Ollama memory.

    Call this once when starting the application rather than once
    per document.

    Returns:
        True if the model responds successfully.
        False if Ollama/model communication fails.
    """

    logger.info(
        "Warming up '%s' on %s "
        "(this can take a while)...",
        model,
        host,
    )

    start = time.time()

    try:

        call_ollama(
            "Reply with one word: ready",
            model=model,
            host=host,
            num_predict=5,
            timeout=timeout,
        )

        logger.info(
            "Model warm after %.1fs.",
            time.time() - start,
        )

        return True

    except Exception as exc:

        logger.warning(
            "Warm-up failed after %.1fs: %s",
            time.time() - start,
            exc,
        )

        return False