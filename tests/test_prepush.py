import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "tools" / "hooks" / "pre-push"
INSTALL = HOOK.parent.parent / "install-hooks.sh"


def load():
    loader = importlib.machinery.SourceFileLoader("prepush", str(HOOK))
    spec = importlib.util.spec_from_loader("prepush", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def mkrepo(tmp_path, text="clean"):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "a.txt").write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                    "commit", "-qm", "c"], check=True)
    return repo


SCAN = ["--extra-patterns", "/nonexistent", "--allow", "/nonexistent"]


def test_gitea_clean_allowed(tmp_path):
    ok, _ = load().check("gitea", "http://lan.invalid/x.git", mkrepo(tmp_path), {}, SCAN)
    assert ok


def test_github_blocked_without_marker_allowed_with(tmp_path):
    m, repo = load(), mkrepo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
    ok, msg = m.check("origin", "git@github.com:x/y.git", repo, {}, SCAN)
    assert not ok and "approval" in msg
    assert m.check("origin", "git@github.com:x/y.git", repo, {"USAGE_LEDGER_PUBLISH_OK": "1"}, SCAN)[0]
    (repo / ".publish-approved").write_text("")
    assert m.check("origin", "git@github.com:x/y.git", repo, {}, SCAN)[0]


def test_gitea_name_with_public_url_is_not_private(tmp_path):
    ok, _ = load().check("gitea", "git@github.com:x/y.git", mkrepo(tmp_path), {}, SCAN)
    assert not ok


def test_gitea_never_scanned_even_with_hit(tmp_path):
    repo = mkrepo(tmp_path, "mail " + "bob" + "@" + "corp.test")
    ok, _ = load().check("gitea", "http://lan.invalid/x.git", repo, {}, SCAN, [("refs/heads/main", "a" * 40)])
    assert ok


def test_github_main_blocked(tmp_path):
    m, repo = load(), mkrepo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
    ok, msg = m.check("github", "git@github.com:x/y.git", repo, {"USAGE_LEDGER_PUBLISH_OK": "1"}, SCAN,
                      [("refs/heads/main", "a" * 40)])
    assert not ok and "only the branch" in msg


def test_github_release_with_hit_blocked(tmp_path):
    m, repo = load(), mkrepo(tmp_path, "mail " + "bob" + "@" + "corp.test")
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
    ok, msg = m.check("github", "git@github.com:x/y.git", repo, {"USAGE_LEDGER_PUBLISH_OK": "1"}, SCAN,
                      [("refs/heads/release", "a" * 40)])
    assert not ok and "privacy_scan" in msg


def test_history_gate_only_for_public_remotes(tmp_path):
    repo = mkrepo(tmp_path, "mail " + "bob" + "@" + "corp.test")
    (repo / "a.txt").write_text("clean")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                    "commit", "-qam", "fix"], check=True)
    m = load()
    assert m.check("gitea", "http://lan.invalid/x.git", repo, {}, SCAN)[0]
    assert not m.check("origin", "git@github.com:x/y.git", repo, {"USAGE_LEDGER_PUBLISH_OK": "1"}, SCAN)[0]


def test_public_remote_only_release_branch(tmp_path):
    m, repo = load(), mkrepo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
    env, url, sha = {"USAGE_LEDGER_PUBLISH_OK": "1"}, "git@github.com:x/y.git", "a" * 40
    assert m.check("github", url, repo, env, SCAN, [("refs/heads/release", sha)])[0]
    for ref, s in [("refs/heads/main", sha), ("refs/heads/other", sha), ("refs/tags/v1", sha),
                   ("refs/heads/release", "0" * 40)]:
        ok, msg = m.check("github", url, repo, env, SCAN, [("refs/heads/release", sha), (ref, s)])
        assert not ok and "only the branch" in msg
    # the private remote is not restricted
    assert m.check("gitea", "http://lan.invalid/x.git", repo, {}, SCAN, [("refs/heads/main", sha)])[0]


def test_public_scan_covers_release_only(tmp_path):
    repo = mkrepo(tmp_path, "mail " + "bob" + "@" + "corp.test")  # leak on main history/tree
    git = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                                     *a], check=True, capture_output=True)
    git("checkout", "-q", "--orphan", "release")
    (repo / "a.txt").write_text("clean")
    git("add", "-A")
    git("commit", "-qm", "Initial public release")
    ok, _ = load().check("github", "git@github.com:x/y.git", repo, {"USAGE_LEDGER_PUBLISH_OK": "1"}, SCAN,
                         [("refs/heads/release", "a" * 40)])
    assert ok


def test_public_push_blocked_when_release_has_private_file(tmp_path):
    m = load()
    for n, f in enumerate(("PROGRESS.md", "TASK15.md", "DELEGATIONS/x.md", ".claude/settings.json",
                           "ledger/2026-10.csv")):
        (tmp_path / str(n)).mkdir()
        repo = mkrepo(tmp_path / str(n))
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("x")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                        "commit", "-qm", "r"], check=True)
        subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
        ok, msg = m.check("github", "git@github.com:x/y.git", repo, {"USAGE_LEDGER_PUBLISH_OK": "1"}, SCAN,
                          [("refs/heads/release", "a" * 40)])
        assert not ok and "private files" in msg and f in msg


def test_make_release_excludes_private_files(tmp_path):
    repo = mkrepo(tmp_path)
    (repo / "tools").mkdir()
    script = repo / "tools" / "make_release.sh"
    script.write_text((HOOK.parent.parent / "make_release.sh").read_text())
    for d in ("DELEGATIONS", ".claude", "docs"):
        (repo / d).mkdir()
    for f in ("DELEGATIONS/x.md", "TASK12.md", "PROGRESS.md", "CLAUDE.md", "README.md", ".claude/settings.json",
              "INST-TEST.md", "NOTES.md", "docs/FORMAT.md", "ledger_collect.py"):
        (repo / f).write_text("x")
    git = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                                     *a], check=True, capture_output=True, text=True).stdout
    git("add", "-A")
    git("commit", "-qm", "more")
    run = lambda: subprocess.run(["bash", str(script), "HEAD"], capture_output=True, text=True)
    assert run().returncode == 0
    files = git("ls-tree", "-r", "--name-only", "release").split()
    assert set(files) == {"README.md", "docs/FORMAT.md", "ledger_collect.py", "tools/make_release.sh"}
    assert git("rev-list", "--count", "release").strip() == "1"
    assert "Initial public release" in git("log", "-1", "--format=%B", "release")
    assert run().returncode == 1  # no overwrite


def test_install_idempotent_and_no_overwrite(tmp_path):
    repo = mkrepo(tmp_path)
    (repo / "tools").mkdir()
    (repo / "tools" / "hooks").mkdir()
    (repo / "tools" / "hooks" / "pre-push").write_text("#!/bin/sh\n")
    script = repo / "tools" / "install-hooks.sh"
    script.write_text(INSTALL.read_text())
    run = lambda: subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert run().returncode == 0
    link = repo / ".git" / "hooks" / "pre-push"
    assert link.is_symlink() and link.resolve() == (repo / "tools/hooks/pre-push").resolve()
    assert "already" in run().stdout
    link.unlink()
    link.write_text("foreign")
    r = run()
    assert r.returncode == 1 and "WARNING" in r.stderr and link.read_text() == "foreign"
