"""Synthetic log builders — no real paths, names or content."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def usage(out=10, inp=1, w5=0, w1=100, cr=50, think=None):
    u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
         "cache_creation_input_tokens": w5 + w1,
         "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
         "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}}
    if think is not None:
        u["output_tokens_details"] = {"thinking_tokens": think}
    return u


def line(cwd, msg_id, req_id, ts="2026-01-05T10:00:00.000Z", sid="s1", entry="cli",
         agent_id=None, model="model-x", **usage_kw):
    d = {"type": "assistant", "timestamp": ts, "sessionId": sid, "cwd": cwd, "requestId": req_id,
         "entrypoint": entry, "isSidechain": agent_id is not None,
         "message": {"id": msg_id, "model": model, "usage": usage(**usage_kw)}}
    if agent_id:
        d["agentId"] = agent_id
    return json.dumps(d)


def write(path: Path, lines, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode) as fh:
        for l in lines:
            fh.write(l + "\n")


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    root = home / "work"
    (root / "alpha" / "sub").mkdir(parents=True)
    (root / "beta").mkdir(parents=True)
    projects = tmp_path / "logs"
    data = tmp_path / "data"
    return {"home": home, "root": root, "projects": projects, "data": data}


def run(env, *extra):
    import ledger_collect
    return ledger_collect.main(["--projects-dir", str(env["projects"]), "--data-dir", str(env["data"]),
                                "--roots", str(env["root"]), "--home", str(env["home"]),
                                "--config", str(env["data"].parent / "none.json"), *extra])


def read_rows(env):
    import csv
    rows = []
    for f in sorted((env["data"] / "ledger").glob("*.csv")):
        with f.open(newline="") as fh:
            rows += list(csv.DictReader(fh))
    return rows
