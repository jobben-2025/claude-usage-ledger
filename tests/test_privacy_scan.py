"""privacy_scan tests — patterns are assembled at runtime so this file itself stays clean."""
import subprocess
from pathlib import Path

import pytest

from tools import privacy_scan as ps

U = "/" + "Users/alice"
CASES = {
    "user-path": f"cache at {U}/x",
    "email": "mail " + "bob" + "@" + "corp.test",
    "ipv4": "host 10." + "20.30.40",
    "project-name": "see PRJ-" + "Secret_Thing",
    "token": "key=" + "sk-" + "a" * 24,
    "long-blob": "blob " + "A1b2" * 14,
}


def run(tmp_path, files, extra=(), allow="", history=True):
    repo = tmp_path / "r"
    repo.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                    "commit", "-qm", "c"], check=True)
    a = tmp_path / "allow.txt"
    a.write_text(allow)
    x = tmp_path / "extra.txt"
    x.write_text("\n".join(extra))
    argv = ["--repo", str(repo), "--allow", str(a), "--extra-patterns", str(x)]
    return repo, argv + ([] if history else ["--no-history"])


@pytest.mark.parametrize("rule", list(CASES))
def test_positive_per_rule(tmp_path, capsys, rule):
    _, argv = run(tmp_path, {"a.txt": CASES[rule]}, history=False)
    assert ps.main(argv) == 1
    out = capsys.readouterr().out
    assert f"[{rule}]" in out and "a.txt:1" in out


def test_clean_and_masking(tmp_path, capsys):
    _, argv = run(tmp_path, {"a.txt": "nothing here, 127.0.0.1, PRJ-Usage-Ledger"}, history=False)
    assert ps.main(argv) == 0
    _, argv = run(tmp_path / "b", {"a.txt": CASES["email"]}, history=False)
    ps.main(argv)
    assert "corp.test" not in capsys.readouterr().out


def test_artifact(tmp_path, capsys):
    _, argv = run(tmp_path, {"ledger/2026-01.csv": "x", "s.jsonl": "{}"}, history=False)
    assert ps.main(argv) == 1
    assert capsys.readouterr().out.count("[artifact]") == 2


def test_extra_patterns(tmp_path, capsys):
    _, argv = run(tmp_path, {"a.txt": "acct zebra99"}, extra=["zebra99"], history=False)
    assert ps.main(argv) == 1


def test_allowlist_needs_reason_and_literal(tmp_path, capsys):
    lit = "PRJ-" + "Secret_Thing"
    _, argv = run(tmp_path, {"t/a.txt": CASES["project-name"]}, allow=f"t/* project-name ={lit}  # synthetic\n",
                  history=False)
    assert ps.main(argv) == 0
    _, argv = run(tmp_path / "b", {"t/a.txt": CASES["project-name"]}, allow="t/* project-name\n", history=False)
    assert ps.main(argv) == 2
    _, argv = run(tmp_path / "c", {"a": "x"}, allow="* * # blanket\n", history=False)
    assert ps.main(argv) == 2


def test_history_finds_removed_leak(tmp_path, capsys):
    repo, argv = run(tmp_path, {"a.txt": CASES["email"]})
    (repo / "a.txt").write_text("clean\n")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                    "commit", "-qam", "fix"], check=True)
    assert ps.main(argv + ["--no-history"]) == 0
    assert ps.main(argv) == 1
    assert "[email]" in capsys.readouterr().out


def test_usage_error(tmp_path):
    assert ps.main(["--repo", str(tmp_path)]) == 2
