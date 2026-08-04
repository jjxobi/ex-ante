"""Atomic parquet IO.

Every write lands in a temporary file beside the destination and is then
renamed. On every platform this project targets, rename within a directory is
atomic, so a reader sees either the previous complete file or the new complete
file. A crashed or failed write never leaves a partial catalogue behind.
"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def write_parquet_atomic(records: list[dict], destination: Path) -> Path:
    """Write records to parquet atomically. Returns the destination path."""
    if not records:
        raise ValueError("refusing to write an empty catalogue")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")

    try:
        table = pa.Table.from_pylist(records)
        pq.write_table(table, temp_path, compression="zstd")
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return destination


def read_parquet(path: Path) -> list[dict]:
    """Read a parquet file back into record dicts."""
    return pq.read_table(Path(path)).to_pylist()
