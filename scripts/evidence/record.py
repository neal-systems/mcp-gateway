#!/usr/bin/env python3
import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
ROOT_MARKER = "pytest.ini"
VALID_CLASSES = ("unit", "container", "ci", "cloud", "oauth")
VALID_STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN")
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def now_log():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
def walk_root(start):
    cur = start
    while not (cur / ROOT_MARKER).is_file():
        if cur.parent == cur:
            return start
        cur = cur.parent
    return cur
def as_root_relative(root, value, default):
    p = Path(default if value is None else value)
    return p if p.is_absolute() else root / p
def manifest(path):
    if not path.exists():
        return {"schema": 1, "generated_at": now_iso(), "checks": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
def write_manifest(path, checks):
    payload = {"schema": 1, "generated_at": now_iso(), "checks": sorted(checks, key=lambda x: x.get("id", ""))}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
def upsert(path, record):
    data = manifest(path)
    checks = data.get("checks", [])
    if not isinstance(checks, list):
        checks = []
    for i, row in enumerate(checks):
        if row.get("id") == record["id"]:
            checks[i] = record
            break
    else:
        checks.append(record)
    write_manifest(path, checks)
def git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
def to_rel_posix(path, root):
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
def status_counts(checks):
    counts = {s: 0 for s in VALID_STATUSES}
    for row in checks:
        if (s := row.get("status")) in counts:
            counts[s] += 1
    return counts
def run_mode(args, root, manifest_path, raw_dir):
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = raw_dir / f"{args.id}-{now_log()}.log"
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        print("run: a command is required after --", file=sys.stderr)
        return 2
    started = now_iso()
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    duration = round(time.perf_counter() - t0, 2)
    finished = now_iso()
    log_path.write_text(proc.stdout or "", encoding="utf-8")
    evidence_path = to_rel_posix(log_path, root)
    status = "PASS" if ((proc.returncode == 0) != args.expect_fail) else "FAIL"
    upsert(manifest_path, {
        "id": args.id,
        "title": args.title,
        "class": args.class_,
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "duration_s": duration,
        "git_sha": git_head(),
        "image_digest": args.image_digest,
        "environment": args.env,
        "command": shlex.join(cmd),
        "evidence_path": evidence_path,
        "notes": args.notes,
        "exit_code": proc.returncode,
    })
    print(f"{args.id} {status} duration_s={duration:.2f} evidence={evidence_path}")
    return 0 if status == "PASS" else 1
def mark_mode(args, manifest_path):
    now = now_iso()
    upsert(manifest_path, {
        "id": args.id,
        "title": args.title,
        "class": args.class_,
        "status": args.status,
        "started_at": now,
        "finished_at": now,
        "duration_s": 0,
        "git_sha": git_head(),
        "image_digest": args.image_digest,
        "environment": args.env,
        "command": args.command or "",
        "evidence_path": args.evidence_path or "",
        "notes": args.notes,
        "exit_code": None,
    })
    return 0
def render_mode(args, root, manifest_path):
    checks = sorted(manifest(manifest_path).get("checks", []), key=lambda x: x.get("id", ""))
    counts = status_counts(checks)
    rows = [
        " ".join(f"{s}={counts[s]}" for s in VALID_STATUSES),
        "",
        "| id | class | status | environment | finished_at | duration_s | git_sha | image_digest | evidence path | notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in checks:
        sha = row.get("git_sha") or "-"
        if sha != "-" and len(sha) > 12:
            sha = sha[:12]
        digest = row.get("image_digest")
        digest = f"sha256:{digest[7:19]}" if isinstance(digest, str) and digest.startswith("sha256:") else "-"
        rows.append("| {} | {} | {} | {} | {} | {:.2f} | {} | {} | {} | {} |".format(
            row.get("id", ""), row.get("class", ""), row.get("status", ""), row.get("environment", ""),
            row.get("finished_at", ""), float(row.get("duration_s", 0)), sha, digest,
            row.get("evidence_path", "") or "-", (row.get("notes") or "").replace("|", "\\|")))
    rows.append("")
    rows.append("Raw logs under evidence/raw/ are private and not published.")
    out = as_root_relative(root, args.out, "docs/cloud/EVIDENCE.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return 0
def summary_mode(manifest_path):
    counts = status_counts(manifest(manifest_path).get("checks", []))
    for status, value in counts.items():
        print(f"{status}: {value}")
    return 1 if counts["FAIL"] > 0 else 0
def parse_args(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", default=None)
    p.add_argument("--raw-dir", default=None)
    sub = p.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("run")
    run.add_argument("--id", required=True)
    run.add_argument("--title", required=True)
    run.add_argument("--class", dest="class_", required=True, choices=VALID_CLASSES)
    run.add_argument("--env", required=True)
    run.add_argument("--image-digest")
    run.add_argument("--notes", default="")
    run.add_argument("--expect-fail", action="store_true")
    run.add_argument("cmd", nargs=argparse.REMAINDER)
    mark = sub.add_parser("mark")
    mark.add_argument("--id", required=True)
    mark.add_argument("--title", required=True)
    mark.add_argument("--class", dest="class_", required=True, choices=VALID_CLASSES)
    mark.add_argument("--env", required=True)
    mark.add_argument("--status", required=True, choices=VALID_STATUSES)
    mark.add_argument("--notes", default="")
    mark.add_argument("--evidence-path")
    mark.add_argument("--image-digest")
    mark.add_argument("--command")
    render = sub.add_parser("render")
    render.add_argument("--out", default="docs/cloud/EVIDENCE.md")
    sub.add_parser("summary")
    return p.parse_args(argv)
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    root = walk_root(Path(__file__).resolve().parent)
    manifest_path = as_root_relative(root, args.manifest, "evidence/acceptance.json")
    raw_dir = as_root_relative(root, args.raw_dir, "evidence/raw")
    if args.mode == "run":
        if not args.cmd:
            raise SystemExit("run requires a command after --")
        return run_mode(args, root, manifest_path, raw_dir)
    if args.mode == "mark":
        return mark_mode(args, manifest_path)
    if args.mode == "render":
        return render_mode(args, root, manifest_path)
    return summary_mode(manifest_path)
if __name__ == "__main__":
    sys.exit(main())
