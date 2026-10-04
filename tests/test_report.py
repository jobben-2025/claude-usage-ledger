import csv
import json

import ledger_report
from ledger_collect import COLUMNS

PRICES = {"_asof": "x", "m-a": {"input": 1, "output": 10, "cache_write_5m": 2, "cache_write_1h": 3, "cache_read": 0.5}}


def mk(ts, project, model, inp=0, w5=0, w1=0, cr=0, out=0, un="0", **kw):
    d = dict.fromkeys(COLUMNS, "0")
    d.update(ts=ts, project=project, model=model, input=inp, cache_write_5m=w5, cache_write_1h=w1,
             cache_read=cr, output=out, unattended=un, session_id="s", agent="main", **kw)
    return d


def setup(tmp_path, rows_by_month):
    data = tmp_path / "data"
    (data / "ledger").mkdir(parents=True)
    for month, rows in rows_by_month.items():
        with (data / "ledger" / f"{month}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(PRICES))
    return data, p


def run(tmp_path, data, p, *extra, capsys):
    code = ledger_report.main(["--data-dir", str(data), "--prices", str(p),
                               "--config", str(tmp_path / "none.json"), "--json", *extra])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


ROWS = {"2026-01": [
    mk("2026-01-05T12:00:00Z", "alpha", "m-a", inp=1_000_000, w5=1_000_000, w1=1_000_000, cr=2_000_000, out=100_000,
       thinking=40_000, un="1"),
    mk("2026-01-05T12:05:00Z", "alpha", "m-a", out=100_000),
    mk("2026-01-20T12:00:00Z", "beta", "m-unknown", inp=500, out=500),
], "2026-02": [mk("2026-02-10T12:00:00Z", "beta", "m-a", inp=1_000_000)]}


def test_sums_and_usd(tmp_path, capsys):
    data, p = setup(tmp_path, ROWS)
    code, rep = run(tmp_path, data, p, capsys=capsys)
    assert code == 0
    a = next(r for r in rep["rows"] if r["project"] == "alpha")
    assert a["input"] == 1_000_000 and a["output"] == 200_000 and a["thinking"] == 40_000
    assert a["responses"] == 2 and a["unattended_share"] == 0.5
    assert (a["usd_input"], a["usd_cache_write_5m"], a["usd_cache_write_1h"], a["usd_cache_read"], a["usd_output"]) == (1, 2, 3, 1, 2)
    assert a["usd_total"] == 9


def test_unknown_model(tmp_path, capsys):
    data, p = setup(tmp_path, ROWS)
    _, rep = run(tmp_path, data, p, capsys=capsys)
    assert rep["unpriced_models"] == {"m-unknown": 1000}
    b = next(r for r in rep["rows"] if r["project"] == "beta")
    assert b["unpriced_tokens"] == 1000 and b["usd_total"] == 1


def test_filters(tmp_path, capsys):
    data, p = setup(tmp_path, ROWS)
    _, rep = run(tmp_path, data, p, "--month", "2026-02", capsys=capsys)
    assert [r["project"] for r in rep["rows"]] == ["beta"]
    _, rep = run(tmp_path, data, p, "--since", "2026-01-19", "--until", "2026-01-21", capsys=capsys)
    assert rep["rows"][0]["responses"] == 1


def test_by(tmp_path, capsys):
    data, p = setup(tmp_path, ROWS)
    _, rep = run(tmp_path, data, p, "--by", "model", "unattended", capsys=capsys)
    keys = {(r["model"], r["unattended"]) for r in rep["rows"]}
    assert ("m-a", "unattended") in keys and ("m-a", "interactive") in keys


def test_formats_and_out(tmp_path, capsys):
    data, p = setup(tmp_path, ROWS)
    base = ["--data-dir", str(data), "--prices", str(p), "--config", str(tmp_path / "none.json")]
    assert ledger_report.main(base) == 0
    assert "USD = API list-price" in capsys.readouterr().out
    out = tmp_path / "r" / "x.csv"
    assert ledger_report.main(base + ["--csv", "--out", str(out)]) == 0
    assert out.read_text().startswith("project,")


def test_exit_codes(tmp_path, capsys):
    assert ledger_report.main(["--config", str(tmp_path / "none.json")]) == 2
    data, p = setup(tmp_path, ROWS)
    assert ledger_report.main(["--data-dir", str(data), "--prices", str(p), "--since", "bad",
                               "--config", str(tmp_path / "none.json")]) == 2
    assert ledger_report.main(["--bogus"]) == 2
