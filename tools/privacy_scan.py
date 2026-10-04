#!/usr/bin/env python3
"""Scan tracked files and the full git history for private data before a public push.

Pure script, no model calls. Generic leak patterns live here; private literals (account names, LAN
addresses, mail addresses) are NOT stored in the repo — pass them via --extra-patterns FILE
(default: ~/.config/usage-ledger/privacy_extra.txt, one literal or `re:<regex>` per line, `#` comments).

Exit codes: 0 clean, 1 findings, 2 usage/configuration error.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ALLOW = Path(__file__).resolve().parent / "privacy_allow.txt"
DEFAULT_EXTRA = Path("~/.config/usage-ledger/privacy_extra.txt")
OWN_PROJECTS = ("PRJ-Usage-Ledger", "PRJ-Usage-Ledger-Data")
ARTIFACT_GLOBS = ("*.jsonl", "*.gz", "*.sqlite", "*.sqlite-*", "*.log", "ledger/*", "samples/*",
                  "archive/*", "reports/*", "data/*")
BENIGN_IPS = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}

RULES: dict[str, re.Pattern[str]] = {
    "user-path": re.compile(r"(?:/Users/|/home/)[A-Za-z0-9._-]+|[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "ipv4": re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"),
    "project-name": re.compile(r"PRJ-[A-Za-z0-9_][A-Za-z0-9_-]*"),
    "token": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bghp_[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}\b"
                        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "long-blob": re.compile(r"(?<![A-Za-z0-9+/=_-])(?:[A-Fa-f0-9]{40,}|[A-Za-z0-9+/]{48,}={0,2})"
                            r"(?![A-Za-z0-9+/=_-])"),
}


class ScanError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    where: str       # "path:line" or "commit:path:line"
    rule: str
    excerpt: str     # masked

    def render(self) -> str:
        return f"{self.where}  [{self.rule}]  {self.excerpt}"


@dataclass(frozen=True)
class AllowEntry:
    glob: str
    rule: str
    literal: Optional[str]


def mask(text: str) -> str:
    return text[:2] + "*" * min(max(len(text) - 2, 3), 8)


def load_allow(path: Path) -> list[AllowEntry]:
    """Line format: `<path-glob> <rule> [=<literal>]  # reason` — the reason is mandatory."""
    entries: list[AllowEntry] = []
    if not path.exists():
        return entries
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        body, _, reason = raw.partition("#")
        parts = body.split()
        if not reason.strip() or len(parts) not in (2, 3):
            raise ScanError(f"{path}:{n}: need '<glob> <rule> [=<literal>]  # reason'")
        glob, rule = parts[0], parts[1]
        if rule != "artifact" and rule not in RULES and not rule.startswith("extra"):
            raise ScanError(f"{path}:{n}: unknown rule {rule!r} (no blanket '*' allowed)")
        literal = parts[2][1:] if len(parts) == 3 and parts[2].startswith("=") else None
        if len(parts) == 3 and literal is None:
            raise ScanError(f"{path}:{n}: third field must be =<literal>")
        entries.append(AllowEntry(glob, rule, literal))
    return entries


def load_extra(path: Optional[Path]) -> dict[str, re.Pattern[str]]:
    extra: dict[str, re.Pattern[str]] = {}
    if path is None or not path.expanduser().exists():
        return extra
    for n, raw in enumerate(path.expanduser().read_text().splitlines(), 1):
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        try:
            pat = re.compile(item[3:] if item.startswith("re:") else re.escape(item))
        except re.error as exc:
            raise ScanError(f"{path}:{n}: bad regex: {exc}") from exc
        extra[f"extra-{n}"] = pat
    return extra


def allowed(entries: list[AllowEntry], file: str, rule: str, hit: str) -> bool:
    for e in entries:
        if not fnmatch.fnmatch(file, e.glob):
            continue
        if e.rule == rule or (e.rule == "extra" and rule.startswith("extra-")):
            if e.literal is None or e.literal == hit:
                return True
    return False


def scan_line(line: str, rules: dict[str, re.Pattern[str]], own: Iterable[str]) -> Iterator[tuple[str, str]]:
    own_set = set(own)
    for rule, pat in rules.items():
        for m in pat.finditer(line):
            hit = m.group(0)
            if rule == "project-name" and hit in own_set:
                continue
            if rule == "ipv4" and hit in BENIGN_IPS:
                continue
            yield rule, hit


def scan_text(file: str, text: str, rules: dict[str, re.Pattern[str]], allow: list[AllowEntry],
              own: Iterable[str], prefix: str = "") -> list[Finding]:
    out: list[Finding] = []
    for n, line in enumerate(text.splitlines(), 1):
        for rule, hit in scan_line(line, rules, own):
            if not allowed(allow, file, rule, hit):
                out.append(Finding(f"{prefix}{file}:{n}", rule, mask(hit)))
    return out


def scan_path_artifact(file: str, allow: list[AllowEntry], prefix: str = "") -> Optional[Finding]:
    if any(fnmatch.fnmatch(file, g) for g in ARTIFACT_GLOBS) and not allowed(allow, file, "artifact", file):
        return Finding(f"{prefix}{file}", "artifact", "tracked data/log artifact")
    return None


def git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ScanError(f"git {' '.join(args)} failed: {exc}") from exc


def scan_worktree(repo: Path, rules, allow, own) -> list[Finding]:
    out: list[Finding] = []
    for rel in git(repo, "ls-files", "-z").decode().split("\0"):
        if not rel:
            continue
        art = scan_path_artifact(rel, allow)
        if art:
            out.append(art)
        p = repo / rel
        if p.is_file():
            data = p.read_bytes()
            if b"\0" in data[:4096]:
                continue
            out += scan_text(rel, data.decode("utf-8", "replace"), rules, allow, own)
    return out


def scan_ref_tree(repo: Path, ref: str, rules, allow, own) -> list[Finding]:
    """Files of the tree at `ref` (instead of the checked-out working tree)."""
    out: list[Finding] = []
    for rel in git(repo, "ls-tree", "-r", "--name-only", "-z", ref).decode().split("\0"):
        if not rel:
            continue
        art = scan_path_artifact(rel, allow)
        if art:
            out.append(art)
        data = git(repo, "show", f"{ref}:{rel}")
        if b"\0" in data[:4096]:
            continue
        out += scan_text(rel, data.decode("utf-8", "replace"), rules, allow, own)
    return out


def scan_history(repo: Path, rules, allow, own, ref: str = "--all") -> list[Finding]:
    """Added lines of every commit on every ref, or only of `ref` (a once-committed leak stays in history)."""
    log = git(repo, "log", ref, "--no-color", "-U0", "--format=@@COMMIT %h", "--no-renames",
              "-p").decode("utf-8", "replace")
    out: list[Finding] = []
    commit, file, seen = "", "", set()
    for raw in log.splitlines():
        if raw.startswith("@@COMMIT "):
            commit, file = raw[9:], ""
        elif raw.startswith("+++ "):
            file = raw[6:] if raw.startswith("+++ b/") else ""
            if file:
                art = scan_path_artifact(file, allow, f"{commit}:")
                if art and (commit, file) not in seen:
                    seen.add((commit, file))
                    out.append(art)
        elif raw.startswith("+") and file:
            out += scan_text(file, raw[1:], rules, allow, own, prefix=f"{commit}:")
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=REPO, help="git repository to scan (default: this repo)")
    ap.add_argument("--allow", type=Path, default=DEFAULT_ALLOW, help="allowlist file")
    ap.add_argument("--extra-patterns", type=Path, default=DEFAULT_EXTRA,
                    help="private literals / re:<regex> outside the repo")
    ap.add_argument("--own-projects", nargs="*", default=list(OWN_PROJECTS),
                    help="project names that are fine to mention")
    ap.add_argument("--ref", help="scan this ref (tree + its history only) instead of the working tree "
                    "and all refs")
    ap.add_argument("--no-history", action="store_true", help="scan only the tracked working tree")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        allow = load_allow(args.allow)
        rules = {**RULES, **load_extra(args.extra_patterns)}
        if not (args.repo / ".git").exists():
            raise ScanError(f"{args.repo} is not a git repository")
        if args.ref:
            found = scan_ref_tree(args.repo, args.ref, rules, allow, args.own_projects)
        else:
            found = scan_worktree(args.repo, rules, allow, args.own_projects)
        if not args.no_history:
            found += scan_history(args.repo, rules, allow, args.own_projects, args.ref or "--all")
    except ScanError as exc:
        print(f"privacy_scan: {exc}", file=sys.stderr)
        return 2
    for f in found:
        print(f.render())
    print(f"privacy_scan: {len(found)} finding(s)" + ("" if args.no_history else " (worktree + history)"),
          file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
