#!/usr/bin/env python3
"""Append one account-usage measurement point to <data_dir>/samples/usage.csv.

Pure script, no model calls. Source of the percentages is only the night-mode scheduler's
`status` subcommand (never the snapshot file). Never disturbs the caller: any failure to
obtain a reading is skipped silently with exit 0; only configuration errors exit 2.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

CONFIG_PATH = Path("~/.config/usage-ledger/config.json")
DEFAULT_SCHEDULER = "~/.claude/night-mode/scheduler.py"
COLUMNS = ["ts_utc", "five_hour_pct", "five_hour_resets_at", "seven_day_pct", "seven_day_resets_at",
           "source", "age_sec", "stale"]


def read_status(scheduler: Path, timeout: int = 30) -> Optional[dict[str, Any]]:
    try:
        res = subprocess.run([sys.executable, str(scheduler), "status"], capture_output=True,
                             text=True, timeout=timeout)
        data = json.loads(res.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def make_row(status: dict[str, Any], now: dt.datetime, max_age: int) -> dict[str, Any]:
    age = status.get("snapshot_age_sec")
    stale = bool(status.get("stale")) or not isinstance(age, (int, float)) or age > max_age

    def num(k: str) -> Any:
        v = status.get(k)
        return v if isinstance(v, (int, float)) else ""

    return {"ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "five_hour_pct": num("five_hour_pct"),
            "five_hour_resets_at": num("five_hour_resets_at"), "seven_day_pct": num("seven_day_pct"),
            "seven_day_resets_at": num("seven_day_resets_at"), "source": status.get("source") or "",
            "age_sec": age if isinstance(age, (int, float)) else "", "stale": int(stale)}


def append_row(path: Path, row: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    if not new:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - 512))
            tail = fh.read().decode("utf-8", "replace").strip().splitlines()
        if tail and tail[-1].startswith(row["ts_utc"] + ","):
            return False
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)
    return True


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Append a usage measurement point (0 tokens).")
    ap.add_argument("--data-dir", help="ledger data repo (default: config data_dir)")
    ap.add_argument("--config", default=str(CONFIG_PATH), help=f"config file (default {CONFIG_PATH})")
    ap.add_argument("--scheduler", help=f"night-mode scheduler.py (env USAGE_LEDGER_SCHEDULER, config "
                                        f"'scheduler', default {DEFAULT_SCHEDULER})")
    ap.add_argument("--max-age", type=int, default=90, help="mark stale above this snapshot age in s (default 90)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    cfg: dict[str, Any] = {}
    cp = Path(args.config).expanduser()
    if cp.exists():
        try:
            cfg = json.loads(cp.read_text())
            if not isinstance(cfg, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            print(f"error: cannot read config {cp}: {exc}", file=sys.stderr)
            return 2
    data = args.data_dir or os.environ.get("USAGE_LEDGER_DATA_DIR") or cfg.get("data_dir")
    if not data:
        print("error: data_dir missing (--data-dir or config.json)", file=sys.stderr)
        return 2
    sched = Path(args.scheduler or os.environ.get("USAGE_LEDGER_SCHEDULER") or cfg.get("scheduler")
                 or DEFAULT_SCHEDULER).expanduser()
    status = read_status(sched)
    if status is None:
        return 0
    try:
        append_row(Path(data).expanduser() / "samples" / "usage.csv",
                   make_row(status, dt.datetime.now(dt.timezone.utc), args.max_age))
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
