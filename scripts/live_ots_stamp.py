"""Live OpenTimestamps check against the real public calendar servers.

tests/test_anchor.py mocks every calendar so the unit suite stays hermetic,
fast, and independent of network reachability. That is correct for a suite
that runs on every commit, but it means the suite alone can never prove this
project's OpenTimestamps integration actually talks to a real calendar. This
script is the live counterpart: it performs the real network call the tests
deliberately avoid, and it is why it lives in scripts/ rather than in
tests/. Run it by hand, not in CI's normal test step.

Usage:
    python scripts/live_ots_stamp.py stamp   <path>
    python scripts/live_ots_stamp.py status  <path>.ots
    python scripts/live_ots_stamp.py upgrade <path>.ots
    python scripts/live_ots_stamp.py verify  <path> <path>.ots

`stamp` submits the file's SHA-256 to the default calendar servers and writes
`<path>.ots`. The proof will read as "pending", not "confirmed": no calendar
issues a Bitcoin attestation synchronously, so a freshly stamped proof always
starts pending and only becomes confirmed once `upgrade` is run again, hours
later, after the calendar has folded the digest into a Bitcoin transaction
and that transaction has confirmed on-chain.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eq import anchor  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="live_ots_stamp",
        description="Stamp, check, upgrade or verify a real file against live OpenTimestamps calendars.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_stamp = sub.add_parser("stamp", help="submit a file's digest to the live calendars")
    p_stamp.add_argument("path")

    p_status = sub.add_parser("status", help="read a local proof's status (no network call)")
    p_status.add_argument("ots_path")

    p_upgrade = sub.add_parser("upgrade", help="ask the live calendars whether a pending proof has completed")
    p_upgrade.add_argument("ots_path")

    p_verify = sub.add_parser("verify", help="recompute a file's digest and check it against its proof")
    p_verify.add_argument("path")
    p_verify.add_argument("ots_path")

    args = parser.parse_args(argv)

    if args.command == "stamp":
        path = Path(args.path)
        ots_path = anchor.stamp(path)
        size = ots_path.stat().st_size
        status = anchor.proof_status(ots_path)
        print(f"wrote {ots_path} ({size} bytes)")
        print(f"status: {status}")
        if status == anchor.STATUS_PENDING:
            print(
                "this is expected: a fresh proof is a calendar commitment, not "
                "yet a Bitcoin attestation. Run `upgrade` again in a few hours."
            )
        return 0

    if args.command == "status":
        print(anchor.proof_status(Path(args.ots_path)))
        return 0

    if args.command == "upgrade":
        result = anchor.upgrade(Path(args.ots_path))
        for key, value in result.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "verify":
        result = anchor.verify(Path(args.path), Path(args.ots_path))
        for key, value in result.items():
            print(f"{key}: {value}")
        return 0 if result["file_matches_proof"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
