"""
generate_client_roster.py

Generates a full synthetic client roster (default 50, via Faker's
it_IT locale -- the brief's own "local IT Faker") and seeds all three
demos against it end to end, through the real pipeline:
  - Demo 1: 1 XML + N-1 PDF/text invoices per client, run through
    Demo1Orchestrator (classify -> extract -> validate -> journal
    entry -> supplier learning), same as any uploaded document.
  - Demo 2: an expected-document checklist for the last couple of
    periods per client, with a realistic partial-received subset, so
    ReminderOrchestrator.run_for_roster has real gaps to chase.
  - Demo 3: 2-3 fiscal years of statement figures per client (each
    client assigned a growing/flat/declining trajectory), fed
    sequentially through ReportOrchestrator so period-over-period
    comparison and anomalies show up on their own -- no anomaly logic
    duplicated here, report_agent.py's existing detect_anomalies()
    does that from the numbers alone.

Additive, not a replacement: src/database/seed_demo_data.py's existing
3-client quick-seed (client ids c-001..c-003) is untouched and still
works as the fast smoke-test path. This script uses a separate id
namespace (r-001..r-0NN) so the two never collide.

Bank-statement CSVs are deliberately NOT generated here -- the brief's
own non-overlap rule excludes "basic reconciliation" (TeamSystem's
territory), and accounting_agent.py's journal-entry builder is
invoice-shaped, not statement-shaped. The "financial statements & bank
statements" dataset need is covered by the Demo 3 figures above, which
is what report_agent.py actually consumes.

Usage (from repo root):
    python src/data/generate_client_roster.py                       # 50 clients, full defaults
    python src/data/generate_client_roster.py --n-clients 5 --docs-per-client 1   # fast smoke test (~1-2 min)

Runtime: non-XML documents go through the local SLM (~20-25s/call on
CPU-only hardware). Default (1 XML + 1 non-XML per client x 50
clients) is ~50 SLM calls, roughly 15-20 minutes -- a one-time setup
cost, not something this script tries to parallelize (the local model
serves one call at a time regardless).
"""

import argparse
import logging
import os
import random
import sys
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
    _make_invoice_data, generate_xml, generate_text, generate_pdf_native, SAMPLES_DIR,
)

DOC_TYPES = ["bank_statement", "sales_invoices", "purchase_invoices", "payroll"]
TONES = ["formal", "friendly"]
TRAJECTORIES = ["growing", "flat", "declining"]

# Fraction of a period's checklist marked "already received" when
# seeding Demo 2 -- partial on purpose, so run_for_roster has real
# gaps to chase (brief: "partial 'already received' set").
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
# Demo 1 -- invoices per client, run through the real pipeline
# ----------------------------------------------------------------------

def _write_and_process_docs(client: Dict[str, Any], global_i_start: int, docs_per_client: int, orchestrator) -> Dict[str, int]:
    for sub in ("xml", "pdf", "text"):
        os.makedirs(os.path.join(SAMPLES_DIR, sub), exist_ok=True)

    stats = {"total": 0, "ready_to_post": 0, "needs_review": 0, "errors": 0}
    customer = (client["name"], client["client_id"])

    for j in range(docs_per_client):
        global_i = global_i_start + j
        data = _make_invoice_data(global_i, customer=customer)

        if j == 0:
            # First doc per client is always XML -- fast (no model
            # call) and exercises the structured extraction + full
            # accounting path deterministically for every client.
            path = os.path.join(SAMPLES_DIR, "xml", f"roster_{client['client_id']}_{j}.xml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(generate_xml(data))
        elif j % 2 == 1:
            path = os.path.join(SAMPLES_DIR, "pdf", f"roster_{client['client_id']}_{j}.pdf")
            generate_pdf_native(data, path)
        else:
            path = os.path.join(SAMPLES_DIR, "text", f"roster_{client['client_id']}_{j}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(generate_text(data))

        stats["total"] += 1
        try:
            result = orchestrator.process_file(path, client_id=client["client_id"])
            status = result.get("status")
            if status == "accounted":
                stats["ready_to_post"] += 1
            elif status == "needs_review":
                stats["needs_review"] += 1
            else:
                stats["errors"] += 1
        except Exception:
            logger.exception(f"Unhandled error processing {path}")
            stats["errors"] += 1

    return stats


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
        doc_types = random.sample(DOC_TYPES, k=random.randint(2, len(DOC_TYPES)))
        memory.seed_expected_documents(client_id, period, doc_types)
        for doc_type in doc_types:
            if random.random() < RECEIVED_FRACTION:
                memory.mark_document_received(client_id, period, doc_type)


# ----------------------------------------------------------------------
# Demo 3 -- 2-3 fiscal years of statement figures per client
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


def _seed_statements(report_orch, client: Dict[str, Any], years: List[str]) -> int:
    base_revenue = random.uniform(200_000, 2_000_000)
    generated = 0
    for idx, year in enumerate(years):
        statement = _make_statement(base_revenue, client["trajectory"], idx)
        try:
            report_orch.generate_report(client_id=client["client_id"], period=year, statement=statement)
            generated += 1
        except Exception:
            logger.exception(f"Unhandled error generating report for {client['client_id']} / {year}")
    return generated


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------

def run(n_clients: int, docs_per_client: int, checklist_periods: int, fiscal_years: int) -> None:
    from src.memory.memory import MemoryStore
    from src.orchestration.demo1_orchestrator import Demo1Orchestrator
    from src.orchestration.demo3_orchestrator import ReportOrchestrator

    memory = MemoryStore()
    demo1 = Demo1Orchestrator(memory=memory)
    demo3 = ReportOrchestrator(memory=memory)

    clients = generate_clients(n_clients)
    periods = _recent_periods(checklist_periods)
    years = [str(date.today().year - fiscal_years + 1 + i) for i in range(fiscal_years)]

    doc_totals = {"total": 0, "ready_to_post": 0, "needs_review": 0, "errors": 0}
    reports_generated = 0
    global_i = 0

    for client in clients:
        memory.upsert_client(
            client["client_id"], client["name"],
            vat_number=client["vat"], email=client["email"], phone=client["phone"],
            preferred_tone=client["tone"],
        )

        stats = _write_and_process_docs(client, global_i, docs_per_client, demo1)
        global_i += docs_per_client
        for k in doc_totals:
            doc_totals[k] += stats[k]

        _seed_checklist(memory, client["client_id"], periods)
        reports_generated += _seed_statements(demo3, client, years)

    _print_summary(clients, doc_totals, reports_generated, memory)


def _print_summary(clients: List[Dict[str, Any]], doc_totals: Dict[str, int], reports_generated: int, memory) -> None:
    print("\n" + "=" * 72)
    print("CLIENT ROSTER SEEDED")
    print("=" * 72)
    print(f"\nClients created: {len(clients)}")
    print(f"  formal tone: {sum(1 for c in clients if c['tone'] == 'formal')}, "
          f"friendly tone: {sum(1 for c in clients if c['tone'] == 'friendly')}")
    print(f"  trajectories: " + ", ".join(
        f"{t}={sum(1 for c in clients if c['trajectory'] == t)}" for t in TRAJECTORIES
    ))

    print(f"\nDemo 1 documents: total={doc_totals['total']}  ready_to_post={doc_totals['ready_to_post']}  "
          f"needs_review={doc_totals['needs_review']}  errors={doc_totals['errors']}")
    if doc_totals["total"]:
        print(f"  ready-to-post rate: {doc_totals['ready_to_post'] / doc_totals['total'] * 100:.1f}%")

    print(f"\nDemo 3 reports generated: {reports_generated}")

    review_queue = memory.list_review_queue()
    print(f"\nReview queue (all demos): {len(review_queue)} open item(s)")

    recognized = 0
    from src.data.generate_synthetic_samples import SUPPLIERS
    for client in clients:
        for supplier_name, _, _, _ in SUPPLIERS:
            if memory.get_supplier_hint(client["client_id"], supplier_name):
                recognized += 1
    print(f"Recurring suppliers recognized across the roster: {recognized}")

    print("\nRoster ready -- GET /api/clients, /api/demo-2/dashboard/<period>, /api/demo-3/reports/<client_id>.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate and seed a full synthetic client roster across all 3 demos.")
    parser.add_argument("--n-clients", type=int, default=50, help="Number of clients to generate (default: 50)")
    parser.add_argument("--docs-per-client", type=int, default=2, help="Demo 1 invoices per client (default: 2)")
    parser.add_argument("--checklist-periods", type=int, default=2, help="Demo 2 periods to seed a checklist for (default: 2)")
    parser.add_argument("--fiscal-years", type=int, default=3, help="Demo 3 fiscal years per client (default: 3)")
    args = parser.parse_args()

    run(args.n_clients, args.docs_per_client, args.checklist_periods, args.fiscal_years)
