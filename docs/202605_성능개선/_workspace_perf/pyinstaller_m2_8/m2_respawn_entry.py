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
