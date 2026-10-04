import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import line, read_rows, run, write


def log_path(env, enc="enc-a", sid="s1"):
    return env["projects"] / enc / f"{sid}.jsonl"


def test_dedup_keeps_max_output(env):
    cwd = str(env["root"] / "alpha")
    write(log_path(env), [line(cwd, "m1", "r1", out=5), line(cwd, "m1", "r1", out=40),
                          line(cwd, "m1", "r1", out=40), line(cwd, "m2", "r2", out=7)])
    assert run(env) == 0
    rows = {r["msg_id"]: r for r in read_rows(env)}
    assert len(rows) == 2 and rows["m1"]["output"] == "40"


def test_incremental_idempotent_and_update(env):
    cwd = str(env["root"] / "alpha")
    p = log_path(env)
    write(p, [line(cwd, "m1", "r1", out=5)])
    run(env)
    run(env)
    assert len(read_rows(env)) == 1
    write(p, [line(cwd, "m1", "r1", out=30), line(cwd, "m2", "r2")], mode="a")
    run(env)
    rows = {r["msg_id"]: r for r in read_rows(env)}
    assert len(rows) == 2 and rows["m1"]["output"] == "30"


def test_partial_last_line_waits(env):
    cwd = str(env["root"] / "alpha")
    p = log_path(env)
    write(p, [line(cwd, "m1", "r1")])
    with p.open("a") as fh:
        fh.write(line(cwd, "m2", "r2")[:30])
    run(env)
    assert len(read_rows(env)) == 1
    with p.open("a") as fh:
        fh.write(line(cwd, "m2", "r2")[30:] + "\n")
    run(env)
    assert len(read_rows(env)) == 2


def test_rotation_rereads_without_duplicates(env):
    cwd = str(env["root"] / "alpha")
    p = log_path(env)
    write(p, [line(cwd, "m1", "r1"), line(cwd, "m2", "r2")])
    run(env)
    write(p, [line(cwd, "m1", "r1")])  # truncated/rewritten shorter
    run(env)
    assert len(read_rows(env)) == 2


def test_project_mapping_subfolder_and_special(env):
    write(log_path(env), [
        line(str(env["root"] / "alpha" / "sub"), "m1", "r1"),
        line(str(env["home"]), "m2", "r2"),
        line(str(env["home"] / ".claude" / "x"), "m3", "r3"),
        line("/elsewhere", "m4", "r4"),
        line(str(env["root"]), "m5", "r5"),
    ])
    run(env)
    got = {r["msg_id"]: r["project"] for r in read_rows(env)}
    assert got == {"m1": "alpha", "m2": "(home)", "m3": "(claude-config)", "m4": "(other)",
                   "m5": "(projects-root)"}


def test_subagent_and_unattended(env):
    cwd = str(env["root"] / "alpha")
    write(env["projects"] / "enc-a" / "s1" / "subagents" / "agent-abc.jsonl",
          [line(cwd, "m1", "r1", agent_id="abc", entry="sdk-cli")])
    write(log_path(env), [line(cwd, "m2", "r2", entry="cli")])
    run(env)
    got = {r["msg_id"]: r for r in read_rows(env)}
    assert got["m1"]["agent"] == "abc" and got["m1"]["unattended"] == "1"
    assert got["m2"]["agent"] == "main" and got["m2"]["unattended"] == "0"


def test_parallel_projects_overlapping_timestamps(env):
    a, b = str(env["root"] / "alpha"), str(env["root"] / "beta")
    ts = "2026-01-05T10:00:00.000Z"
    write(log_path(env, "enc-a", "sa"), [line(a, "ma", "ra", ts=ts, sid="sa", out=11)])
    write(log_path(env, "enc-b", "sb"), [line(b, "mb", "rb", ts=ts, sid="sb", out=22)])
    run(env)
    got = {r["project"]: r["output"] for r in read_rows(env)}
    assert got == {"alpha": "11", "beta": "22"}


def test_synthetic_and_non_usage_skipped(env):
    cwd = str(env["root"] / "alpha")
    write(log_path(env), [line(cwd, "m1", "r1", model="<synthetic>"), '{"type":"user"}', "not json",
                          line(cwd, "m2", "r2")])
    run(env)
    assert [r["msg_id"] for r in read_rows(env)] == ["m2"]


def test_since_and_dry_run(env, capsys):
    cwd = str(env["root"] / "alpha")
    write(log_path(env), [line(cwd, "m1", "r1", ts="2026-01-01T00:00:00Z"),
                          line(cwd, "m2", "r2", ts="2026-02-01T00:00:00Z")])
    assert run(env, "--dry-run") == 0
    assert not env["data"].exists()
    assert "alpha\tresponses=2" in capsys.readouterr().out
    run(env, "--since", "2026-01-15")
    assert [r["msg_id"] for r in read_rows(env)] == ["m2"]


def test_encoded_dir_fallback(env):
    from ledger_collect import encode_dir
    enc = encode_dir(str((env["root"] / "beta").resolve())) + "-deeper"
    d = line("x", "m1", "r1").replace('"cwd": "x", ', "")
    write(env["projects"] / enc / "s1.jsonl", [d])
    run(env)
    assert read_rows(env)[0]["project"] == "beta"


def test_lock_blocks_parallel_run(env):
    (env["data"] / "state").mkdir(parents=True)
    fh = (env["data"] / "state" / "lock").open("w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    code = (
        "import sys; sys.path.insert(0, %r); import ledger_collect as c; "
        "sys.exit(c.main(['--projects-dir', %r, '--data-dir', %r, '--roots', %r, '--config', '/nonexistent']))"
        % (str(Path(__file__).resolve().parent.parent), str(env["projects"]), str(env["data"]), str(env["root"]))
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=1.5)
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
    assert proc.wait(timeout=10) == 0


def test_config_error_exit_2(env):
    import ledger_collect
    assert ledger_collect.main(["--projects-dir", str(env["projects"]), "--config", "/nonexistent"]) == 2


def test_help_exit_0():
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "ledger_collect.py"), "--help"])
    assert r.returncode == 0
