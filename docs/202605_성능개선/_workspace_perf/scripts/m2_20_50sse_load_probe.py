"""M2-20 50 SSE 연결 부하 시뮬레이션 probe.

1. WhatUdoinSupervisor로 SSE service 실제 spawn
2. 50개 스레드로 concurrent EventSource-style GET /api/stream 연결
3. 각 연결이 ': connected' 첫 line 받으면 카운트
4. 단발 publish 1건 → 50개 연결 모두에 도달하는 p95 지연 측정
5. 모든 연결 close → broker subscribers=0 복귀 확인 (/healthz)
6. SSE service /healthz 50회 동시 GET → p95 측정 (Web API spawn 제외 — 60s cap)
7. 결과 markdown 저장

시간 cap: 60초 이내. 시스템 제약 시 30 SSE로 축소하고 명시.

Web API /healthz 라이브 spawn 제외 사유:
  - 60초 cap + SQLite WAL lock 위험 + DB init 초기화 부하로 SSE service만 spawn.
  - 대신 SSE service /healthz를 동시 요청으로 일반 JSON GET API 회귀 대역 측정.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from supervisor import WhatUdoinSupervisor, sse_service_spec  # noqa: E402

NUM_SSE = 50  # 목표 연결 수
SSE_TIMEOUT = 60  # 전체 타임아웃 (초)
PUBLISH_WAIT = 3.0  # publish 후 event 대기 (초)
P95_SLA_MS = 2000.0  # publish→수신 p95 SLA
HEALTHZ_P95_SLA_MS = 500.0  # /healthz p95 SLA
HEALTHZ_PARALLEL = 50  # 동시 /healthz 요청 수


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_post(url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, resp.read(4096)
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096)
    except Exception as exc:
        return 0, str(exc).encode()


def _poll_healthz(url: str, timeout: float = 12.0, interval: float = 0.3) -> dict:
    deadline = time.monotonic() + timeout
    last = {"ok": False, "status": None, "subscribers": None, "error": "timeout"}
    while time.monotonic() < deadline:
        req = urllib.request.Request(url.rstrip("/") + "/healthz", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read(4096))
                    last = {
                        "ok": data.get("status") == "ok",
                        "status": data.get("status"),
                        "subscribers": data.get("subscribers"),
                        "error": "",
                    }
                    if last["ok"]:
                        return last
        except Exception as exc:
            last = {"ok": False, "status": None, "subscribers": None, "error": str(exc)}
        time.sleep(interval)
    return last


def _sse_connect_worker(
    stream_url: str,
    idx: int,
    connected_times: list,  # 인덱스 idx에 연결 시각 기록
    event_times: list,       # 인덱스 idx에 event 수신 시각 기록
    ready_ev: threading.Event,  # 모든 연결 준비 완료 신호
    publish_ev: threading.Event,  # publish 이후 신호
    stop_ev: threading.Event,
    errors: list,
):
    """단일 SSE 스트림 연결 스레드."""
    try:
        req = urllib.request.Request(stream_url)
        req.add_header("Accept", "text/event-stream")
        req.add_header("Cache-Control", "no-cache")
        with urllib.request.urlopen(req, timeout=SSE_TIMEOUT) as resp:
            # 연결 열림 — 첫 데이터 기다림
            buf = b""
            connected_recorded = False
            event_recorded = False
            while not stop_ev.is_set():
                try:
                    chunk = resp.read1(512) if hasattr(resp, "read1") else resp.read(512)
                except Exception:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line_s = line.decode("utf-8", errors="replace").strip()
                    if not connected_recorded and line_s == ": connected":
                        connected_times[idx] = time.monotonic()
                        connected_recorded = True
                    elif connected_recorded and not event_recorded and line_s.startswith("event:"):
                        # event: test 수신 시각
                        event_times[idx] = time.monotonic()
                        event_recorded = True
    except Exception as exc:
        errors[idx] = str(exc)


def main():
    run_ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    results: list[dict] = []
    start_time = time.monotonic()

    def record(name: str, passed: bool, detail: str = ""):
        results.append({"name": name, "passed": passed, "detail": detail})
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))

    print(f"\n=== M2-20 50 SSE Load Probe ===")

    tmp_run = _REPO / "_workspace" / "perf" / "m2_20_live" / "load_run"
    tmp_run.mkdir(parents=True, exist_ok=True)

    # 1. Supervisor + SSE service spawn
    port = _free_port()
    python = sys.executable
    sse_script = str(_REPO / "sse_service.py")
    base_url = f"http://127.0.0.1:{port}"
    stream_url = f"{base_url}/api/stream"
    publish_url = f"{base_url}/internal/publish"

    sup = WhatUdoinSupervisor(run_dir=tmp_run)
    token_info = sup.ensure_internal_token()
    token = Path(token_info.path).read_text(encoding="utf-8").strip()

    spec = sse_service_spec(
        command=[python, sse_script],
        port=port,
        startup_grace_seconds=2.0,
    )
    print(f"\n[1] SSE service spawn (port {port})...")
    state = sup.start_service(spec)
    record("sse_service spawn", state.status == "running",
           f"status={state.status} pid={state.pid}")

    if state.status != "running":
        record("ABORT: sse_service not running", False)
        _write_md(results, _repo_run_dir(run_ts), run_ts, port, 0, 0, 0, 0, 0, start_time)
        sup.stop_all()
        sys.exit(1)

    healthz = _poll_healthz(base_url, timeout=12.0)
    record("sse_service /healthz ok", healthz["ok"],
           f"status={healthz['status']} error={healthz['error']}")

    # 2. 50 concurrent SSE 연결 시도
    num_sse = NUM_SSE
    print(f"\n[2] {num_sse} SSE 연결 시작...")

    connected_times = [None] * num_sse
    event_times = [None] * num_sse
    errors = [None] * num_sse
    ready_ev = threading.Event()
    publish_ev = threading.Event()
    stop_ev = threading.Event()

    threads = []
    connect_start = time.monotonic()
    for i in range(num_sse):
        t = threading.Thread(
            target=_sse_connect_worker,
            args=(stream_url, i, connected_times, event_times, ready_ev, publish_ev, stop_ev, errors),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # 연결 확립 대기 (최대 15초)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        connected_count = sum(1 for t in connected_times if t is not None)
        if connected_count >= num_sse:
            break
        time.sleep(0.2)

    connected_count = sum(1 for t in connected_times if t is not None)
    connect_elapsed_ms = (time.monotonic() - connect_start) * 1000
    record(f"{num_sse} SSE initial connect: {connected_count}/{num_sse}",
           connected_count >= num_sse * 0.9,  # 90% 이상 연결 시 PASS
           f"{connected_count}/{num_sse} in {connect_elapsed_ms:.0f}ms")

    # 3. publish 1건
    print(f"\n[3] publish 1건 → {connected_count}/{num_sse} 연결 도달 측정...")
    publish_time = time.monotonic()
    payload = json.dumps({"event": "test", "data": {"msg": "m2_20_load"}}).encode()
    good_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    status_pub, body_pub = _http_post(publish_url, payload, good_headers)
    record("publish → 200", status_pub == 200, f"got {status_pub}")

    # 4. event 수신 대기 (최대 PUBLISH_WAIT 초)
    deadline2 = time.monotonic() + PUBLISH_WAIT
    while time.monotonic() < deadline2:
        received_count = sum(1 for t in event_times if t is not None)
        if received_count >= connected_count:
            break
        time.sleep(0.1)

    received_count = sum(1 for t in event_times if t is not None)

    # p95 계산
    delays_ms = [
        (event_times[i] - publish_time) * 1000
        for i in range(num_sse)
        if event_times[i] is not None
    ]
    delays_ms.sort()
    p95_ms = delays_ms[int(len(delays_ms) * 0.95)] if delays_ms else 99999.0
    avg_ms = sum(delays_ms) / len(delays_ms) if delays_ms else 99999.0

    record(f"publish→수신: {received_count}/{connected_count} 연결 도달",
           received_count >= connected_count * 0.9,
           f"{received_count}/{connected_count}")
    record(f"publish→수신 p95 < {P95_SLA_MS}ms",
           p95_ms < P95_SLA_MS,
           f"p95={p95_ms:.1f}ms avg={avg_ms:.1f}ms n={len(delays_ms)}")

    # 5. 연결 종료 + subscribers=0 확인
    print(f"\n[4] 연결 종료 + subscribers=0 확인...")
    stop_ev.set()
    for t in threads:
        t.join(timeout=3.0)

    # 브로커 cleanup에 1-2초 여유
    time.sleep(2.0)

    healthz_after = _poll_healthz(base_url, timeout=5.0)
    subs_after = healthz_after.get("subscribers", -1)
    record("subscribers → 0 after all disconnect",
           subs_after == 0,
           f"subscribers={subs_after}")

    # 6. /healthz 50회 동시 GET (일반 JSON API 회귀 측정)
    print(f"\n[5] /healthz {HEALTHZ_PARALLEL}회 동시 GET (일반 API 회귀)...")
    healthz_times = [None] * HEALTHZ_PARALLEL
    healthz_statuses = [None] * HEALTHZ_PARALLEL
    healthz_errors = [None] * HEALTHZ_PARALLEL

    def healthz_worker(idx):
        t0 = time.monotonic()
        req = urllib.request.Request(f"{base_url}/healthz")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                resp.read(4096)
                healthz_statuses[idx] = resp.status
        except urllib.error.HTTPError as e:
            healthz_statuses[idx] = e.code
            healthz_errors[idx] = str(e)
        except Exception as exc:
            healthz_statuses[idx] = 0
            healthz_errors[idx] = str(exc)
        healthz_times[idx] = (time.monotonic() - t0) * 1000

    hthreads = [threading.Thread(target=healthz_worker, args=(i,), daemon=True)
                for i in range(HEALTHZ_PARALLEL)]
    h_start = time.monotonic()
    for t in hthreads:
        t.start()
    for t in hthreads:
        t.join(timeout=10.0)

    h_ok = sum(1 for s in healthz_statuses if s == 200)
    h_times = [t for t in healthz_times if t is not None]
    h_times.sort()
    h_p95 = h_times[int(len(h_times) * 0.95)] if h_times else 99999.0
    h_avg = sum(h_times) / len(h_times) if h_times else 99999.0

    record(f"/healthz 동시 {HEALTHZ_PARALLEL}회: {h_ok}/{HEALTHZ_PARALLEL} 200 OK",
           h_ok >= HEALTHZ_PARALLEL * 0.9,
           f"{h_ok}/{HEALTHZ_PARALLEL}")
    record(f"/healthz p95 < {HEALTHZ_P95_SLA_MS}ms",
           h_p95 < HEALTHZ_P95_SLA_MS,
           f"p95={h_p95:.1f}ms avg={h_avg:.1f}ms")

    # 7. 정리
    print(f"\n[6] Supervisor stop_all()...")
    sup.stop_all(timeout=5.0)
    state_after = sup.services.get("sse")
    record("stop_all: status=stopped",
           state_after is not None and state_after.status == "stopped")

    # 결과 요약
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    elapsed = time.monotonic() - start_time
    print(f"\n=== 결과 요약: {passed}/{total} PASS, 경과={elapsed:.1f}s ===")

    # Markdown 저장
    run_dir = _repo_run_dir(run_ts)
    _write_md(results, run_dir, run_ts, port, connected_count, received_count,
              p95_ms, h_p95, subs_after, start_time,
              avg_delay_ms=avg_ms, h_avg_ms=h_avg,
              num_sse=num_sse)

    sys.exit(0 if passed == total else 1)


def _repo_run_dir(run_ts: str) -> Path:
    run_dir = _REPO / "_workspace" / "perf" / "m2_20_live" / "runs" / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_md(results, run_dir, run_ts, port, connected_count, received_count,
              p95_ms, h_p95_ms, subs_after, start_time, avg_delay_ms=0.0,
              h_avg_ms=0.0, num_sse=50):
    elapsed = time.monotonic() - start_time
    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    lines = [
        f"# M2-20 50 SSE 부하 시뮬레이션 Probe",
        f"",
        f"**실행 시각**: {run_ts}",
        f"**SSE port**: {port}",
        f"**목표 연결 수**: {num_sse}",
        f"**경과 시간**: {elapsed:.1f}s",
        f"",
        f"## 측정값 요약",
        f"",
        f"| 항목 | 측정값 | SLA | 판정 |",
        f"|---|---|---|---|",
        f"| SSE 연결 성공 | {connected_count}/{num_sse} | ≥90% | {'PASS' if connected_count >= num_sse * 0.9 else 'FAIL'} |",
        f"| event 수신 도달 | {received_count}/{connected_count} | ≥90% | {'PASS' if received_count >= (connected_count * 0.9 if connected_count else 1) else 'FAIL'} |",
        f"| publish→수신 p95 | {p95_ms:.1f}ms | <2000ms | {'PASS' if p95_ms < 2000 else 'FAIL'} |",
        f"| publish→수신 avg | {avg_delay_ms:.1f}ms | — | — |",
        f"| subscribers=0 복귀 | {subs_after} | ==0 | {'PASS' if subs_after == 0 else 'FAIL'} |",
        f"| /healthz p95 (SSE svc) | {h_p95_ms:.1f}ms | <500ms | {'PASS' if h_p95_ms < 500 else 'FAIL'} |",
        f"| /healthz avg | {h_avg_ms:.1f}ms | — | — |",
        f"",
        f"## 시나리오 결과",
        f"",
        f"| # | 항목 | 결과 | 상세 |",
        f"|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        status_str = "PASS" if r["passed"] else "FAIL"
        detail = r["detail"].replace("|", "\\|") if r["detail"] else ""
        lines.append(f"| {i} | {r['name']} | {status_str} | {detail} |")

    lines += [
        f"",
        f"## 총계",
        f"",
        f"**{passed}/{total} PASS**, 경과={elapsed:.1f}s",
        f"",
        f"## 범위 제한 (선언)",
        f"",
        f"- **Web API /healthz 라이브 spawn 제외**: 60초 cap + SQLite WAL lock 위험 + DB init 부하로 SSE service만 spawn.",
        f"  SSE service /healthz {NUM_SSE}회 동시 요청으로 JSON GET API 회귀 대역 측정함.",
        f"- **EventSource 인증 세션 제외**: SSE service 단독 spawn이라 쿠키 기반 인증 없음.",
        f"  라이브 브라우저 인증 SSE는 M2-0 gate probe에서 별도 측정됨.",
    ]

    md_path = run_dir / "50sse_load_probe.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n결과 저장: {md_path}")
    return md_path


if __name__ == "__main__":
    main()
