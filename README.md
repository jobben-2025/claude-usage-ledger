# usage-ledger

Per-project token usage from Claude Code logs (`~/.claude/projects/**/*.jsonl`) — **plain scripts, zero
model tokens**: no model call in the collector, hook, scheduled job or report.

Every assistant response appears in exactly one session log, so usage is attributed per project from the
log itself (never from differences of account percentages) and parallel sessions cannot count each other.

## Data flow

Logs → `ledger_collect.py` (incremental, de-duplicated by `(message.id, requestId)`, project derived from
each line's `cwd`) → `ledger/YYYY-MM.csv` in a **separate private data repository** → daily commit + push
to your private mirror. Column layout and measurement notes: [`docs/FORMAT.md`](docs/FORMAT.md)
(German).

## Install

Requires Python 3 (stdlib only) and git. macOS launchd is used for scheduling.

1. Config: copy `config.example.json` to `~/.config/usage-ledger/config.json`; set `data_dir` (your private
   data repo) and `roots` (folders that contain your project folders).
2. Hooks in `~/.claude/settings.json`: `bash <repo>/hooks/session_end.sh` under `hooks.SessionEnd` (starts the
   collector detached, silent, exit 0) and `bash <repo>/hooks/session_start_health.sh` under
   `hooks.SessionStart` (prints one line only when something is broken).
3. Scheduling: `launchd/install.sh` renders the plist templates for this machine and loads the jobs (daily
   collect, commit and push; 15-minute sampler).
4. Push guard: `tools/install-hooks.sh` symlinks `tools/hooks/pre-push` into `.git/hooks` (idempotent, never
   overwrites a foreign hook). Re-run after every fresh clone — git does not clone hooks.

## Usage

- `python3 ledger_collect.py --help` — `--dry-run` writes nothing and prints totals per project.
- `python3 ledger_report.py [--by project model day unattended] [--month YYYY-MM | --since/--until]
  [--csv|--json] [--out PATH] [--windows]` — tokens per category and the **API list-price equivalent in USD**
  (not a subscription cost). Prices come from `prices.json` (override via `--prices`, the `prices` key in
  `config.json`, or `~/.config/usage-ledger/prices.json`); unknown model IDs are listed and counted at 0 USD.
  `--windows` adds `pct_5h`/`pct_7d`, an approximation: the per-window maximum of the samples, distributed by
  list-price equivalent; `n/a` without a sample.
- `python3 usage_sample.py` — appends one account-usage sample to `<data_dir>/samples/usage.csv` (every
  15 minutes via launchd); an unreadable source is skipped silently.
- `python3 ledger_archive.py` — `rsync -a -z --partial` of the raw `*.jsonl` logs (including `subagents/`)
  to `archive_target` from `config.json`; never `--delete`, BatchMode SSH, time limit, failures go to
  `<data_dir>/logs/collect.log` (exit 1, collecting is unaffected). Without `archive_target` the step is
  skipped and logged once per day; the SessionStart health check complains after 7 days. `--print-command`
  shows the exact rsync call.

### Archive target (rsync)

Raw logs only grow at the end, so rsync sends just the increment; leave compression to the target
(e.g. a ZFS dataset with zstd). Claude Code deletes local logs after `cleanupPeriodDays` (recommended: 180);
the target keeps everything because nothing is ever deleted there.

```sh
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_usage_archive -N ''
ssh-copy-id -i ~/.ssh/id_ed25519_usage_archive user@host
ssh -o BatchMode=yes -i ~/.ssh/id_ed25519_usage_archive user@host true   # must exit 0 without a prompt
```

Then add to `~/.config/usage-ledger/config.json`:
`"archive_target": "user@host:/path/to/archive/"`, `"archive_ssh_key": "~/.ssh/id_ed25519_usage_archive"`,
optionally `"archive_timeout": 900` (seconds).

Exit codes: 0 ok, 1 partial failure (see log), 2 usage/configuration error.
Tests: `python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/python -m pytest -q`
(synthetic fixtures only).

## Privacy separation

This repository holds code, documentation and tests with synthetic fixtures only — never real logs, ledger
data, project names, absolute paths, user names, e-mail addresses, IPs or tokens. All data lives in the
private data repository, which must never get a public remote (the health check reports any remote other
than your private mirror).

Two safeguards enforce this:

- `tools/privacy_scan.py [--no-history]` scans tracked files and the whole git history for user paths,
  e-mail addresses, IPv4 addresses, `PRJ-*`-style project names, token/key patterns, long encoded blobs and
  tracked data artifacts (exit 0 clean, 1 findings, 2 error). Your own private literals (account names,
  LAN addresses) are read from `~/.config/usage-ledger/privacy_extra.txt` (`--extra-patterns`), not stored in
  the repo. Exceptions go into `tools/privacy_allow.txt`, each with a mandatory reason.
- The `pre-push` hook always lets the private `gitea` remote (local backup) through without scanning,
  blocks every other remote until a deliberate approval (`.publish-approved` in the repo root, or
  `USAGE_LEDGER_PUBLISH_OK=1`), then accepts only the `release` branch and aborts the push while the scan
  of `release` (tree and full history) reports findings.

Before publishing, run `tools/privacy_scan.py` and review the output; the guard is a local safety net, not
server-side protection.

## What is published

Only the orphan branch `release` goes public (`tools/make_release.sh` builds it from `main` as a single
commit; it refuses to overwrite an existing `release`). The builder uses an **allow-list**: code
(`ledger_*.py`, `usage_sample.py`), `docs/`, `hooks/`, `launchd/`, `tests/`, `tools/`, `README.md`, `LICENSE`,
`.gitignore` and the config/price templates. Everything else — private working files (`CLAUDE.md`,
`PHASES.md`, `PROGRESS.md`, `SUMMARY.md`, `USER-NOTIFY.md`, `TASK*.md`, `DELEGATIONS/`, `.claude/`) and any new
file nobody listed — stays private by default. The `pre-push` hook lets a public remote receive nothing but
`release` (never `main`, other branches, tags or deletions), refuses the push if any commit of `release`
contains a private path (working files, `.claude/`, logs, `*.jsonl`, ledger/archive data), and scans the
branch, including its history (`privacy_scan.py --ref release`). The `github` remote has a fixed push refspec
`release:main`, so a bare `git push github` cannot send anything else.

## License

[MIT](LICENSE) © 2026 Benjamin Becht.
