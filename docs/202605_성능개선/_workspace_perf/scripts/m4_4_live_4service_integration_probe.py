"""M4-4 라이브 4 service 통합 probe.

시나리오:
  1. WhatUdoinSupervisor 인스턴스 생성 → ensure_internal_token()
  2. 4 service spec 생성 (각 free port):
     - ollama_service.py (라이브 spawn, /healthz 검증)
     - sse_service.py    (라이브 spawn, /healthz 검증)
     - scheduler_service.py (라이브 spawn, /healthz 검증)
     - web_api (mock command: python -c "import time; time.sleep(60)")
  3. 각 service probe_healthz PASS (sse/scheduler/ollama 3종)
     web_api는 mock command이므로 spawn 검증만.
  4. supervisor.STOP_ORDER 따라 stop_all() → 4 service 모두 status=stopped
     종료 시간 cap 10초 단언 (1.5s grace × N + alpha)
  5. 결과 markdown 저장

실행:
    python _workspace/perf/scripts/m4_4_live_4service_integration_probe.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from supervisor import (
    WhatUdoinSupervisor,
    ollama_service_spec,
    sse_service_spec,
    scheduler_service_spec,
    web_api_service_spec,
    OLLAMA_SERVICE_NAME,
    SSE_SERVICE_NAME,
    SCHEDULER_SERVICE_NAME,
    WEB_API_SERVICE_NAME,
    STOP_ORDER,
)

_pass = 0
_fail = 0
_results: list[dict] = []


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    _results.append({"name": name, "passed": cond, "detail": detail})
    if cond:
        _pass += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


def _poll_healthz_ok(base_url: str, timeout: float = 20.0) -> dict:
    """GET /healthz 폴링 → status in {ok, starting} 반환."""
    import urllib.request
    deadline = time.monotonic() + timeout
    last: dict = {"ok": False, "status": None, "error": "timeout", "body": {}}
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(base_url.rstrip("/") + "/healthz", method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read(8192))
                    svc_status = data.get("status")
                    ok = svc_status in ("ok", "starting", "degraded")  # degraded도 살아있음 인정
                    last = {"ok": ok, "status": svc_status, "error": "", "body": data}
                    if ok:
                        return last
                else:
                    last = {"ok": False, "status": None, "error": f"http {resp.status}", "body": {}}
        except Exception as exc:
            last = {"ok": False, "status": None, "error": str(exc), "body": {}}
        time.sleep(0.3)
    return last


def main() -> int:
    utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "_workspace" / "perf" / "m4_4_live" / "runs" / utc_stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_run = out_dir / "supervisor_run"
    tmp_run.mkdir(exist_ok=True)

    python = sys.executable

    print("\n=== M4-4 Live 4 Service Integration Probe ===")
    print(f"  run_dir : {out_dir}")
    print(f"  STOP_ORDER: {STOP_ORDER}")

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Supervisor 초기화
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[1] Supervisor 초기화 + ensure_internal_token...")
    sup = WhatUdoinSupervisor(run_dir=tmp_run)
    tok_info = sup.ensure_internal_token()
    _ok("ensure_internal_token: path exists", Path(tok_info.path).exists())

    # ──────────────────────────────────────────────────────────────────────────
    # 2. 4 service spec 생성
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[2] Service spec 생성 (각 free port)...")
    ollama_port = _free_port()
    sse_port = _free_port()
    sched_port = _free_port()
    # web_api는 mock command
    web_api_port = _free_port()  # 실제 바인드 없음 (mock command)

    spec_ollama = ollama_service_spec(
        command=[python, str(ROOT / "ollama_service.py")],
        port=ollama_port,
        startup_grace_seconds=2.0,
    )
    spec_sse = sse_service_spec(
        command=[python, str(ROOT / "sse_service.py")],
        port=sse_port,
        startup_grace_seconds=2.0,
    )
    spec_sched = scheduler_service_spec(
        command=[python, str(ROOT / "scheduler_service.py")],
        port=sched_port,
        startup_grace_seconds=2.0,
    )
    # web_api: mock command — 환경 의존도가 높으므로 sleep mock
    spec_webapi = web_api_service_spec(
        command=[python, "-c", "import time; time.sleep(60)"],
        name=WEB_API_SERVICE_NAME,
        startup_grace_seconds=1.0,
    )

    _ok("ollama_service_spec: name=ollama", spec_ollama.name == OLLAMA_SERVICE_NAME)
    _ok("sse_service_spec: name=sse", spec_sse.name == SSE_SERVICE_NAME)
    _ok("scheduler_service_spec: name=scheduler", spec_sched.name == SCHEDULER_SERVICE_NAME)
    _ok("web_api_service_spec: name=web-api", spec_webapi.name == WEB_API_SERVICE_NAME)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. 4 service spawn
    # ──────────────────────────────────────────────────────────────────────────
    print(f"\n[3] 4 service spawn...")
    print(f"  ollama  port={ollama_port}")
    print(f"  sse     port={sse_port}")
    print(f"  sched   port={sched_port}")
    print(f"  web_api  mock command")

    st_ollama = sup.start_service(spec_ollama)
    st_sse = sup.start_service(spec_sse)
    st_sched = sup.start_service(spec_sched)
    st_webapi = sup.start_service(spec_webapi)

    _ok("ollama spawn: status not failed",
        st_ollama.status not in ("failed_startup", "degraded"),
        f"status={st_ollama.status}")
    _ok("sse spawn: status not failed",
        st_sse.status not in ("failed_startup", "degraded"),
        f"status={st_sse.status}")
    _ok("sched spawn: status not failed",
        st_sched.status not in ("failed_startup", "degraded"),
        f"status={st_sched.status}")
    _ok("web_api mock spawn: status not failed",
        st_webapi.status not in ("failed_startup", "degraded"),
        f"status={st_webapi.status}")

    _LIVE_OLLAMA = st_ollama.status not in ("failed_startup", "degraded")
    _LIVE_SSE = st_sse.status not in ("failed_startup", "degraded")
    _LIVE_SCHED = st_sched.status not in ("failed_startup", "degraded")
    _LIVE_WEBAPI = st_webapi.status not in ("failed_startup", "degraded")

    # ──────────────────────────────────────────────────────────────────────────
    # 4. probe_healthz 3종 (sse/scheduler/ollama)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[4] probe_healthz 검증 (sse/scheduler/ollama 3종)...")

    def _probe_healthz_svc(name: str, port: int, is_live: bool) -> bool:
        if not is_live:
            _ok(f"{name} probe_healthz (spawn 실패 skip)", False, "spawn failed")
            return False
        open_ok = _wait_port_open("127.0.0.1", port, timeout=15.0)
        _ok(f"{name} 포트 open 대기", open_ok, f"port={port}")
        if not open_ok:
            return False
        h = _poll_healthz_ok(f"http://127.0.0.1:{port}", timeout=20.0)
        _ok(f"{name} probe_healthz PASS",
            h["ok"],
            f"status={h['status']} error={h['error']}")
        return h["ok"]

    _h_ollama = _probe_healthz_svc("ollama", ollama_port, _LIVE_OLLAMA)
    _h_sse = _probe_healthz_svc("sse", sse_port, _LIVE_SSE)
    _h_sched = _probe_healthz_svc("sched", sched_port, _LIVE_SCHED)

    # web_api mock spawn 검증 — probe_healthz 없음
    if _LIVE_WEBAPI:
        # mock은 HTTP 서버가 없으므로 프로세스가 살아있는지만 확인
        proc_alive = (st_webapi._process is not None and
                      st_webapi._process.poll() is None)
        _ok("web_api mock: process alive", proc_alive,
            f"pid={st_webapi.pid}")
    else:
        _ok("web_api mock: spawn skip", False, "spawn failed")

    # ──────────────────────────────────────────────────────────────────────────
    # 5. STOP_ORDER 따라 stop_all() → 4 service 모두 status=stopped
    # ──────────────────────────────────────────────────────────────────────────
    print(f"\n[5] stop_all() — STOP_ORDER={STOP_ORDER}...")
    t0_stop = time.monotonic()
    sup.stop_all(timeout=5.0)
    elapsed_stop = time.monotonic() - t0_stop

    # 10초 cap
    _ok("stop_all: 10초 이내 완료", elapsed_stop < 10.0,
        f"elapsed={elapsed_stop:.2f}s")

    # 각 service status=stopped
    for svc_name in [OLLAMA_SERVICE_NAME, SSE_SERVICE_NAME, SCHEDULER_SERVICE_NAME, WEB_API_SERVICE_NAME]:
        st = sup.services.get(svc_name)
        if st is not None:
            _ok(f"{svc_name}: status=stopped", st.status == "stopped",
                f"status={st.status}")
        else:
            _ok(f"{svc_name}: status=stopped (서비스 없음)", False, "not in services")

    # ──────────────────────────────────────────────────────────────────────────
    # 결과 요약 + markdown 저장
    # ──────────────────────────────────────────────────────────────────────────
    md_path = out_dir / "4service_integration.md"
    lines = [
        "# M4-4 Live 4 Service Integration Probe",
        "",
        f"**실행 시각**: {utc_stamp}",
        f"**Python**: {python}",
        f"**STOP_ORDER**: {list(STOP_ORDER)}",
        "",
        "## 서비스 포트 할당",
        "",
        f"| service | port | 모드 |",
        f"|---------|------|------|",
        f"| ollama | {ollama_port} | {'LIVE' if _LIVE_OLLAMA else 'FAIL'} |",
        f"| sse | {sse_port} | {'LIVE' if _LIVE_SSE else 'FAIL'} |",
        f"| scheduler | {sched_port} | {'LIVE' if _LIVE_SCHED else 'FAIL'} |",
        f"| web-api | mock | {'LIVE' if _LIVE_WEBAPI else 'FAIL'} |",
        "",
        "## 결과",
        "",
        "| # | 항목 | 결과 | 상세 |",
        "|---|------|------|------|",
    ]
    for i, r in enumerate(_results, 1):
        status_str = "PASS" if r["passed"] else "FAIL"
        detail = r["detail"].replace("|", "\\|") if r["detail"] else ""
        lines.append(f"| {i} | {r['name']} | {status_str} | {detail} |")

    lines += [
        "",
        "## 종료 시간",
        "",
        f"- stop_all() elapsed: {elapsed_stop:.2f}s",
        f"- cap: 10.0s",
        "",
        "## 총계",
        "",
        f"**{_pass}/{_pass + _fail} PASS**",
    ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=== 결과: {_pass}/{_pass + _fail} PASS ===")
    print(f"  elapsed_stop={elapsed_stop:.2f}s")
    print(f"  결과 저장: {md_path}")

    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
