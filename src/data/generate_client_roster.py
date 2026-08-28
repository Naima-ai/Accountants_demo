"""
generate_client_roster.py

Generates a full synthetic client roster (default 50, via Faker's
it_IT locale -- the brief's own "local IT Faker") and, per client:
  - exactly 4 Demo 1 documents (one of each format the pipeline
    supports): XML, native PDF, scanned PDF, and a real photographed
    receipt image -- registered in the DB but NOT processed. Nothing
    is classified, extracted, validated, or booked at generation time;
    that only happens when a document is explicitly selected and Run
    from the Demo 1 UI (or POST /api/demo-1/documents/{doc_id}/run).
  - a Demo 2 expected-document checklist for the last couple of
    periods, partially received, so ReminderOrchestrator.run_for_roster
    has real gaps to chase.
  - 2-3 fiscal years of internally-consistent financial-statement
    figures (each client assigned a growing/flat/declining trajectory)
    -- stored as raw data only, NOT run through ReportOrchestrator. The
    advisory report (with its SLM-written narrative) is generated on
    demand from the Advisory Report page, same "generate, don't
    pre-run" principle as Demo 1.

Because nothing here calls the SLM or Tesseract, this is pure file/DB
writes -- expect the full 50-client roster to finish in well under a
minute, not the tens of minutes earlier versions of this script took.

Additive, not a replacement: src/database/seed_demo_data.py's existing
3-client quick-seed (client ids c-001..c-003, which DOES run the full
pipeline immediately) is untouched. This script uses a separate id
namespace (r-001..r-0NN) so the two never collide.

Usage (from repo root):
    python src/data/generate_client_roster.py                  # 50 clients, full defaults
    python src/data/generate_client_roster.py --n-clients 5     # fast smoke test

Run `python src/data/download_real_samples.py` first (once, needs
network) if data_set/samples/images/ is empty -- without it, the image
document per client falls back to another scanned PDF instead.
"""

import argparse
import glob
import logging
import os
import random
import sys
import uuid
from datetime import date
from typing import Any, Dict, List

logger = logging.getLogger("generate_client_roster")
logging.basicConfig(level=logging.INFO)

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly (python src/data/generate_client_roster.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.data.generate_synthetic_samples import (
    _make_invoice_data, generate_xml, generate_text, generate_pdf_native, generate_pdf_scanned, SAMPLES_DIR,
)

IMAGES_DIR = os.path.join(SAMPLES_DIR, "images")
TONES = ["formal", "friendly"]
TRAJECTORIES = ["growing", "flat", "declining"]
DEMO2_DOC_TYPES = ["bank_statement", "sales_invoices", "purchase_invoices", "payroll"]

# Fraction of a Demo 2 period's checklist marked "already received" --
# partial on purpose, so run_for_roster has real gaps to chase.
RECEIVED_FRACTION = 0.65


# ----------------------------------------------------------------------
# Roster (client identities)
# ----------------------------------------------------------------------

def generate_clients(n: int) -> List[Dict[str, Any]]:
    from faker import Faker

    fake = Faker("it_IT")
    Faker.seed(42)

    clients = []
    for i in range(n):
        clients.append({
            "client_id": f"r-{i + 1:03d}",
            "name": fake.company(),
            "vat": fake.company_vat(),
            "email": fake.company_email(),
            "phone": fake.phone_number(),
            "tone": TONES[i % len(TONES)],
            "trajectory": TRAJECTORIES[i % len(TRAJECTORIES)],
        })
    return clients


# ----------------------------------------------------------------------
# Demo 1 -- 4 document files per client, registered but NOT processed
# ----------------------------------------------------------------------

def _real_receipt_images() -> List[str]:
    if not os.path.isdir(IMAGES_DIR):
        return []
    return sorted(glob.glob(os.path.join(IMAGES_DIR, "real_*.png")))


def _register_documents(
    memory, client: Dict[str, Any], global_i: int, real_images: List[str], image_cycle: int,
) -> int:
    """Writes and registers exactly 4 documents for one client -- XML,
    native PDF, scanned PDF, and a real receipt image -- with
    status="uploaded" (nothing processed). Returns the updated
    image_cycle index."""
    for sub in ("xml", "pdf", "pdf_scanned"):
        os.makedirs(os.path.join(SAMPLES_DIR, sub), exist_ok=True)

    customer = (client["name"], client["client_id"])
    client_id = client["client_id"]

    xml_data = _make_invoice_data(global_i, customer=customer)
    xml_path = os.path.join(SAMPLES_DIR, "xml", f"roster_{client_id}_0.xml")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(generate_xml(xml_data))

    pdf_data = _make_invoice_data(global_i + 1, customer=customer)
    pdf_path = os.path.join(SAMPLES_DIR, "pdf", f"roster_{client_id}_1.pdf")
    generate_pdf_native(pdf_data, pdf_path)

    scanned_data = _make_invoice_data(global_i + 2, customer=customer)
    scanned_path = os.path.join(SAMPLES_DIR, "pdf_scanned", f"roster_{client_id}_2.pdf")
    generate_pdf_scanned(scanned_data, scanned_path)

    if real_images:
        image_path = real_images[image_cycle % len(real_images)]
        image_cycle += 1
    else:
        # No real images available -- fall back to another scanned PDF
        # rather than silently dropping this slot.
        image_data = _make_invoice_data(global_i + 3, customer=customer)
        image_path = os.path.join(SAMPLES_DIR, "pdf_scanned", f"roster_{client_id}_3.pdf")
        generate_pdf_scanned(image_data, image_path)

    for path, file_type in (
        (xml_path, "xml"), (pdf_path, "pdf_text"), (scanned_path, "pdf_scanned"), (image_path, "image"),
    ):
        memory.record_document(
            doc_id=str(uuid.uuid4()), original_filename=os.path.basename(path), source_path=path,
            client_id=client_id, file_type=file_type, status="uploaded",
        )

    return image_cycle


# ----------------------------------------------------------------------
# Demo 2 -- expected-document checklist, partially received
# ----------------------------------------------------------------------

def _recent_periods(n: int) -> List[str]:
    """Last n calendar periods as 'YYYY-MM', oldest first."""
    today = date.today()
    y, m = today.year, today.month
    periods = []
    for _ in range(n):
        periods.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(periods))


def _seed_checklist(memory, client_id: str, periods: List[str]) -> None:
    for period in periods:
        doc_types = random.sample(DEMO2_DOC_TYPES, k=random.randint(2, len(DEMO2_DOC_TYPES)))
        memory.seed_expected_documents(client_id, period, doc_types)
        for doc_type in doc_types:
            if random.random() < RECEIVED_FRACTION:
                memory.mark_document_received(client_id, period, doc_type)


# ----------------------------------------------------------------------
# Demo 3 -- 2-3 fiscal years of RAW statement figures per client
# ----------------------------------------------------------------------

_TRAJECTORY_GROWTH = {"growing": 1.08, "flat": 1.00, "declining": 0.90}


def _make_statement(base_revenue: float, trajectory: str, year_index: int) -> Dict[str, float]:
    revenue = base_revenue * (_TRAJECTORY_GROWTH[trajectory] ** year_index)
    cogs_ratio = random.uniform(0.55, 0.65)
    opex_ratio = random.uniform(0.30, 0.38) if trajectory == "declining" else random.uniform(0.25, 0.32)
    stress = 1.3 if trajectory == "declining" else 1.0

    cogs = revenue * cogs_ratio
    opex = revenue * opex_ratio
    current_assets = revenue * random.uniform(0.25, 0.35) / stress
    inventory = current_assets * random.uniform(0.10, 0.25)
    current_liabilities = revenue * random.uniform(0.20, 0.30) * stress
    total_debt = revenue * random.uniform(0.15, 0.35)
    equity = revenue * random.uniform(0.30, 0.50)
    accounts_receivable = revenue * random.uniform(0.08, 0.12) * stress

    return {
        "revenue": round(revenue, 2),
        "cogs": round(cogs, 2),
        "operating_expenses": round(opex, 2),
        "net_income": round(revenue - cogs - opex, 2),
        "current_assets": round(current_assets, 2),
        "inventory": round(inventory, 2),
        "current_liabilities": round(current_liabilities, 2),
        "total_debt": round(total_debt, 2),
        "equity": round(equity, 2),
        "accounts_receivable": round(accounts_receivable, 2),
    }


def _seed_statements(memory, client: Dict[str, Any], years: List[str]) -> int:
    base_revenue = random.uniform(200_000, 2_000_000)
    stored = 0
    for idx, year in enumerate(years):
        statement = _make_statement(base_revenue, client["trajectory"], idx)
        memory.store_financial_statement(client["client_id"], year, statement)
        stored += 1
    return stored


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------

def run(n_clients: int, fiscal_years: int, checklist_periods: int) -> None:
    from src.memory.memory import MemoryStore

    memory = MemoryStore()

    clients = generate_clients(n_clients)
    years = [str(date.today().year - fiscal_years + 1 + i) for i in range(fiscal_years)]
    periods = _recent_periods(checklist_periods)

    real_images = _real_receipt_images()
    if real_images:
        logger.info(f"Found {len(real_images)} real receipt image(s) -- cycling them across the roster.")
    else:
        logger.warning(
            "No real receipt images found under data_set/samples/images/. Run "
            "`python src/data/download_real_samples.py` first if you want the image slot per client to be a "
            "real photographed receipt -- falling back to another scanned PDF for that slot instead."
        )

    global_i = 0
    image_cycle = 0
    documents_registered = 0
    statements_stored = 0

    for client in clients:
        memory.upsert_client(
            client["client_id"], client["name"],
            vat_number=client["vat"], email=client["email"], phone=client["phone"],
            preferred_tone=client["tone"],
        )

        image_cycle = _register_documents(memory, client, global_i, real_images, image_cycle)
        global_i += 4
        documents_registered += 4

        _seed_checklist(memory, client["client_id"], periods)

        statements_stored += _seed_statements(memory, client, years)

    _print_summary(clients, documents_registered, statements_stored, periods)


def _print_summary(clients: List[Dict[str, Any]], documents_registered: int, statements_stored: int, periods: List[str]) -> None:
    print("\n" + "=" * 72)
    print("CLIENT ROSTER GENERATED (no documents processed, no reports generated)")
    print("=" * 72)
    print(f"\nClients created: {len(clients)}")
    print(f"  formal tone: {sum(1 for c in clients if c['tone'] == 'formal')}, "
          f"friendly tone: {sum(1 for c in clients if c['tone'] == 'friendly')}")
    print(f"  trajectories: " + ", ".join(
        f"{t}={sum(1 for c in clients if c['trajectory'] == t)}" for t in TRAJECTORIES
    ))

    print(f"\nDemo 1 documents registered: {documents_registered} (4 per client: XML, native PDF, "
          f"scanned PDF, image) -- all status=uploaded, nothing classified/extracted/validated/booked yet.")

    print(f"\nDemo 2 checklist seeded for periods: {', '.join(periods)}")

    print(f"\nDemo 3 raw financial statements stored: {statements_stored} -- no advisory reports generated yet.")

    print("\nNext steps:")
    print("  Demo 1: pick a client on the Doc-to-Data page, select one of their documents, click Run.")
    print("  Demo 2: run the roster from the Reminder Agent page.")
    print("  Demo 3: pick a client on the Advisory Report page (Existing Client mode) to load their years and generate a report.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a synthetic client roster: clients, 4 Demo 1 documents each, a Demo 2 checklist, and raw Demo 3 statements -- no agents run.")
    parser.add_argument("--n-clients", type=int, default=50, help="Number of clients to generate (default: 50)")
    parser.add_argument("--fiscal-years", type=int, default=3, help="Demo 3 fiscal years per client (default: 3)")
    parser.add_argument("--checklist-periods", type=int, default=2, help="Demo 2 periods to seed a checklist for (default: 2)")
    args = parser.parse_args()

    run(args.n_clients, args.fiscal_years, args.checklist_periods)
