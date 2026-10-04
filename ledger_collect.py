#!/usr/bin/env python3
"""Collect Claude Code token usage from session logs into a per-month CSV ledger.

Pure script, no model calls. Rules: docs/FORMAT.md.
Exit codes: 0 ok, 1 partial failure (logged), 2 configuration error.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator, Optional

CONFIG_PATH = Path("~/.config/usage-ledger/config.json")
COLUMNS = [
    "ts", "project", "session_id", "agent", "model", "input", "cache_write_5m",
    "cache_write_1h", "cache_read", "output", "thinking", "web_search", "web_fetch",
    "unattended", "source_file_hash", "msg_id", "request_id",
]
KEY_COLS = ("msg_id", "request_id")


class ConfigError(Exception):
    pass


def load_config(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config {path} must be a JSON object")
    return data


def encode_dir(p: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in p)


class ProjectMapper:
    def __init__(self, roots: list[Path], home: Path) -> None:
        self.roots = [r.resolve() if r.exists() else r for r in roots]
        self.home = home
        self._encoded: Optional[list[tuple[str, str]]] = None

    def from_cwd(self, cwd: str) -> str:
        p = Path(cwd)
        for root in self.roots:
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if rel.parts:
                return rel.parts[0]
        try:
            rel = p.relative_to(self.home)
        except ValueError:
            return "(other)"
        parts = rel.parts
        if not parts:
            return "(home)"
        if rel in [r.relative_to(self.home) for r in self.roots if r.is_relative_to(self.home)]:
            return "(projects-root)"
        if parts[0] == ".claude":
            return "(claude-config)"
        if parts[0] in ("Desktop", ".cache") and len(parts) > 1:
            return parts[1] if parts[0] == "Desktop" else f".cache/{parts[1]}"
        return parts[0]

    def from_encoded(self, enc: str) -> str:
        if self._encoded is None:
            self._encoded = []
            for root in self.roots:
                if root.is_dir():
                    for d in root.iterdir():
                        if d.is_dir():
                            self._encoded.append((encode_dir(str(d)), d.name))
            self._encoded.sort(key=lambda t: -len(t[0]))
        for prefix, name in self._encoded:
            if enc == prefix or enc.startswith(prefix + "-"):
                return name
        return "(unknown)"


def iter_log_files(projects_dir: Path) -> Iterator[Path]:
    if not projects_dir.is_dir():
        return
    yield from sorted(projects_dir.glob("*/*.jsonl"))
    yield from sorted(projects_dir.glob("*/*/subagents/agent-*.jsonl"))


def to_int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def row_from_line(obj: dict[str, Any], path: Path, projects_dir: Path,
                  mapper: ProjectMapper) -> Optional[dict[str, str]]:
    if obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
        return None
    if msg.get("model") == "<synthetic>" or obj.get("isApiErrorMessage"):
        return None
    u = msg["usage"]
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    w5, w1 = to_int(cc.get("ephemeral_5m_input_tokens")), to_int(cc.get("ephemeral_1h_input_tokens"))
    total_w = to_int(u.get("cache_creation_input_tokens"))
    if not cc and total_w:
        w5 = total_w
    td = u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {}
    stu = u.get("server_tool_use") if isinstance(u.get("server_tool_use"), dict) else {}
    cwd = obj.get("cwd")
    if isinstance(cwd, str) and cwd:
        project = mapper.from_cwd(cwd)
    else:
        project = mapper.from_encoded(path.relative_to(projects_dir).parts[0])
    agent = "main"
    if "subagents" in path.parts or obj.get("agentId"):
        agent = str(obj.get("agentId") or path.stem.removeprefix("agent-"))
    ent = obj.get("entrypoint")
    unattended = "?" if not ent else ("1" if ent == "sdk-cli" else "0")
    return {
        "ts": str(obj.get("timestamp") or ""),
        "project": project,
        "session_id": str(obj.get("sessionId") or ""),
        "agent": agent,
        "model": str(msg.get("model") or ""),
        "input": str(to_int(u.get("input_tokens"))),
        "cache_write_5m": str(w5),
        "cache_write_1h": str(w1),
        "cache_read": str(to_int(u.get("cache_read_input_tokens"))),
        "output": str(to_int(u.get("output_tokens"))),
        "thinking": str(to_int(td.get("thinking_tokens"))),
        "web_search": str(to_int(stu.get("web_search_requests"))),
        "web_fetch": str(to_int(stu.get("web_fetch_requests"))),
        "unattended": unattended,
        "source_file_hash": hashlib.sha1(str(path.relative_to(projects_dir)).encode()).hexdigest()[:10],
        "msg_id": str(msg.get("id") or ""),
        "request_id": str(obj.get("requestId") or ""),
    }


def row_key(r: dict[str, str]) -> tuple[str, ...]:
    if r["msg_id"] and r["request_id"]:
        return (r["msg_id"], r["request_id"])
    return (r["source_file_hash"], r["ts"], r["session_id"], r["agent"], r["output"], r["input"])


def better(new: dict[str, str], old: dict[str, str]) -> bool:
    return int(new["output"]) > int(old["output"])


def read_new_lines(path: Path, offset: int) -> tuple[list[bytes], int]:
    """Complete lines after offset; the offset returned stops after the last newline."""
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    return data[: end + 1].splitlines(), offset + end + 1


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_month(path: Path) -> dict[tuple[str, ...], dict[str, str]]:
    out: dict[tuple[str, ...], dict[str, str]] = {}
    if path.exists():
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                r = {c: r.get(c) or "" for c in COLUMNS}
                out[row_key(r)] = r
    return out


def dump_month(rows: dict[tuple[str, ...], dict[str, str]]) -> str:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in sorted(rows.values(), key=lambda r: (r["ts"], r["request_id"])):
        w.writerow(r)
    return buf.getvalue()


def log(data_dir: Path, msg: str) -> None:
    try:
        p = data_dir / "logs" / "collect.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


def collect(projects_dir: Path, data_dir: Path, roots: list[Path], since: Optional[str],
            dry_run: bool, home: Path) -> tuple[int, dict[str, dict[str, int]], int, int]:
    """Return (exit_code, per-project sums, files_seen, new_rows)."""
    mapper = ProjectMapper(roots, home)
    state_path = data_dir / "state" / "offsets.json"
    state: dict[str, dict[str, int]] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except ValueError:
            state = {}
    new_state = dict(state)
    pending: dict[str, dict[tuple[str, ...], dict[str, str]]] = defaultdict(dict)
    errors = 0
    files = 0
    for path in iter_log_files(projects_dir):
        rel = str(path.relative_to(projects_dir))
        try:
            st = path.stat()
            old = state.get(rel, {})
            off = old.get("offset", 0)
            if old.get("inode") != st.st_ino or st.st_size < off:
                off = 0
            if off == st.st_size and old.get("inode") == st.st_ino:
                continue
            files += 1
            lines, new_off = read_new_lines(path, off)
        except OSError as exc:
            errors += 1
            log(data_dir, f"ERROR {rel}: {exc}")
            continue
        for raw in lines:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            row = row_from_line(obj, path, projects_dir, mapper)
            if row is None or (since and row["ts"][:10] < since):
                continue
            month = row["ts"][:7] or "unknown"
            k = row_key(row)
            cur = pending[month].get(k)
            if cur is None or better(row, cur):
                pending[month][k] = row
        new_state[rel] = {"offset": new_off, "inode": st.st_ino, "size": st.st_size}

    sums: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    new_rows = 0
    writes: list[tuple[Path, str]] = []
    for month, rows in sorted(pending.items()):
        mpath = data_dir / "ledger" / f"{month}.csv"
        existing = load_month(mpath)
        changed = False
        for k, r in rows.items():
            cur = existing.get(k)
            if cur is None:
                existing[k] = r
                new_rows += 1
                changed = True
            elif better(r, cur):
                existing[k] = r
                changed = True
        if changed:
            writes.append((mpath, dump_month(existing)))
        for r in rows.values():
            s = sums[r["project"]]
            s["responses"] += 1
            for c in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output"):
                s[c] += int(r[c])
    if not dry_run:
        for mpath, text in writes:
            atomic_write(mpath, text)
        atomic_write(state_path, json.dumps(new_state, indent=1, sort_keys=True))
        log(data_dir, f"files={files} new_rows={new_rows} errors={errors}")
    return (1 if errors else 0), {p: dict(s) for p, s in sums.items()}, files, new_rows


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Collect Claude Code token usage into a CSV ledger (0 tokens).")
    ap.add_argument("--projects-dir", help="Claude Code log dir (default ~/.claude/projects)")
    ap.add_argument("--data-dir", help="ledger data repo (default: config data_dir)")
    ap.add_argument("--roots", nargs="+", help="dirs containing project folders (default: config roots)")
    ap.add_argument("--config", default=str(CONFIG_PATH), help=f"config file (default {CONFIG_PATH})")
    ap.add_argument("--since", help="only responses on/after YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="write nothing; print per-project sums")
    ap.add_argument("--home", help=argparse.SUPPRESS)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(Path(args.config))
        data = args.data_dir or cfg.get("data_dir")
        if not data:
            raise ConfigError("data_dir missing (--data-dir or config.json)")
        projects_dir = Path(args.projects_dir or cfg.get("projects_dir") or "~/.claude/projects").expanduser()
        roots = [Path(r).expanduser() for r in (args.roots or cfg.get("roots") or ["~/PROJECTS"])]
        if args.since:
            dt.date.fromisoformat(args.since)
    except (ConfigError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    data_dir = Path(data).expanduser()
    home = Path(args.home).expanduser() if args.home else Path.home()
    lock_path = data_dir / "state" / "lock"
    if not args.dry_run:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = None
    try:
        if not args.dry_run:
            lock_fh = lock_path.open("w")
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
        code, sums, files, new_rows = collect(projects_dir, data_dir, roots, args.since, args.dry_run, home)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        log(data_dir, f"ERROR {exc}")
        return 1
    finally:
        if lock_fh:
            lock_fh.close()
    if args.dry_run:
        for p, s in sorted(sums.items(), key=lambda kv: -kv[1]["output"]):
            print(f"{p}\tresponses={s['responses']} in={s['input']} cw5m={s['cache_write_5m']} "
                  f"cw1h={s['cache_write_1h']} cr={s['cache_read']} out={s['output']}")
    else:
        print(f"files={files} new_rows={new_rows}")
    return code


if __name__ == "__main__":
    sys.exit(main())
