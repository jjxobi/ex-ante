"""Command line entrypoints for the ingest components."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from eq import health, ingest, paths, publish, region, render, revisions, storage
from eq import snapshots as snapshot_selection


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

    sub.add_parser(
        "region-build",
        help="regenerate the frozen grid and depth boundary (run once; outputs are committed)",
    )

    sub.add_parser(
        "publish-cycle",
        help=(
            "fit both registered models on both strata against the newest ingested "
            "snapshot and publish the next daily and weekly window for each (8 "
            "forecasts). Refuses per window (Rule 1, D11) if a target window has "
            "already started."
        ),
    )

    rnd = sub.add_parser(
        "render",
        help="score every due window and render one model/horizon/stratum scoreboard as static JSON",
    )
    rnd.add_argument("--model", required=True, choices=publish.MODELS)
    rnd.add_argument("--horizon", required=True, choices=publish.HORIZONS)
    rnd.add_argument("--stratum", required=True, choices=publish.STRATA)
    rnd.add_argument(
        "--record-start",
        type=_as_date,
        default=None,
        help=(
            "defaults to this (model, horizon, stratum)'s own earliest "
            "published window, so the record defines its own start rather "
            "than needing a hand-configured date"
        ),
    )
    rnd.add_argument("--out", default=None, help="defaults under site/<model>/<horizon>/<stratum>.json")

    sub.add_parser(
        "health",
        help="exit non-zero if the newest published forecast is more than 48 hours old",
    )

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
        # Date-shaped only. A bare catalogue-*.parquet glob also matches
        # catalogue-ci.parquet, and because "c" sorts after "2" the lexical
        # maximum returns the CI slice in preference to every real dated
        # catalogue. That is the trap recorded in D4b, verified against DuckDB.
        dated = [path for _, path in snapshot_selection.dated_snapshots()]
        if len(dated) < 2:
            print("a diff needs two snapshots; fewer than two are present, skipping")
            return 0
        previous, current = dated[-2], dated[-1]
        destination = paths.DATA_DIR / "revisions" / f"revisions-{observed_at.isoformat()}.parquet"
        written = revisions.write_daily_diff(previous, current, destination, observed_at)
        print(f"wrote {written} revision rows to {destination}")
        return 0

    if args.command == "region-build":
        result = region.build_and_write()
        print(
            f"wrote {result['grid_cells']} grid cells across {result['region_cells']} "
            f"region cells, hash {result['grid_hash']}"
        )
        return 0

    if args.command == "publish-cycle":
        newest = snapshot_selection.newest_snapshot()
        events = storage.read_parquet(newest)
        cycle = publish.publish_cycle(events, input_catalogue_path=newest)
        for published in cycle.published:
            print(
                f"published {published.model}/{published.horizon}/{published.stratum} "
                f"window {published.window_start.date().isoformat()} -> "
                f"{published.forecast_path}"
            )
        return 0

    if args.command == "render":
        record_start = args.record_start
        if record_start is None:
            earliest = render.earliest_published_window_start(args.model, args.horizon, args.stratum)
            if earliest is None:
                print(
                    f"nothing has ever been published for {args.model}/{args.horizon}/"
                    f"{args.stratum}; nothing to render"
                )
                return 0
            record_start = earliest
        scoreboard = render.build_scoreboard(
            args.model, args.horizon, args.stratum, record_start=record_start
        )
        payload = render.render_site({f"{args.model}/{args.horizon}/{args.stratum}": scoreboard})
        destination = (
            Path(args.out)
            if args.out
            else paths.SITE_DIR / args.model / args.horizon / f"{args.stratum}.json"
        )
        render.write_site(payload, destination)
        print(f"wrote {destination}")
        return 0

    if args.command == "health":
        check = health.check_health()
        print(json.dumps(health.to_json(check), indent=2))
        return 0 if check.healthy else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
