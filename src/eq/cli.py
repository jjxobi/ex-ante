"""Command line entrypoints for the ingest components."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from eq import ingest, paths, revisions


def _as_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eq", description="Catalogue ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="daily full-catalogue snapshot")
    snap.add_argument("--date", type=_as_date, default=None)

    rng = sub.add_parser("range", help="ingest a single date range")
    rng.add_argument("--start", type=_as_date, required=True)
    rng.add_argument("--end", type=_as_date, required=True)
    rng.add_argument("--min-magnitude", type=float, required=True)
    rng.add_argument("--out", required=True)

    dff = sub.add_parser("diff", help="diff the two newest snapshots into a revision record")
    dff.add_argument("--date", type=_as_date, default=None)

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        today = args.date or datetime.now(timezone.utc).date()
        path = ingest.snapshot_full_catalogue(today)
        print(f"wrote {path}")
        return 0

    if args.command == "range":
        count = ingest.ingest_range(
            args.start, args.end, args.min_magnitude, args.out
        )
        print(f"wrote {count} records to {args.out}")
        return 0

    if args.command == "diff":
        observed_at = args.date or datetime.now(timezone.utc).date()
        snapshots = sorted(paths.SNAPSHOT_DIR.glob("catalogue-*.parquet"))
        if len(snapshots) < 2:
            print("a diff needs two snapshots; fewer than two are present, skipping")
            return 0
        previous, current = snapshots[-2], snapshots[-1]
        destination = paths.DATA_DIR / "revisions" / f"revisions-{observed_at.isoformat()}.parquet"
        written = revisions.write_daily_diff(previous, current, destination, observed_at)
        print(f"wrote {written} revision rows to {destination}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
