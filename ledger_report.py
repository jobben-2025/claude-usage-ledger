#!/usr/bin/env python3
"""Report token usage and API-price equivalent from the CSV ledger.

Pure script, no model calls. Exit codes: 0 ok, 2 usage/configuration error.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

CONFIG_PATH = Path("~/.config/usage-ledger/config.json")
PRICES_PATH = Path("~/.config/usage-ledger/prices.json")
DEFAULT_PRICES = Path(__file__).resolve().parent / "prices.json"
CATS = ["input", "cache_write_5m", "cache_write_1h", "cache_read", "output"]
BY_CHOICES = ("project", "model", "day", "unattended")


class ReportError(Exception):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.expanduser().read_text())
    except (OSError, ValueError) as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportError(f"{path} must be a JSON object")
    return data


def load_prices(cfg: dict[str, Any], explicit: Optional[str]) -> dict[str, dict[str, float]]:
    if explicit:
        raw = load_json(Path(explicit))
    elif isinstance(cfg.get("prices"), dict):
        raw = cfg["prices"]
    elif PRICES_PATH.expanduser().exists():
        raw = load_json(PRICES_PATH)
    else:
        raw = load_json(DEFAULT_PRICES)
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}


def to_int(v: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def local_day(ts: str) -> str:
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone().strftime("%Y-%m-%d")


def group_key(row: dict[str, str], day: str, by: list[str]) -> tuple[str, ...]:
    out = []
    for b in by:
        if b == "project":
            out.append(row["project"])
        elif b == "model":
            out.append(row["model"])
        elif b == "day":
            out.append(day)
        else:
            out.append("unattended" if row["unattended"] == "1" else "interactive")
    return tuple(out)


def new_group() -> dict[str, Any]:
    g: dict[str, Any] = {c: 0 for c in CATS}
    g.update(thinking=0, responses=0, unattended_responses=0, web_search=0, web_fetch=0,
             usd={c: 0.0 for c in CATS}, unpriced_tokens=0,
             pct_5h=0.0, pct_7d=0.0, cov_pct_5h=0.0, cov_pct_7d=0.0, cost_all=0.0)
    return g


def epoch(ts: str) -> float:
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


class Windows:
    """Usage windows (5h / 7d) rebuilt from measurement points; value = highest pct seen."""

    def __init__(self, length: int) -> None:
        self.length = length
        self.ends: list[float] = []
        self.pct: list[float] = []

    def build(self, points: list[tuple[float, float]]) -> None:
        # points: (resets_at, pct); resets_at jitters by seconds within one window -> merge within 10 min
        merged: list[list[float]] = []
        for r, p in sorted(points):
            if merged and r - merged[-1][0] <= 600:
                merged[-1][1] = max(merged[-1][1], p)
                merged[-1][2] = r
            else:
                merged.append([r, p, r])
        self.ends = [m[2] for m in merged]
        self.pct = [m[1] for m in merged]

    def find(self, t: float) -> Optional[int]:
        i = bisect.bisect_right(self.ends, t)
        if i < len(self.ends) and self.ends[i] - self.length <= t:
            return i
        return None


def load_windows(data_dir: Path) -> tuple[Windows, Windows]:
    w5, w7 = Windows(5 * 3600), Windows(7 * 86400)
    p5: list[tuple[float, float]] = []
    p7: list[tuple[float, float]] = []
    path = data_dir / "samples" / "usage.csv"
    if path.exists():
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("stale") == "1":
                    continue
                try:
                    if r["five_hour_resets_at"] and r["five_hour_pct"]:
                        p5.append((float(r["five_hour_resets_at"]), float(r["five_hour_pct"])))
                    if r["seven_day_resets_at"] and r["seven_day_pct"]:
                        p7.append((float(r["seven_day_resets_at"]), float(r["seven_day_pct"])))
                except ValueError:
                    continue
    w5.build(p5)
    w7.build(p7)
    return w5, w7


def row_cost(row: dict[str, str], prices: dict[str, dict[str, float]]) -> float:
    price = prices.get(row["model"])
    if price is None:
        return 0.0
    return sum(to_int(row[c]) * float(price.get(c, 0)) for c in CATS) / 1_000_000


def aggregate(data_dir: Path, by: list[str], prices: dict[str, dict[str, float]],
              since: Optional[str], until: Optional[str], month: Optional[str],
              windows: bool = False
              ) -> tuple[dict[tuple[str, ...], dict[str, Any]], dict[str, int]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = defaultdict(new_group)
    unknown: dict[str, int] = defaultdict(int)
    files = sorted((data_dir / "ledger").glob("*.csv"))
    rows: list[tuple[dict[str, str], str, float, float]] = []
    for f in files:
        with f.open(newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append((row, local_day(row["ts"]), epoch(row["ts"]), row_cost(row, prices)))
    # window weight = API-price equivalent of all ledger rows in the window (all months, no filter)
    wins = load_windows(data_dir) if windows else None
    totals: list[dict[int, float]] = [defaultdict(float), defaultdict(float)]
    if wins:
        for _, _, t, cost in rows:
            for k, w in enumerate(wins):
                i = w.find(t)
                if i is not None:
                    totals[k][i] += cost
    for row, day, t, cost in rows:
        if month and not day.startswith(month):
            continue
        if (since and day < since) or (until and day > until):
            continue
        g = groups[group_key(row, day, by)]
        toks = {c: to_int(row[c]) for c in CATS}
        for c in CATS:
            g[c] += toks[c]
        g["thinking"] += to_int(row["thinking"])
        g["web_search"] += to_int(row["web_search"])
        g["web_fetch"] += to_int(row["web_fetch"])
        g["responses"] += 1
        if row["unattended"] == "1":
            g["unattended_responses"] += 1
        if wins:
            g["cost_all"] += cost
            for k, (name, w) in enumerate(zip(("pct_5h", "pct_7d"), wins)):
                i = w.find(t)
                if i is not None and totals[k][i] > 0:
                    g[name] += w.pct[i] * cost / totals[k][i]
                    g["cov_" + name] += cost
        price = prices.get(row["model"])
        if price is None:
            n = sum(toks.values())
            g["unpriced_tokens"] += n
            unknown[row["model"]] += n
            continue
        for c in CATS:
            g["usd"][c] += toks[c] * float(price.get(c, 0)) / 1_000_000
    return groups, unknown


def flat(by: list[str], key: tuple[str, ...], g: dict[str, Any], windows: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = dict(zip(by, key))
    for c in CATS:
        d[c] = g[c]
    d.update(thinking=g["thinking"], responses=g["responses"],
             unattended_share=round(g["unattended_responses"] / g["responses"], 4) if g["responses"] else 0,
             web_search=g["web_search"], web_fetch=g["web_fetch"], unpriced_tokens=g["unpriced_tokens"])
    for c in CATS:
        d[f"usd_{c}"] = round(g["usd"][c], 4)
    d["usd_total"] = round(sum(g["usd"].values()), 4)
    if windows:
        for n in ("pct_5h", "pct_7d"):
            cov = g["cov_" + n]
            # n/v when no measurement point covers any of the group's priced usage
            d[n] = round(g[n], 2) if cov > 0 else None
            d["cov_" + n] = round(cov / g["cost_all"], 3) if g["cost_all"] > 0 else 0
    return d


def render_text(by: list[str], rows: list[dict[str, Any]], unknown: dict[str, int],
                windows: bool = False) -> str:
    cols = by + ["responses", "input", "cache_write_5m", "cache_write_1h", "cache_read", "output",
                 "thinking", "unattended_share", "usd_total"]
    if windows:
        cols += ["pct_5h", "pct_7d"]
    table = [cols] + [["n/v" if r[c] is None else f"{r[c]:,.2f}" if c in ("usd_total", "pct_5h", "pct_7d") else f"{r[c]:.0%}" if c == "unattended_share"
                       else f"{r[c]:,}" if isinstance(r[c], int) else str(r[c]) for c in cols] for r in rows]
    widths = [max(len(t[i]) for t in table) for i in range(len(cols))]
    lines = ["  ".join(c.rjust(w) if i >= len(by) else c.ljust(w)
                        for i, (c, w) in enumerate(zip(t, widths))) for t in table]
    lines.append("")
    lines.append("USD = API list-price equivalent (not the subscription bill); web_search/web_fetch counted, not priced.")
    if windows:
        lines.append("pct_5h/pct_7d = estimated account-window points: window max % (samples/usage.csv) split by "
                     "API-price-equivalent weight of the responses in the same window; n/v = no measurement point; "
                     "percentages are rounded and parallel projects share a window.")
    if unknown:
        lines.append("Unpriced model IDs (cost counted as 0): " +
                     ", ".join(f"{m} ({n:,} tokens)" for m, n in sorted(unknown.items())))
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Token and API-price-equivalent report from the ledger (0 tokens).")
    ap.add_argument("--data-dir", help="ledger data repo (default: config data_dir)")
    ap.add_argument("--config", default=str(CONFIG_PATH), help=f"config file (default {CONFIG_PATH})")
    ap.add_argument("--prices", help="price list JSON (default: config 'prices', ~/.config/usage-ledger/prices.json, repo prices.json)")
    ap.add_argument("--by", nargs="+", choices=BY_CHOICES, default=["project"], help="grouping (default project)")
    ap.add_argument("--since", help="local day >= YYYY-MM-DD")
    ap.add_argument("--until", help="local day <= YYYY-MM-DD")
    ap.add_argument("--month", help="only YYYY-MM")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--csv", action="store_true", help="CSV output")
    fmt.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--windows", action="store_true",
                    help="add estimated 5h/7d account-window shares from <data_dir>/samples/usage.csv")
    ap.add_argument("--out", help="write to file instead of stdout (default report dir: <data_dir>/reports/)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        cfgp = Path(args.config).expanduser()
        cfg = load_json(cfgp) if cfgp.exists() else {}
        data = args.data_dir or cfg.get("data_dir")
        if not data:
            raise ReportError("data_dir missing (--data-dir or config.json)")
        data_dir = Path(data).expanduser()
        if not (data_dir / "ledger").is_dir():
            raise ReportError(f"no ledger directory in {data_dir}")
        prices = load_prices(cfg, args.prices)
        for d in (args.since, args.until):
            if d:
                dt.date.fromisoformat(d)
        if args.month:
            dt.datetime.strptime(args.month, "%Y-%m")
    except (ReportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    by = list(dict.fromkeys(args.by))
    groups, unknown = aggregate(data_dir, by, prices, args.since, args.until, args.month, args.windows)
    rows = sorted((flat(by, k, g, args.windows) for k, g in groups.items()), key=lambda r: -r["usd_total"])
    if args.json:
        text = json.dumps({"by": by, "rows": rows, "unpriced_models": unknown}, indent=2) + "\n"
    elif args.csv:
        import io
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        text = buf.getvalue()
    else:
        text = render_text(by, rows, unknown, args.windows)
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
