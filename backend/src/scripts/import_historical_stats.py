from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.models.database import AsyncSessionLocal
from src.services.historical_stats import (
    DEFAULT_HISTORICAL_STATS_CUTOFF,
    DEFAULT_HISTORICAL_STATS_SOURCE,
    DEFAULT_UNIT_STAKE_RUB,
    apply_historical_stats_import,
    parse_historical_stats_workbook,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Shamrai historical stats workbook.")
    parser.add_argument("--source", default=str(DEFAULT_HISTORICAL_STATS_SOURCE), help="Path to historical .xlsx workbook.")
    parser.add_argument(
        "--cutoff",
        default=DEFAULT_HISTORICAL_STATS_CUTOFF.isoformat(),
        help="Live bets before this timestamp are treated as covered by the historical baseline.",
    )
    parser.add_argument("--unit-stake-rub", default=str(DEFAULT_UNIT_STAKE_RUB), help="Unit stake used by the workbook.")
    parser.add_argument("--apply", action="store_true", help="Persist parsed data. Without this flag the command is dry-run only.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    cutoff = datetime.fromisoformat(str(args.cutoff))
    parsed = parse_historical_stats_workbook(
        Path(args.source),
        cutoff_at=cutoff,
        unit_stake_rub=Decimal(str(args.unit_stake_rub)),
    )
    async with AsyncSessionLocal() as db:
        report = await apply_historical_stats_import(db, parsed, apply=bool(args.apply))
        if args.apply:
            await db.commit()
    print(json.dumps({
        "status": report.status,
        "source_filename": report.source_filename,
        "source_sha256": report.source_sha256,
        "cutoff_at": report.cutoff_at.isoformat(),
        "monthly_rows": report.monthly_rows,
        "breakdown_rows": report.breakdown_rows,
        "detail_rows": report.detail_rows,
        "total_bets": report.total_bets,
        "total_profit_rub": str(report.total_profit_rub),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
