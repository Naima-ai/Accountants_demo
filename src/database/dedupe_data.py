"""
One-off maintenance script for duplicate rows left behind by re-running
data generators (generate_client_roster.py etc.) or by clicking
"Generate" more than once for the same client/period.

DRY RUN BY DEFAULT -- nothing is deleted until you pass --yes. Stop any
running `streamlit run` / `uvicorn` process pointed at the same
database file before running this with --yes, to avoid SQLite lock
contention on Windows.

Usage (from repo root):
    python src/database/dedupe_data.py                        # report only
    python src/database/dedupe_data.py --yes                  # actually delete
    python src/database/dedupe_data.py --yes --only documents
    python src/database/dedupe_data.py --yes --only statements --keep-statement oldest
    python src/database/dedupe_data.py --yes --only reports --keep-report oldest
"""

import argparse
import json
import os
import sys
from collections import defaultdict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.database.database import (
    AnalysisReport, Document, FinancialStatement, ReviewQueueItem, session_scope,
)

# Furthest-along status chosen when picking which duplicate document to keep
_STATUS_RANK = {"accounted": 3, "needs_review": 2, "uploaded": 1, "failed": 0}

def find_duplicate_documents():
    with session_scope() as s:
        rows = [
            {
                "id": d.id, "client_id": d.client_id, "source_path": d.source_path,
                "original_filename": d.original_filename, "status": d.status,
                "classification": d.classification, "ingested_at": d.ingested_at,
            }
            for d in s.query(Document).all()
        ]
    groups = defaultdict(list)
    for d in rows:
        groups[(d["client_id"], d["source_path"])].append(d)
    return {k: v for k, v in groups.items() if len(v) > 1}


def dedupe_documents(dry_run: bool = True) -> int:
    dupes = find_duplicate_documents()
    if not dupes:
        print("No duplicate documents found (grouped by client_id + source_path).")
        return 0

    to_delete = []
    for (client_id, source_path), rows in dupes.items():
        rows_sorted = sorted(
            rows, key=lambda r: (_STATUS_RANK.get(r["status"], 0), r["ingested_at"]), reverse=True,
        )
        keep, drop = rows_sorted[0], rows_sorted[1:]
        print(f"\n{client_id} :: {source_path}")
        print(f"  KEEP    id={keep['id']}  status={keep['status']}  "
              f"classification={keep['classification']}  ingested_at={keep['ingested_at']}")
        for d in drop:
            print(f"  DELETE  id={d['id']}  status={d['status']}  "
                  f"classification={d['classification']}  ingested_at={d['ingested_at']}")
            to_delete.append(d["id"])

    if dry_run:
        print(f"\n[dry run] Would delete {len(to_delete)} duplicate document row(s). "
              f"Re-run with --yes to apply.")
        return len(to_delete)

    with session_scope() as s:
        for doc_id in to_delete:
            doc = s.get(Document, doc_id)
            if doc is not None:
                s.delete(doc)  
        stale_ids = set(to_delete)
        for item in s.query(ReviewQueueItem).filter_by(ref_type="document").all():
            if item.ref_id in stale_ids:
                s.delete(item)

    print(f"Deleted {len(to_delete)} duplicate document row(s).")
    return len(to_delete)

# Financial statements -- grouped by (client_id, period, statement_type).

def find_duplicate_statements():
    with session_scope() as s:
        rows = [
            {
                "id": row.id, "client_id": row.client_id, "period": row.period,
                "statement_type": row.statement_type, "created_at": row.created_at,
                "data": json.loads(row.data_json),
            }
            for row in s.query(FinancialStatement).all()
        ]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["client_id"], r["period"], r["statement_type"])].append(r)
    return {k: v for k, v in groups.items() if len(v) > 1}


def dedupe_statements(dry_run: bool = True, keep: str = "newest") -> int:
    dupes = find_duplicate_statements()
    if not dupes:
        print("No duplicate financial statements found (grouped by client_id + period + statement_type).")
        return 0

    to_delete = []
    for (client_id, period, stype), rows in dupes.items():
        rows_sorted = sorted(rows, key=lambda r: r["id"])  # insertion order
        keep_row = rows_sorted[-1] if keep == "newest" else rows_sorted[0]
        drop_rows = [r for r in rows_sorted if r["id"] != keep_row["id"]]

        print(f"\n{client_id} :: {period} ({stype})")
        for r in rows_sorted:
            tag = "KEEP  " if r["id"] == keep_row["id"] else "DELETE"
            print(f"  {tag}  id={r['id']}  created_at={r['created_at']}  "
                  f"revenue={r['data'].get('revenue')}  net_income={r['data'].get('net_income')}  "
                  f"equity={r['data'].get('equity')}")
        to_delete.extend(r["id"] for r in drop_rows)

    if dry_run:
        print(f"\n[dry run] Would delete {len(to_delete)} duplicate statement row(s). "
              f"Re-run with --yes to apply (add --keep-statement oldest to keep the "
              f"first-inserted row of each pair instead of the newest).")
        return len(to_delete)

    with session_scope() as s:
        for stmt_id in to_delete:
            row = s.get(FinancialStatement, stmt_id)
            if row is not None:
                s.delete(row)

    print(f"Deleted {len(to_delete)} duplicate statement row(s).")
    return len(to_delete)


# ----------------------------------------------------------------------
# Analysis reports -- grouped by (client_id, period). Each "Generate
# report" click inserts a new row unconditionally, so generating for
# the same client/period more than once produces duplicates -- and if
# more than one of them has an alert-severity anomaly, EACH gets its
# own separate review-queue entry (different ref_id)
# ----------------------------------------------------------------------

def find_duplicate_reports():
    with session_scope() as s:
        rows = [
            {
                "id": row.id, "client_id": row.client_id, "period": row.period,
                "status": row.status, "generated_at": row.generated_at,
                "anomalies": json.loads(row.anomalies_json),
            }
            for row in s.query(AnalysisReport).all()
        ]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["client_id"], r["period"])].append(r)
    return {k: v for k, v in groups.items() if len(v) > 1}


def dedupe_reports(dry_run: bool = True, keep: str = "newest") -> int:
    dupes = find_duplicate_reports()
    if not dupes:
        print("No duplicate analysis reports found (grouped by client_id + period).")
        return 0

    to_delete = []
    for (client_id, period), rows in dupes.items():
        rows_sorted = sorted(rows, key=lambda r: r["id"])  # insertion order
        keep_row = rows_sorted[-1] if keep == "newest" else rows_sorted[0]
        drop_rows = [r for r in rows_sorted if r["id"] != keep_row["id"]]

        print(f"\n{client_id} :: {period}")
        for r in rows_sorted:
            tag = "KEEP  " if r["id"] == keep_row["id"] else "DELETE"
            n_alerts = sum(1 for a in r["anomalies"] if a.get("severity") == "alert")
            print(f"  {tag}  id={r['id']}  generated_at={r['generated_at']}  "
                  f"status={r['status']}  alert_anomalies={n_alerts}")
        to_delete.extend(r["id"] for r in drop_rows)

    if dry_run:
        print(f"\n[dry run] Would delete {len(to_delete)} duplicate analysis report(s). "
              f"Re-run with --yes to apply (add --keep-report oldest to keep the "
              f"first-generated report of each pair instead of the newest).")
        return len(to_delete)

    with session_scope() as s:
        for report_id in to_delete:
            row = s.get(AnalysisReport, report_id)
            if row is not None:
                s.delete(row)
        stale_ids = {str(i) for i in to_delete}
        for item in s.query(ReviewQueueItem).filter_by(ref_type="analysis_report").all():
            if item.ref_id in stale_ids:
                s.delete(item)

    print(f"Deleted {len(to_delete)} duplicate analysis report(s).")
    return len(to_delete)


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Find and remove duplicate documents/financial-statements left by re-running data generators."
    )
    parser.add_argument("--yes", action="store_true",
                        help="Actually delete. Without this flag, only reports what would happen.")
    parser.add_argument("--keep-statement", choices=["newest", "oldest"], default="newest",
                        help="Which duplicate financial statement to keep per (client, period). Default: newest.")
    parser.add_argument("--keep-report", choices=["newest", "oldest"], default="newest",
                        help="Which duplicate analysis report to keep per (client, period). Default: newest.")
    parser.add_argument("--only", choices=["documents", "statements", "reports"], default=None,
                        help="Limit the run to just one category. Default: all three.")
    args = parser.parse_args()

    if not args.yes:
        print("Running in DRY RUN mode -- nothing will be deleted. Pass --yes to apply.\n")

    if args.only in (None, "documents"):
        print("=" * 72)
        print("DOCUMENTS")
        print("=" * 72)
        dedupe_documents(dry_run=not args.yes)

    if args.only in (None, "statements"):
        print("\n" + "=" * 72)
        print("FINANCIAL STATEMENTS")
        print("=" * 72)
        dedupe_statements(dry_run=not args.yes, keep=args.keep_statement)

    if args.only in (None, "reports"):
        print("\n" + "=" * 72)
        print("ANALYSIS REPORTS")
        print("=" * 72)
        dedupe_reports(dry_run=not args.yes, keep=args.keep_report)