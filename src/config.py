"""
config.py

Single place for settings shared across the src/ stack (database, memory,
orchestration, API, local SLM).
"""

import os
from pathlib import Path

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
DATA_SET_DIR = BASE_DIR / "data_set"
DEMO_1_DIR = BASE_DIR / "demo_1"
DEMO_2_DIR = BASE_DIR / "demo_2"
DEMO_3_DIR = BASE_DIR / "demo_3"

# Local, on-prem storage for the app database + generated artifacts
# (reports, exports). Never a cloud path -- keeps with the "0 bytes
# leaving the firm" positioning from the demo brief.
VAR_DIR = BASE_DIR / "var"
REPORTS_DIR = VAR_DIR / "reports"

# ----------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------

# Defaults to a local SQLite file so the whole stack runs with zero
# external services. Override with DATABASE_URL for Postgres/MySQL in
# an actual pilot deployment.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(VAR_DIR / 'accountants_demo.db').as_posix()}")

# ----------------------------------------------------------------------
# Local SLM -- in-process llama.cpp (src/llm/slm_client.py), no daemon
# ----------------------------------------------------------------------

# Small, quantized, Italian-capable instruct model by default -- swap to
# a bigger quant (e.g. Qwen2.5-3B-Instruct-GGUF) via env vars alone, no
# code change, if more accuracy is wanted and resources allow.
SLM_MODEL_REPO = os.getenv("SLM_MODEL_REPO", "Qwen/Qwen2.5-1.5B-Instruct-GGUF")
SLM_MODEL_FILE = os.getenv("SLM_MODEL_FILE", "qwen2.5-1.5b-instruct-q4_k_m.gguf")

# Downloaded once via huggingface_hub and cached here -- a one-time public
# download, not an ongoing cloud dependency once the firm is running.
SLM_MODELS_DIR = os.getenv("SLM_MODELS_DIR", str(VAR_DIR / "models"))

SLM_N_CTX = int(os.getenv("SLM_N_CTX", "4096"))
SLM_N_THREADS = int(os.getenv("SLM_N_THREADS", str(os.cpu_count() or 4)))
# 0 = CPU only. Set to a layer count (or -1 for "all layers") to offload
# to the on-prem NVIDIA GPU described in the demo brief -- config only.
SLM_N_GPU_LAYERS = int(os.getenv("SLM_N_GPU_LAYERS", "0"))

# ----------------------------------------------------------------------
# Shared confidence / review thresholds
# ----------------------------------------------------------------------

# Below this, any demo's output gets routed to the human-review queue.
# Matches CONFIDENCE_REVIEW_THRESHOLD in demo_1/classifier.py and
# demo_1/extractor.py so "needs review" means the same thing everywhere.
CONFIDENCE_REVIEW_THRESHOLD = float(os.getenv("CONFIDENCE_REVIEW_THRESHOLD", "0.6"))

# ----------------------------------------------------------------------
# Demo 2 -- reminder / document collection agent
# ----------------------------------------------------------------------

REMINDER_FOLLOWUP_INTERVAL_DAYS = int(os.getenv("REMINDER_FOLLOWUP_INTERVAL_DAYS", "3"))
REMINDER_MAX_FOLLOWUPS = int(os.getenv("REMINDER_MAX_FOLLOWUPS", "3"))
REMINDER_DEFAULT_CHANNEL = os.getenv("REMINDER_DEFAULT_CHANNEL", "email")

# Rough minutes an accountant would spend chasing one missing document by
# hand -- used only to compute the "hours saved" demo metric, nothing
# operational depends on it.
REMINDER_MANUAL_MINUTES_PER_DOC = float(os.getenv("REMINDER_MANUAL_MINUTES_PER_DOC", "12"))

# ----------------------------------------------------------------------
# Demo 3 -- financial analysis / advisory report agent
# ----------------------------------------------------------------------

# Generic industry-benchmark ratios used when a caller doesn't supply
# its own benchmark set. Illustrative for the demo, not a real dataset.
DEFAULT_BENCHMARKS = {
    "gross_margin_pct": 40.0,
    "net_margin_pct": 8.0,
    "current_ratio": 1.5,
    "quick_ratio": 1.0,
    "dso_days": 45.0,
    "debt_to_equity": 1.0,
}

# Anomaly-detection thresholds (deltas vs. prior period or benchmark)
ANOMALY_MARGIN_DECLINE_PCT_POINTS = float(os.getenv("ANOMALY_MARGIN_DECLINE_PCT_POINTS", "3.0"))
ANOMALY_DSO_INCREASE_DAYS = float(os.getenv("ANOMALY_DSO_INCREASE_DAYS", "10.0"))
ANOMALY_CASH_STRAIN_CURRENT_RATIO = float(os.getenv("ANOMALY_CASH_STRAIN_CURRENT_RATIO", "1.0"))


def ensure_dirs() -> None:
    """Create local var/ directories the app writes to, if missing."""
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    Path(SLM_MODELS_DIR).mkdir(parents=True, exist_ok=True)
