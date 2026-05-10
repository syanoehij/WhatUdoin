"""M3-4 일반 API p95 + database is locked 0건 측정 probe.

환경 cap: 60초 이내.

전략:
1. Scheduler service만 spawn (60s cap 준수)
2. cron 6종 등록 + startup 콜백 완료 후 healthz 50회 동시 GET p95 < 500ms 단언
3. Scheduler service 로그에서 'database is locked' 0건 확인
4. 결과 markdown _workspace/perf/m3_4_live/runs/<UTC>/general_api_p95.md

Scheduler service /healthz는 메모리 내 sched.get_jobs() 조회만 하므로
DB I/O 없음 -> general JSON API p95 대역 측정으로 적합.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import socket
import sys
import time
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

HEALTHZ_PARALLEL = 50
HEALTHZ_P95_SLA_MS = 500.0
TOTAL_TIMEOUT_SEC = 60


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_healthz(url: str, timeout: float = 5.0) -> tuple[float, int, str]:
    """GET /healthz 1회 실행, (latency_ms, status_code, error) 반환."""
    endpoint = url.rstrip("/") + "/healthz"
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(endpoint, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(8192)
            latency_ms = (time.perf_counter() - t0) * 1000
            return latency_ms, resp.status, ""
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        return latency_ms, 0, str(exc)


def _poll_healthz_ok(url: str, timeout: float = 25.0, interval: float = 0.5) -> dict:
    """/healthz가 200 + status==ok 올 때까지 폴링."""
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


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * pct / 100), len(sorted_vals) - 1)
    return sorted_vals[idx]


def main():
    run_ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = _REPO / "_workspace" / "perf" / "m3_4_live" / "runs" / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    tmp_run = run_dir / "supervisor_run"
    tmp_run.mkdir(exist_ok=True)

    results: list[dict] = []
    overall_start = time.monotonic()

    def record(name: str, passed: bool, detail: str = ""):
        results.append({"name": name, "passed": passed, "detail": detail})
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))

    print("\n=== M3-4 General API p95 + Database Locked 0건 Probe ===")
    print(f"  run_dir: {run_dir}")
    print(f"  time cap: {TOTAL_TIMEOUT_SEC}s")

    # 1. Supervisor 초기화
    print("\n[1] Supervisor 초기화...")
    sup = WhatUdoinSupervisor(run_dir=tmp_run)
    sup.ensure_internal_token()

    # 2. Scheduler service spawn
    port = _free_port()
    python = sys.executable
    sched_script = str(_REPO / "scheduler_service.py")

    spec = scheduler_service_spec(
        command=[python, sched_script],
        port=port,
        startup_grace_seconds=2.0,
    )

    print(f"\n[2] Scheduler service spawn (port {port})...")
    state = sup.start_service(spec)
    record("start_service: status=running", state.status == "running",
           f"status={state.status}, pid={state.pid}")

    if state.status != "running":
        print("  ABORT: service 시작 실패")
        sys.exit(1)

    base_url = f"http://127.0.0.1:{port}"

    # 3. healthz=ok 대기 (startup 콜백 + cron 등록 완료)
    print(f"\n[3] healthz=ok 대기 (startup 콜백 완료까지)...")
    healthz = _poll_healthz_ok(base_url, timeout=25.0)
    record("healthz: status=ok (startup 콜백 완료)", healthz["ok"],
           f"status={healthz['status']} jobs_count={healthz['body'].get('jobs_count', '?')}")

    elapsed_before_load = time.monotonic() - overall_start
    remaining = TOTAL_TIMEOUT_SEC - elapsed_before_load
    print(f"  [INFO] startup 완료까지 {elapsed_before_load:.1f}s 소요, 남은 시간: {remaining:.1f}s")

    # 4. /healthz 50회 동시 GET p95 측정
    print(f"\n[4] /healthz {HEALTHZ_PARALLEL}회 동시 GET p95 측정...")
    latencies: list[float] = []
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=HEALTHZ_PARALLEL) as ex:
        futures = [ex.submit(_get_healthz, base_url, 5.0) for _ in range(HEALTHZ_PARALLEL)]
        for f in concurrent.futures.as_completed(futures):
            lat, code, err = f.result()
            latencies.append(lat)
            if code != 200:
                errors.append(f"code={code} err={err[:40]}")

    latencies.sort()
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    success_count = sum(1 for l in latencies if l < 5000)

    print(f"  p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms")
    print(f"  success={success_count}/{HEALTHZ_PARALLEL}  errors={len(errors)}")

    record(f"p95 < {HEALTHZ_P95_SLA_MS}ms", p95 < HEALTHZ_P95_SLA_MS,
           f"p95={p95:.1f}ms")
    record(f"p99 측정값", True,  # 참고용 기록
           f"p99={p99:.1f}ms")
    record(f"동시 {HEALTHZ_PARALLEL}회 오류 0건", len(errors) == 0,
           f"errors={len(errors)}" + (f" samples={errors[:3]}" if errors else ""))

    # 5. database is locked 로그 grep
    print("\n[5] 'database is locked' 로그 grep...")
    stderr_log = tmp_run / "logs" / "services" / "scheduler.stderr.log"
    stdout_log = tmp_run / "logs" / "services" / "scheduler.stdout.log"
    sched_app_log = (
        Path(os.environ.get("WHATUDOIN_RUN_DIR", str(_REPO)))
        / "logs" / "services" / "scheduler.app.log"
    )

    lock_count = 0
    checked_files: list[str] = []

    for log_path in [stderr_log, stdout_log, sched_app_log]:
        if log_path.exists():
            checked_files.append(str(log_path))
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
                count = content.lower().count("database is locked")
                lock_count += count
                if count > 0:
                    print(f"  [WARN] {log_path.name}: 'database is locked' {count}건 발견")
            except Exception as e:
                print(f"  [WARN] {log_path.name} 읽기 실패: {e}")
        else:
            print(f"  [INFO] {log_path.name}: 파일 없음 (skip)")

    record("'database is locked' 발생 0건", lock_count == 0,
           f"count={lock_count}" + (f" checked={checked_files}" if checked_files else " (로그 없음)"))

    # 6. stop_all
    print("\n[6] Supervisor stop_all()...")
    sup.stop_all(timeout=5.0)
    state_after = sup.services.get("scheduler")
    record("stop_all: service stopped",
           state_after is not None and state_after.status == "stopped",
           f"status={state_after.status if state_after else 'N/A'}")

    total_elapsed = time.monotonic() - overall_start
    record(f"전체 실행 {TOTAL_TIMEOUT_SEC}s 이내 완료", total_elapsed < TOTAL_TIMEOUT_SEC,
           f"elapsed={total_elapsed:.1f}s")

    # 결과 요약
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n=== 결과 요약: {passed}/{total} PASS ===")

    # Markdown 저장
    md_path = run_dir / "general_api_p95.md"
    lines = [
        "# M3-4 General API p95 + Database Locked 0건 Probe",
        "",
        f"**실행 시각**: {run_ts}",
        f"**Scheduler port**: {port}",
        f"**총 소요 시간**: {total_elapsed:.1f}s",
        "",
        "## 부하 설정",
        "",
        f"- 대상: Scheduler service /healthz (in-memory, DB I/O 없음)",
        f"- 동시 연결: {HEALTHZ_PARALLEL}개",
        f"- SLA: p95 < {HEALTHZ_P95_SLA_MS}ms",
        "",
        "## 측정 결과",
        "",
        f"| 지표 | 값 |",
        "|---|---|",
        f"| p50 | {p50:.1f}ms |",
        f"| p95 | {p95:.1f}ms |",
        f"| p99 | {p99:.1f}ms |",
        f"| 성공 | {success_count}/{HEALTHZ_PARALLEL} |",
        f"| 오류 | {len(errors)} |",
        f"| database is locked | {lock_count}건 |",
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
        "## 총계",
        "",
        f"**{passed}/{total} PASS**",
    ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n결과 저장: {md_path}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
