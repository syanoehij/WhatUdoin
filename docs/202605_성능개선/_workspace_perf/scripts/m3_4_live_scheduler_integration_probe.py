"""M3-4 라이브 Scheduler service 통합 probe.

1. WhatUdoinSupervisor 인스턴스 생성 -> ensure_internal_token() -> scheduler_service_spec()
2. start_service()로 subprocess.Popen으로 scheduler_service.py 실제 실행
3. /healthz 폴링 -> 200 + status in {ok, starting} 단언
4. healthz JSON 상세 단언:
   - jobs_count >= 6 (cron 6종)
   - next_run_at 비어있지 않음
   - last_finalize_expired_done_at 채워짐 (startup 콜백 1회 실행)
   - uptime_seconds >= 0
5. supervisor STOP_ORDER 따라 stop_all() -> 5초 내 graceful shutdown 단언
6. service status=stopped 단언
7. 결과 markdown _workspace/perf/m3_4_live/runs/<UTC>/live_scheduler_integration.md

Windows note: stop_all()은 TerminateProcess(hard kill)이므로 SIGTERM graceful은 보장 안 됨.
"5초 내 프로세스 종료"를 shutdown 완료로 간주.
"""
from __future__ import annotations

import datetime
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from supervisor import WhatUdoinSupervisor, scheduler_service_spec  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _poll_healthz(url: str, timeout: float = 15.0, interval: float = 0.5) -> dict:
    """/healthz가 200 + status in {ok, starting} 올 때까지 폴링."""
    deadline = time.monotonic() + timeout
    last: dict = {"ok": False, "status": None, "error": "timeout", "body": {}}
    while time.monotonic() < deadline:
        req = urllib.request.Request(url.rstrip("/") + "/healthz", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read(8192))
                    svc_status = data.get("status")
                    ok = svc_status in ("ok", "starting")
                    last = {"ok": ok, "status": svc_status, "error": "", "body": data}
                    if ok:
                        return last
                else:
                    last = {"ok": False, "status": None, "error": f"http {resp.status}", "body": {}}
        except Exception as exc:
            last = {"ok": False, "status": None, "error": str(exc), "body": {}}
        time.sleep(interval)
    return last


def _poll_healthz_ok(url: str, timeout: float = 25.0, interval: float = 0.5) -> dict:
    """/healthz가 200 + status==ok 올 때까지 폴링 (startup 콜백 완료 대기)."""
    deadline = time.monotonic() + timeout
    last: dict = {"ok": False, "status": None, "error": "timeout", "body": {}}
    while time.monotonic() < deadline:
        req = urllib.request.Request(url.rstrip("/") + "/healthz", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read(8192))
                    svc_status = data.get("status")
                    ok = svc_status == "ok"
                    last = {"ok": ok, "status": svc_status, "error": "", "body": data}
                    if ok:
                        return last
                else:
                    last = {"ok": False, "status": None, "error": f"http {resp.status}", "body": {}}
        except Exception as exc:
            last = {"ok": False, "status": None, "error": str(exc), "body": {}}
        time.sleep(interval)
    return last


def main():
    run_ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = _REPO / "_workspace" / "perf" / "m3_4_live" / "runs" / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    tmp_run = run_dir / "supervisor_run"
    tmp_run.mkdir(exist_ok=True)

    results: list[dict] = []
    healthz_body: dict = {}

    def record(name: str, passed: bool, detail: str = ""):
        results.append({"name": name, "passed": passed, "detail": detail})
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))

    print("\n=== M3-4 Live Scheduler Integration Probe ===")
    print(f"  run_dir: {run_dir}")

    # 1. Supervisor 생성 + 토큰 발급
    print("\n[1] Supervisor 초기화...")
    sup = WhatUdoinSupervisor(run_dir=tmp_run)
    token_info = sup.ensure_internal_token()
    record("ensure_internal_token: path exists",
           Path(token_info.path).exists(),
           str(token_info.path))

    # 2. Scheduler service spec 생성
    port = _free_port()
    python = sys.executable
    sched_script = str(_REPO / "scheduler_service.py")
    record("scheduler_service.py exists", Path(sched_script).exists(), sched_script)

    spec = scheduler_service_spec(
        command=[python, sched_script],
        port=port,
        startup_grace_seconds=2.0,
    )
    record("scheduler_service_spec: name=scheduler", spec.name == "scheduler")
    record("scheduler_service_spec: port env set", str(port) in str(spec.env))
    record("scheduler_service_spec: SCHEDULER_SERVICE=1 in env",
           spec.env.get("WHATUDOIN_SCHEDULER_SERVICE") == "1")

    # 3. Scheduler service 실제 spawn
    print(f"\n[2] Scheduler service spawn (port {port})...")
    state = sup.start_service(spec)
    record("start_service: status=running", state.status == "running",
           f"status={state.status}, pid={state.pid}")

    # 4. /healthz 폴링 (status in {ok, starting} 허용)
    base_url = f"http://127.0.0.1:{port}"
    print(f"\n[3] /healthz 폴링 ({base_url})...")
    healthz = _poll_healthz(base_url, timeout=15.0)
    record("healthz: 200 + status in {ok, starting}", healthz["ok"],
           f"status={healthz['status']} error={healthz['error']}")

    # status=ok 까지 추가 대기 (startup 콜백 finalize_expired_done 완료 후)
    if healthz["ok"] and healthz["status"] != "ok":
        print("  [INFO] status=starting, ok 대기 중 (startup 콜백 실행 중)...")
        healthz = _poll_healthz_ok(base_url, timeout=25.0)

    healthz_body = healthz.get("body", {})

    # 5. healthz JSON 상세 단언
    print("\n[4] healthz 상세 단언...")
    jobs_count = healthz_body.get("jobs_count", 0)
    record("healthz: jobs_count >= 6", jobs_count >= 6,
           f"jobs_count={jobs_count}")
    next_run_at = healthz_body.get("next_run_at")
    record("healthz: next_run_at 비어있지 않음", bool(next_run_at),
           f"next_run_at={next_run_at}")
    last_fin = healthz_body.get("last_finalize_expired_done_at")
    record("healthz: last_finalize_expired_done_at 채워짐", bool(last_fin),
           f"last_finalize_expired_done_at={last_fin}")
    uptime = healthz_body.get("uptime_seconds", -1)
    record("healthz: uptime_seconds >= 0", uptime >= 0,
           f"uptime_seconds={uptime}")

    # 6. stop_all() + 5초 내 프로세스 종료 단언
    print(f"\n[5] Supervisor stop_all()...")
    t0 = time.monotonic()
    sup.stop_all(timeout=5.0)
    elapsed = time.monotonic() - t0
    record("stop_all: 5초 내 완료", elapsed < 6.0,
           f"elapsed={elapsed:.2f}s")

    state_after = sup.services.get("scheduler")
    record("stop_all: service status=stopped",
           state_after is not None and state_after.status == "stopped",
           f"status={state_after.status if state_after else 'N/A'}")

    proc_dead = (state_after is None or state_after._process is None
                 or state_after._process.poll() is not None)
    record("stop_all: process terminated", proc_dead,
           f"poll={state_after._process.poll() if state_after and state_after._process else 'N/A'}")

    # 7. 결과 요약
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n=== 결과 요약: {passed}/{total} PASS ===")

    # 8. Markdown 저장
    md_path = run_dir / "live_scheduler_integration.md"
    lines = [
        "# M3-4 Live Scheduler Integration Probe",
        "",
        f"**실행 시각**: {run_ts}",
        f"**Python**: {sys.executable}",
        f"**Scheduler port**: {port}",
        f"**run_dir**: {tmp_run}",
        "",
        "## 시나리오 결과",
        "",
        "| # | 항목 | 결과 | 상세 |",
        "|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        status_str = "PASS" if r["passed"] else "FAIL"
        detail = r["detail"].replace("|", "\\|") if r["detail"] else ""
        lines.append(f"| {i} | {r['name']} | {status_str} | {detail} |")

    lines += [
        "",
        "## healthz JSON 스냅샷",
        "",
        "```json",
        json.dumps(healthz_body, indent=2, ensure_ascii=False),
        "```",
        "",
        "## 총계",
        "",
        f"**{passed}/{total} PASS**",
        "",
        "## Windows note",
        "",
        "stop_all()은 TerminateProcess(hard kill)이므로 SIGTERM graceful shutdown은 보장 안 됨.",
        "5초 내 프로세스 종료를 shutdown 완료 기준으로 사용.",
    ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n결과 저장: {md_path}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
