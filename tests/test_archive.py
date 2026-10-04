import json
import shutil
import subprocess

import pytest

import ledger_archive


def mkenv(tmp_path):
    proj = tmp_path / "logs"
    for rel, txt in (("enc-dir/sess1.jsonl", '{"a":1}\n'),
                     ("enc-dir/sess1/subagents/agent-x.jsonl", '{"b":1}\n'),
                     ("enc-dir/notes.txt", "skip me")):
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt)
    return proj, tmp_path / "data"


def run(proj, data, tmp_path, *extra):
    return ledger_archive.main(["--projects-dir", str(proj), "--data-dir", str(data),
                                "--config", str(tmp_path / "none.json"), *extra])


def test_build_command_no_delete_batchmode_key():
    cmd = ledger_archive.build_command(ledger_archive.Path("/src"), "u@h:/dst", "/k/key", 30)
    assert cmd[0] == "rsync" and "-a" in cmd and "-z" in cmd and "--partial" in cmd
    assert not any(c.startswith("--delete") for c in cmd)
    assert "--timeout=30" in cmd
    ssh = cmd[cmd.index("-e") + 1]
    assert "BatchMode=yes" in ssh and "-i /k/key" in ssh
    assert "--include=*.jsonl" in cmd and "--exclude=*" in cmd
    assert cmd[-2:] == ["/src/", "u@h:/dst/"]


def test_missing_target_skips_and_logs_once_per_day(tmp_path, capsys):
    proj, data = mkenv(tmp_path)
    assert run(proj, data, tmp_path) == 0
    assert run(proj, data, tmp_path) == 0
    log = (data / "logs" / "collect.log").read_text().splitlines()
    assert len([ln for ln in log if "archive_target not set" in ln]) == 1
    assert (data / "state" / "archive_missing_since").exists()


def test_failure_logged_exit_1(tmp_path, monkeypatch, capsys):
    proj, data = mkenv(tmp_path)
    monkeypatch.setattr(ledger_archive, "run_rsync", lambda cmd, t: (255, "ssh: unreachable"))
    assert run(proj, data, tmp_path, "--target", "u@h:/dst") == 1
    assert "ERROR archive: rsync exit 255" in (data / "logs" / "collect.log").read_text()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync missing")
def test_local_roundtrip_only_jsonl_no_delete(tmp_path):
    proj, data = mkenv(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "old-kept.jsonl").write_text("older than local retention\n")
    assert run(proj, data, tmp_path, "--target", str(dst)) == 0
    assert (dst / "enc-dir" / "sess1.jsonl").read_text() == '{"a":1}\n'
    assert (dst / "enc-dir/sess1/subagents/agent-x.jsonl").exists()
    assert not (dst / "enc-dir" / "notes.txt").exists()
    assert (dst / "old-kept.jsonl").exists()
    (proj / "enc-dir" / "sess1.jsonl").unlink()
    assert run(proj, data, tmp_path, "--target", str(dst)) == 0
    assert (dst / "enc-dir" / "sess1.jsonl").exists()  # never deleted at the target
    ok = json.loads((data / "state" / "archive_last_ok.json").read_text())
    assert ok["clean"] is True
    assert not (data / "state" / "archive_missing_since").exists()
