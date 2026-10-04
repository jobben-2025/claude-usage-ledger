#!/usr/bin/env python3
"""Archive raw Claude Code session logs to a remote target via rsync (no git, no deletes).

Pure script, no model calls. Sources are never modified or deleted; nothing is ever deleted at
the target (no --delete). Raw *.jsonl files only grow at the end, so rsync transfers just the
increment; compression is left to the target file system.
Exit codes: 0 ok (or target not configured), 1 archive failure (logged), 2 configuration error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

CONFIG_PATH = Path("~/.config/usage-ledger/config.json")
DEFAULT_TIMEOUT = 900  # seconds, whole rsync run


class ConfigError(Exception):
    pass


def build_command(projects_dir: Path, target: str, ssh_key: Optional[str] = None,
                  timeout: int = DEFAULT_TIMEOUT, dry_run: bool = False) -> list[str]:
    """rsync argv: raw *.jsonl (incl. subagents/) only, no --delete, BatchMode SSH."""
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    if ssh_key:
        ssh += ["-i", str(Path(ssh_key).expanduser()), "-o", "IdentitiesOnly=yes"]
    cmd = ["rsync", "-a", "-z", "--partial", "--prune-empty-dirs", f"--timeout={timeout}",
           "-e", shlex.join(ssh),
           "--include=*/", "--include=*.jsonl", "--exclude=*"]
    if dry_run:
        cmd += ["-n", "--itemize-changes"]
    return cmd + [str(projects_dir).rstrip("/") + "/", target.rstrip("/") + "/"]


def log(data_dir: Path, msg: str) -> None:
    try:
        p = data_dir / "logs" / "collect.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


def note_missing_target(data_dir: Path) -> None:
    """Log 'target missing' once per day; remember since when it is missing (health check)."""
    state = data_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    since = state / "archive_missing_since"
    if not since.exists():
        since.write_text(today + "\n")
    logged = state / "archive_missing_logged"
    if not logged.exists() or logged.read_text().strip() != today:
        log(data_dir, "archive: archive_target not set, step skipped")
        logged.write_text(today + "\n")


def run_rsync(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return res.returncode, (res.stderr or res.stdout).strip()[-500:]


def archive(projects_dir: Path, data_dir: Path, target: str, ssh_key: Optional[str],
            timeout: int) -> tuple[int, str]:
    """Transfer, then verify with a dry run: no pending changes means the target is complete."""
    rc, err = run_rsync(build_command(projects_dir, target, ssh_key, timeout), timeout)
    if rc != 0:
        return 1, f"rsync exit {rc}: {err}"
    cmd = build_command(projects_dir, target, ssh_key, timeout, dry_run=True)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"verify failed: {exc}"
    if res.returncode != 0:
        return 1, f"verify rsync exit {res.returncode}: {res.stderr.strip()[-300:]}"
    # Live session files may grow between the two passes; count only files that are new/changed.
    pending = [ln for ln in res.stdout.splitlines() if ln.startswith(">f")]
    files = sum(1 for _ in projects_dir.glob("**/*.jsonl"))
    state = data_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "archive_last_ok.json").write_text(json.dumps(
        {"at": dt.datetime.now().isoformat(timespec="seconds"), "files": files,
         "pending_after": len(pending), "clean": not pending}) + "\n")
    (state / "archive_missing_since").unlink(missing_ok=True)
    return 0, f"files={files} pending_after={len(pending)}"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="rsync raw Claude Code logs to a remote target (0 tokens).")
    ap.add_argument("--projects-dir", help="Claude Code log dir (default ~/.claude/projects)")
    ap.add_argument("--data-dir", help="ledger data repo (default: config data_dir)")
    ap.add_argument("--target", help="rsync target user@host:/path/ (default: config archive_target)")
    ap.add_argument("--ssh-key", help="SSH key (default: config archive_ssh_key)")
    ap.add_argument("--timeout", type=int, help=f"rsync timeout seconds (default {DEFAULT_TIMEOUT})")
    ap.add_argument("--config", default=str(CONFIG_PATH), help=f"config file (default {CONFIG_PATH})")
    ap.add_argument("--print-command", action="store_true", help="print the rsync command and exit")
    ap.add_argument("--quiet", action="store_true", help="no summary on stdout (daily job / hooks)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    lock_fh = None
    try:
        cfg: dict[str, Any] = {}
        cp = Path(args.config).expanduser()
        if cp.exists():
            try:
                cfg = json.loads(cp.read_text())
                if not isinstance(cfg, dict):
                    raise ValueError("not an object")
            except (OSError, ValueError) as exc:
                raise ConfigError(f"cannot read config {cp}: {exc}") from exc
        data = args.data_dir or cfg.get("data_dir")
        if not data:
            raise ConfigError("data_dir missing (--data-dir or config.json)")
        projects_dir = Path(args.projects_dir or cfg.get("projects_dir") or "~/.claude/projects").expanduser()
        if not projects_dir.is_dir():
            raise ConfigError(f"projects dir not found: {projects_dir}")
        data_dir = Path(data).expanduser()
        target = args.target or cfg.get("archive_target")
        ssh_key = args.ssh_key or cfg.get("archive_ssh_key")
        timeout = int(args.timeout or cfg.get("archive_timeout") or DEFAULT_TIMEOUT)
        if args.print_command:
            if not target:
                raise ConfigError("archive_target not set")
            print(shlex.join(build_command(projects_dir, target, ssh_key, timeout)))
            return 0
        (data_dir / "state").mkdir(parents=True, exist_ok=True)
        if not target:
            note_missing_target(data_dir)
            return 0
        lock_fh = (data_dir / "state" / "archive.lock").open("w")
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        code, msg = archive(projects_dir, data_dir, target, ssh_key, timeout)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock_fh:
            lock_fh.close()
    if code:
        log(data_dir, f"ERROR archive: {msg}")
        print(f"error: archive: {msg}", file=sys.stderr)
    else:
        log(data_dir, f"archive: ok {msg}")
        if not args.quiet:
            print(f"archive: {msg}")
    return code


if __name__ == "__main__":
    sys.exit(main())
