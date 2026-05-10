"""
M2-8 PyInstaller frozen self re-spawn probe.

Builds a minimal onedir executable and verifies the supervisor primitive that
future M2 services depend on: frozen parent re-runs sys.executable with a
--service argument, the child recognizes service mode while frozen, and the
parent can request a graceful child shutdown.
"""

from __future__ import annotations

import json
import os
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
BASE_DIR = REPO_ROOT / "_workspace" / "perf" / "pyinstaller_m2_8"
ENTRY = BASE_DIR / "m2_respawn_entry.py"
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
SPEC_DIR = BASE_DIR / "spec"
RUNS_DIR = BASE_DIR / "runs"
APP_NAME = "M2RespawnProbe"


ENTRY_SOURCE = r'''
from __future__ import annotations

import argparse
import json
import multiprocessing
import subprocess
import sys
import time
from pathlib import Path


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def service_main(args: argparse.Namespace) -> int:
    multiprocessing.freeze_support()
    run_dir = Path(args.run_dir)
    stop_flag = run_dir / "stop.flag"
    write_json(run_dir / "service_ready.json", {
        "mode": "service",
        "service": args.service,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "argv": sys.argv,
        "freeze_support_called": True,
    })
    deadline = time.time() + 30
    while time.time() < deadline:
        if stop_flag.exists():
            write_json(run_dir / "service_exit.json", {
                "graceful": True,
                "frozen": bool(getattr(sys, "frozen", False)),
                "executable": sys.executable,
            })
            return 0
        time.sleep(0.1)
    write_json(run_dir / "service_exit.json", {
        "graceful": False,
        "reason": "timeout",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
    })
    return 2


def supervisor_main(args: argparse.Namespace) -> int:
    multiprocessing.freeze_support()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    child_cmd = [sys.executable, "--service=echo", "--run-dir", str(run_dir)]
    proc = subprocess.Popen(child_cmd)
    ready_path = run_dir / "service_ready.json"
    deadline = time.time() + 30
    while time.time() < deadline and not ready_path.exists():
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    ready = json.loads(ready_path.read_text(encoding="utf-8")) if ready_path.exists() else {}
    (run_dir / "stop.flag").write_text("stop", encoding="utf-8")
    try:
        child_returncode = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        child_returncode = proc.wait(timeout=5)
    exit_path = run_dir / "service_exit.json"
    service_exit = json.loads(exit_path.read_text(encoding="utf-8")) if exit_path.exists() else {}
    executable = Path(sys.executable).resolve()
    child_executable = Path(ready.get("executable", "")).resolve() if ready.get("executable") else None
    checks = {
        "parent_frozen": bool(getattr(sys, "frozen", False)),
        "child_frozen": bool(ready.get("frozen")),
        "child_service_arg": "--service=echo" in ready.get("argv", []),
        "child_uses_same_executable": child_executable == executable,
        "freeze_support_called": True and bool(ready.get("freeze_support_called")),
        "child_graceful_exit": child_returncode == 0 and bool(service_exit.get("graceful")),
    }
    passed = all(checks.values())
    write_json(run_dir / "result.json", {
        "passed": passed,
        "checks": checks,
        "parent": {
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "argv": sys.argv,
            "child_cmd": child_cmd,
        },
        "service_ready": ready,
        "service_exit": service_exit,
        "child_returncode": child_returncode,
    })
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    if args.service:
        return service_main(args)
    return supervisor_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def log(message: str) -> None:
    print(f"[m2-8] {message}", flush=True)


def write_entry() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    ENTRY.write_text(textwrap.dedent(ENTRY_SOURCE).lstrip(), encoding="utf-8")


def clean_previous_build() -> None:
    for path in (DIST_DIR, BUILD_DIR, SPEC_DIR):
        if path.exists():
            shutil.rmtree(path)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def run_command(cmd: list[str], log_prefix: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / f"{log_prefix}_stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (BASE_DIR / f"{log_prefix}_stderr.log").write_text(result.stderr or "", encoding="utf-8")
    return result


def find_exe() -> Path:
    exe = DIST_DIR / APP_NAME / f"{APP_NAME}.exe"
    if exe.exists():
        return exe
    matches = list((DIST_DIR / APP_NAME).glob("*.exe")) if (DIST_DIR / APP_NAME).exists() else []
    if matches:
        return matches[0]
    raise FileNotFoundError(f"frozen exe not found under {DIST_DIR / APP_NAME}")


def main() -> int:
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / started
    run_dir.mkdir(parents=True, exist_ok=True)
    write_entry()
    clean_previous_build()
    build_cmd = [
        PYTHON,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--name",
        APP_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(SPEC_DIR),
        str(ENTRY),
    ]
    log("building onedir executable")
    build = run_command(build_cmd, "pyinstaller_build")
    if build.returncode != 0:
        summary = {
            "passed": False,
            "phase": "build",
            "returncode": build.returncode,
            "build_cmd": build_cmd,
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(run_dir)
        return 1
    exe = find_exe()
    log(f"running frozen exe: {exe}")
    run_cmd = [str(exe), "--run-dir", str(run_dir)]
    probe = run_command(run_cmd, "frozen_run")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    passed = probe.returncode == 0 and bool(result.get("passed"))
    summary = {
        "passed": passed,
        "build_returncode": build.returncode,
        "run_returncode": probe.returncode,
        "exe": str(exe),
        "dist_dir": str(DIST_DIR / APP_NAME),
        "run_dir": str(run_dir),
        "result": result,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# M2-8 PyInstaller respawn probe",
        "",
        f"- verdict: {'PASS' if passed else 'FAIL'}",
        f"- exe: `{exe}`",
        f"- dist_dir: `{DIST_DIR / APP_NAME}`",
        f"- run_dir: `{run_dir}`",
        "",
        "| check | result |",
        "|---|---|",
    ]
    for key, value in (result.get("checks") or {}).items():
        lines.append(f"| {key} | {value} |")
    lines.extend([
        "",
        "## Result",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    (run_dir / "m2_8_pyinstaller_respawn_probe.md").write_text("\n".join(lines), encoding="utf-8")
    print(run_dir)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
