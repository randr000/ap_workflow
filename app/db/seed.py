"""Seed master data from data/*.json.

    python -m app.db.seed          # reset and seed the configured database
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.config import DATA_DIR, load_settings
from app.db.database import init_db, make_engine, make_session_factory
from app.db.repository import Repository
from app.domain.reference import ReferenceData


def _load(path: Path):
    # parse_float=Decimal: any numeric literal in the data files becomes Decimal, never float.
    with path.open() as f:
        return json.load(f, parse_float=Decimal)


def load_reference_from_files(data_dir: Path = DATA_DIR) -> ReferenceData:
    ref = ReferenceData(
        vendors=_load(data_dir / "vendors.json"),
        accounts=_load(data_dir / "accounts.json"),
        departments=_load(data_dir / "departments.json"),
        locations=_load(data_dir / "locations.json"),
        periods=_load(data_dir / "accounting_periods.json"),
        purchase_orders=_load(data_dir / "purchase_orders.json"),
        receipts=_load(data_dir / "receipts.json"),
        coding_history=_load(data_dir / "coding_history.json"),
    )
    # Receipts are the source of truth for received quantities; keep PO lines in sync.
    for po in ref.purchase_orders:
        for line in po.lines:
            line.received_quantity = ref.received_quantity(po.po_number, line.line_number)
    return ref


def seed_repository(repo: Repository, data_dir: Path = DATA_DIR) -> ReferenceData:
    ref = load_reference_from_files(data_dir)
    repo.add_reference(ref)
    return ref


def create_seeded_repository(url: str, reset: bool = True) -> Repository:
    engine = make_engine(url)
    init_db(engine, reset=reset)
    repo = Repository(make_session_factory(engine))
    seed_repository(repo)
    return repo


def main() -> None:
    settings = load_settings()
    create_seeded_repository(settings.database_url, reset=True)
    print(f"Seeded master data into {settings.database_url}")


if __name__ == "__main__":
    main()
