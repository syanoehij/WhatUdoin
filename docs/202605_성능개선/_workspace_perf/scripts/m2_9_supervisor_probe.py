"""
M2-9 Supervisor skeleton probe.

Exercises the WhatUdoinSupervisor primitives without starting the real app:
startup sequence declaration, internal token file/env propagation, service
status/log paths, and separate startup-failure vs runtime-crash counters.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
PYTHON = sys.executable
BASE_DIR = REPO_ROOT / "_workspace" / "perf" / "supervisor_m2_9"
RUNS_DIR = BASE_DIR / "runs"
CHILD = BASE_DIR / "m2_9_child_service.py"


CHILD_SOURCE = r'''
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    if args.mode == "startup_fail":
        return 7
    run_dir = Path(args.run_dir)
    token_file = os.environ.get("WHATUDOIN_INTERNAL_TOKEN_FILE", "")
    token_from_file = ""
    if token_file and Path(token_file).exists():
        token_from_file = Path(token_file).read_text(encoding="utf-8").strip()
    payload = {
        "mode": args.mode,
        "pid": os.getpid(),
        "service_name": os.environ.get("WHATUDOIN_SERVICE_NAME", ""),
        "token_env_present": bool(os.environ.get("WHATUDOIN_INTERNAL_TOKEN")),
        "token_file": token_file,
        "token_matches_file": os.environ.get("WHATUDOIN_INTERNAL_TOKEN", "") == token_from_file,
        "argv": sys.argv,
    }
    (run_dir / f"child_{args.mode}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.mode == "runtime_crash":
        time.sleep(1.2)
        return 9
    time.sleep(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def write_child() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    CHILD.write_text(textwrap.dedent(CHILD_SOURCE).lstrip(), encoding="utf-8")


def run_py_compile() -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "py_compile", str(REPO_ROOT / "supervisor.py"), str(CHILD)],
        cwd=str(REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def wait_for_file(path: Path, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from supervisor import M2_STARTUP_SEQUENCE, ServiceSpec, WhatUdoinSupervisor

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / stamp
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    write_child()
    compile_result = run_py_compile()
    (run_dir / "py_compile_stdout.log").write_text(compile_result.stdout or "", encoding="utf-8")
    (run_dir / "py_compile_stderr.log").write_text(compile_result.stderr or "", encoding="utf-8")

    supervisor = WhatUdoinSupervisor(run_dir=run_dir / "runtime")
    token_info = supervisor.ensure_internal_token()

    web_api = supervisor.start_service(ServiceSpec(
        name="web-api",
        command=[PYTHON, str(CHILD), "--mode", "ok", "--run-dir", str(run_dir)],
        startup_grace_seconds=0.3,
    ))
    wait_for_file(run_dir / "child_ok.json")
    child_ok = json.loads((run_dir / "child_ok.json").read_text(encoding="utf-8"))
    web_api_started = web_api.status == "running" and web_api.pid is not None

    startup_fail = supervisor.start_service(ServiceSpec(
        name="startup-fail",
        command=[PYTHON, str(CHILD), "--mode", "startup_fail", "--run-dir", str(run_dir)],
        startup_grace_seconds=0.3,
    ))

    runtime_crash = supervisor.start_service(ServiceSpec(
        name="runtime-crash",
        command=[PYTHON, str(CHILD), "--mode", "runtime_crash", "--run-dir", str(run_dir)],
        startup_grace_seconds=0.3,
    ))
    wait_for_file(run_dir / "child_runtime_crash.json")
    time.sleep(1.4)
    runtime_crash = supervisor.poll_service("runtime-crash")
    supervisor.stop_service("web-api", timeout=2.0)
    snapshot = supervisor.snapshot()

    checks = {
        "startup_sequence_has_7_steps": len(M2_STARTUP_SEQUENCE) == 7,
        "internal_token_file_exists": Path(token_info.path).exists(),
        "internal_token_not_empty": bool(Path(token_info.path).read_text(encoding="utf-8").strip()),
        "internal_token_acl_applied": bool(token_info.acl_applied),
        "spawn_env_token_matches_file": bool(child_ok.get("token_matches_file")),
        "spawn_env_service_name": child_ok.get("service_name") == "web-api",
        "web_api_started_with_pid": web_api_started,
        "startup_failure_counter_only": startup_fail.status == "failed_startup"
        and startup_fail.startup_failures == 1
        and startup_fail.runtime_crashes == 0,
        "runtime_crash_counter_only": runtime_crash is not None
        and runtime_crash.status == "crashed"
        and runtime_crash.runtime_crashes == 1
        and runtime_crash.startup_failures == 0,
        "service_log_paths_exist": all(
            Path(state["stdout_log"]).exists() and Path(state["stderr_log"]).exists()
            for state in snapshot["services"].values()
        ),
        "py_compile_passed": compile_result.returncode == 0,
    }
    passed = all(checks.values())
    summary = {
        "passed": passed,
        "checks": checks,
        "token_info": token_info.__dict__,
        "child_ok": child_ok,
        "snapshot": snapshot,
    }
    (run_dir / "m2_9_supervisor_probe.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# M2-9 Supervisor probe",
        "",
        f"- verdict: {'PASS' if passed else 'FAIL'}",
        f"- run_dir: `{run_dir}`",
        "",
        "| check | result |",
        "|---|---|",
    ]
    for key, value in checks.items():
        lines.append(f"| {key} | {value} |")
    lines.extend([
        "",
        "## Token",
        "",
        "```json",
        json.dumps(token_info.__dict__, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Snapshot",
        "",
        "```json",
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    (run_dir / "m2_9_supervisor_probe.md").write_text("\n".join(lines), encoding="utf-8")
    print(run_dir)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
