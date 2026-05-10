"""M2-20 라이브 Supervisor 통합 probe.

1. WhatUdoinSupervisor 인스턴스 생성 → ensure_internal_token() → sse_service_spec()
2. start_service()로 subprocess.Popen으로 sse_service.py 실제 실행
3. /healthz 폴링 → 200 + status=ok 단언
4. /internal/publish 3가지 토큰 시나리오 단언:
   - 토큰 없음 → 401
   - Authorization: Bearer wrong → 401
   - Authorization: Bearer <correct> + JSON {event, data} → 200 ok=True
5. supervisor.stop_all() + 프로세스 종료 + 토큰 파일 잔존 확인
6. 결과를 _workspace/perf/m2_20_live/runs/<UTC>/live_supervisor_integration.md 저장
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

# 프로젝트 루트 추가
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from supervisor import WhatUdoinSupervisor, sse_service_spec  # noqa: E402


def _free_port() -> int:
    """사용 가능한 로컬 포트 1개 반환."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_post(url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    """urllib로 POST 요청. HTTPError도 status code 반환."""
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


def _poll_healthz(url: str, timeout: float = 10.0, interval: float = 0.3) -> dict:
    """/healthz가 ok=True 올 때까지 폴링. timeout 초 후 마지막 결과 반환."""
    deadline = time.monotonic() + timeout
    last = {"ok": False, "status": None, "error": "timeout"}
    while time.monotonic() < deadline:
        req = urllib.request.Request(url.rstrip("/") + "/healthz", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read(4096))
                    last = {"ok": data.get("status") == "ok", "status": data.get("status"), "error": ""}
                    if last["ok"]:
                        return last
                else:
                    last = {"ok": False, "status": None, "error": f"http {resp.status}"}
        except Exception as exc:
            last = {"ok": False, "status": None, "error": str(exc)}
        time.sleep(interval)
    return last


def main():
    run_ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = _REPO / "_workspace" / "perf" / "m2_20_live" / "runs" / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    tmp_run = run_dir / "supervisor_run"
    tmp_run.mkdir(exist_ok=True)

    results: list[dict] = []

    def record(name: str, passed: bool, detail: str = ""):
        results.append({"name": name, "passed": passed, "detail": detail})
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))

    print(f"\n=== M2-20 Live Supervisor Integration Probe ===")
    print(f"  run_dir: {run_dir}")

    # 1. Supervisor 생성 + 토큰 발급
    print("\n[1] Supervisor 초기화...")
    sup = WhatUdoinSupervisor(run_dir=tmp_run)
    token_info = sup.ensure_internal_token()
    record("ensure_internal_token: path exists",
           Path(token_info.path).exists(),
           str(token_info.path))
    token = Path(token_info.path).read_text(encoding="utf-8").strip()
    record("ensure_internal_token: token non-empty", bool(token))

    # 2. SSE service spec 생성
    port = _free_port()
    python = sys.executable
    sse_script = str(_REPO / "sse_service.py")
    record("sse_service.py exists", Path(sse_script).exists(), sse_script)

    spec = sse_service_spec(
        command=[python, sse_script],
        port=port,
        startup_grace_seconds=2.0,
    )
    # supervisor가 token을 spec env에 없더라도 service_env()에서 주입함
    record("sse_service_spec: name=sse", spec.name == "sse")
    record("sse_service_spec: port env set", str(port) in str(spec.env))

    # 3. SSE service 실제 spawn
    print(f"\n[2] SSE service spawn (port {port})...")
    state = sup.start_service(spec)
    record("start_service: status=running", state.status == "running",
           f"status={state.status}, pid={state.pid}")

    # 4. /healthz 폴링
    base_url = f"http://127.0.0.1:{port}"
    print(f"\n[3] /healthz 폴링 ({base_url})...")
    healthz = _poll_healthz(base_url, timeout=12.0)
    record("healthz: ok=True", healthz["ok"],
           f"status={healthz['status']} error={healthz['error']}")

    if not healthz["ok"]:
        print("  WARNING: healthz failed — 토큰 검증 단계를 건너뜁니다.")
        # 그래도 계속 진행 (결과에는 FAIL로 기록됨)

    # 5. /internal/publish 시나리오 3종
    publish_url = f"{base_url}/internal/publish"
    payload = json.dumps({"event": "test", "data": {"msg": "m2_20_live"}}).encode()
    ct_headers = {"Content-Type": "application/json"}

    print(f"\n[4] /internal/publish 시나리오 3종...")

    # 5a. 토큰 없음 → 401
    status_a, body_a = _http_post(publish_url, payload, ct_headers)
    record("publish no-token → 401", status_a == 401,
           f"got {status_a}, body={body_a[:80].decode(errors='replace')}")

    # 5b. 잘못된 토큰 → 401
    bad_headers = {**ct_headers, "Authorization": "Bearer wrong_token_xyz"}
    status_b, body_b = _http_post(publish_url, payload, bad_headers)
    record("publish wrong-token → 401", status_b == 401,
           f"got {status_b}, body={body_b[:80].decode(errors='replace')}")

    # 5c. 정상 토큰 → 200 ok=True
    good_headers = {**ct_headers, "Authorization": f"Bearer {token}"}
    status_c, body_c = _http_post(publish_url, payload, good_headers)
    try:
        resp_data = json.loads(body_c)
        ok_val = resp_data.get("ok") is True
    except Exception:
        ok_val = False
    record("publish correct-token → 200", status_c == 200,
           f"got {status_c}")
    record("publish correct-token → ok=True in body", ok_val,
           f"body={body_c[:80].decode(errors='replace')}")

    # 6. stop_all() + 상태 확인
    print(f"\n[5] Supervisor stop_all()...")
    sup.stop_all(timeout=5.0)
    state_after = sup.services.get("sse")
    record("stop_all: service status=stopped",
           state_after is not None and state_after.status == "stopped",
           f"status={state_after.status if state_after else 'N/A'}")

    # 프로세스 완전 종료 확인
    proc_dead = state_after is None or state_after._process is None or state_after._process.poll() is not None
    record("stop_all: process terminated", proc_dead,
           f"poll={state_after._process.poll() if state_after and state_after._process else 'N/A'}")

    # 토큰 파일 잔존 (삭제 없음 — supervisor는 stop_all에서 파일을 지우지 않는다)
    token_still_exists = Path(token_info.path).exists()
    record("token file: still readable after stop_all", token_still_exists,
           str(token_info.path))

    # 7. 결과 요약
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n=== 결과 요약: {passed}/{total} PASS ===")

    # 8. Markdown 저장
    md_path = run_dir / "live_supervisor_integration.md"
    lines = [
        f"# M2-20 Live Supervisor Integration Probe",
        f"",
        f"**실행 시각**: {run_ts}",
        f"**Python**: {sys.executable}",
        f"**SSE port**: {port}",
        f"**run_dir**: {tmp_run}",
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
        f"**{passed}/{total} PASS**",
        f"",
        f"## 토큰 검증 시나리오",
        f"",
        f"| 시나리오 | 기대 | 실제 |",
        f"|---|---|---|",
        f"| Authorization 헤더 없음 | 401 | {status_a} |",
        f"| Authorization: Bearer wrong | 401 | {status_b} |",
        f"| Authorization: Bearer <correct> | 200 | {status_c} |",
    ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n결과 저장: {md_path}")

    # 비정상 종료 코드
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
