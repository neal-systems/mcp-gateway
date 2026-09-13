import json
import subprocess
from pathlib import Path

S = Path(__file__).resolve().parents[1] / "scripts" / "evidence" / "record.py"
B = "Raw logs under evidence/raw/ are private and not published."


def call(tmp_path, *args, m="acceptance.json"):
    mp = tmp_path / m
    rp = tmp_path / "raw"
    r = subprocess.run(["python3", str(S), "--manifest", str(mp), "--raw-dir", str(rp), *args], text=True, capture_output=True)
    return r, mp


def manifest(path):
    return json.loads(path.read_text(encoding="utf-8"))


def by_id(path, check_id):
    return next(c for c in manifest(path)["checks"] if c["id"] == check_id)


def test_run_records_pass(tmp_path):
    r, mp = call(tmp_path, "run", "--id", "run-pass", "--title", "pass", "--class", "unit", "--env", "ci", "--", "python3", "-c", "print('hi')")
    assert r.returncode == 0
    c = manifest(mp)["checks"][0]
    assert c["status"] == "PASS" and c["id"] == "run-pass" and isinstance(c["duration_s"], (int, float))
    assert "hi" in Path(c["evidence_path"]).read_text(encoding="utf-8")
    assert str(Path(c["evidence_path"]).resolve()).startswith(str((tmp_path / "raw").resolve()))


def test_run_fail_and_expect_fail(tmp_path):
    r, mp = call(tmp_path, "run", "--id", "run-fail", "--title", "fail", "--class", "unit", "--env", "ci", "--", "python3", "-c", "import sys;sys.exit(3)")
    assert r.returncode == 1 and by_id(mp, "run-fail")["status"] == "FAIL"
    r, mp = call(tmp_path, "run", "--expect-fail", "--id", "run-ef", "--title", "ef", "--class", "unit", "--env", "ci", "--", "python3", "-c", "import sys;sys.exit(3)")
    assert r.returncode == 0 and by_id(mp, "run-ef")["status"] == "PASS"


def test_mark_upsert_and_replace(tmp_path):
    call(tmp_path, "mark", "--id", "x", "--title", "first", "--class", "unit", "--env", "ci", "--status", "BLOCKED", "--notes", "old")
    call(tmp_path, "mark", "--id", "x", "--title", "second", "--class", "unit", "--env", "ci", "--status", "PASS", "--notes", "new")
    checks = manifest(tmp_path / "acceptance.json")["checks"]
    assert len(checks) == 1 and checks[0]["notes"] == "new" and checks[0]["status"] == "PASS"


def test_render_and_privacy(tmp_path):
    call(tmp_path, "mark", "--id", "r", "--title", "render", "--class", "unit", "--env", "ci", "--status", "PASS")
    out = tmp_path / "evidence.md"
    r, _ = call(tmp_path, "render", "--out", str(out))
    t = out.read_text(encoding="utf-8")
    assert r.returncode == 0 and "r" in t and "PASS" in t and B in t


def test_summary_status_codes(tmp_path):
    call(tmp_path, "mark", "--id", "a", "--title", "a", "--class", "unit", "--env", "ci", "--status", "PASS", m="sum.json")
    assert call(tmp_path, "summary", m="sum.json")[0].returncode == 0
    call(tmp_path, "mark", "--id", "b", "--title", "b", "--class", "unit", "--env", "ci", "--status", "FAIL", m="sum.json")
    assert call(tmp_path, "summary", m="sum.json")[0].returncode == 1


def test_manifest_schema_and_sorted_ids(tmp_path):
    call(tmp_path, "mark", "--id", "zzz", "--title", "zzz", "--class", "unit", "--env", "ci", "--status", "PASS")
    call(tmp_path, "mark", "--id", "aaa", "--title", "aaa", "--class", "unit", "--env", "ci", "--status", "PASS")
    data = manifest(tmp_path / "acceptance.json")
    ids = [c["id"] for c in data["checks"]]
    assert data["schema"] == 1 and ids == sorted(ids)
