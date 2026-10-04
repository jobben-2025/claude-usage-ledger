import csv
import json

import ledger_report
import usage_sample
from test_report import mk, setup

R1 = 1_800_000_000  # 2027-01-15T08:00:00Z, synthetic
SCOLS = usage_sample.COLUMNS


def write_samples(data, rows):
    (data / "samples").mkdir()
    with (data / "samples" / "usage.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SCOLS)
        w.writeheader()
        for r in rows:
            w.writerow(dict.fromkeys(SCOLS, "") | r)


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_window_split(tmp_path, capsys):
    rows = {"2027-01": [mk(iso(R1 - 3600), "alpha", "m-a", inp=3_000_000),
                        mk(iso(R1 - 1800), "beta", "m-a", inp=1_000_000),
                        mk(iso(R1 + 3600), "beta", "m-a", inp=1_000_000)]}  # next window, no samples
    data, p = setup(tmp_path, rows)
    write_samples(data, [
        {"ts_utc": iso(R1 - 7000), "five_hour_pct": 10, "five_hour_resets_at": R1, "stale": 0},
        {"ts_utc": iso(R1 - 100), "five_hour_pct": 40, "five_hour_resets_at": R1 + 5, "stale": 0},
        {"ts_utc": iso(R1 - 50), "five_hour_pct": 99, "five_hour_resets_at": R1, "stale": 1},
        {"ts_utc": iso(R1 - 50), "seven_day_pct": 20, "seven_day_resets_at": R1 + 86400, "stale": 0}])
    code = ledger_report.main(["--data-dir", str(data), "--prices", str(p), "--config", str(tmp_path / "n.json"),
                               "--json", "--windows"])
    rep = json.loads(capsys.readouterr().out)
    a = next(r for r in rep["rows"] if r["project"] == "alpha")
    b = next(r for r in rep["rows"] if r["project"] == "beta")
    assert code == 0
    assert a["pct_5h"] == 30.0          # 40% * 3/4, stale 99 ignored
    assert b["pct_5h"] == 10.0          # 40% * 1/4; the uncovered response adds nothing
    assert b["cov_pct_5h"] == 0.5
    assert a["pct_7d"] == 12.0  # 20% * 3/5 of the weekly weight


def test_nv_without_samples(tmp_path, capsys):
    data, p = setup(tmp_path, {"2027-01": [mk(iso(R1), "alpha", "m-a", inp=1000)]})
    ledger_report.main(["--data-dir", str(data), "--prices", str(p), "--config", str(tmp_path / "n.json"),
                        "--windows"])
    out = capsys.readouterr().out
    assert "n/v" in out


def test_sampler_row_and_idempotence(tmp_path):
    import datetime as dt
    status = {"five_hour_pct": 42.0, "five_hour_resets_at": 1, "seven_day_pct": 60.0,
              "seven_day_resets_at": 2, "source": "cli", "snapshot_age_sec": 120, "stale": False}
    now = dt.datetime(2027, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    row = usage_sample.make_row(status, now, 90)
    assert row["stale"] == 1 and row["ts_utc"] == "2027-01-01T12:00:00Z"
    path = tmp_path / "s" / "usage.csv"
    assert usage_sample.append_row(path, row) is True
    assert usage_sample.append_row(path, row) is False
    assert usage_sample.make_row(dict(status, snapshot_age_sec=5), now, 90)["stale"] == 0
    assert path.read_text().splitlines()[0] == ",".join(SCOLS)


def test_sampler_main_with_fake_scheduler(tmp_path):
    fake = tmp_path / "sched.py"
    fake.write_text("import json;print(json.dumps({'five_hour_pct': 5.0, 'five_hour_resets_at': 9, "
                    "'seven_day_pct': 7.0, 'seven_day_resets_at': 8, 'source': 'x', 'snapshot_age_sec': 1, "
                    "'stale': False}))")
    cfg = str(tmp_path / "none.json")
    assert usage_sample.main(["--data-dir", str(tmp_path / "d"), "--scheduler", str(fake), "--config", cfg]) == 0
    rows = list(csv.DictReader((tmp_path / "d" / "samples" / "usage.csv").open()))
    assert rows[0]["five_hour_pct"] == "5.0" and rows[0]["stale"] == "0"
    broken = tmp_path / "bad.py"
    broken.write_text("print('nope')")
    assert usage_sample.main(["--data-dir", str(tmp_path / "d2"), "--scheduler", str(broken), "--config", cfg]) == 0
    assert not (tmp_path / "d2" / "samples").exists()
    assert usage_sample.main(["--config", cfg]) == 2
