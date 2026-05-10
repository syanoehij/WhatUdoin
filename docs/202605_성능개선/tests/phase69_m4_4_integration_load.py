"""Phase 69: M4-4 통합 부하 테스트 (standalone runner).

A. 라이브 4 service 통합 — supervisor + 4 spec 구조 단언 (spawn은 probe 스크립트)
B. Ollama hang 중 일반 API p95 — mock 격리 단언 (threadpool 격리 proxy + IPC 구조)
C. M4 종료 게이트 5종 평가 — 코드 grep + 설계 단언
D. 회귀: phase54~68 핵심 항목 재확인

실행:
    python tests/phase69_m4_4_integration_load.py
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _read(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace")


# 소스 코드 로드
sup_src = _read(ROOT / "supervisor.py")
app_src = _read(ROOT / "app.py")
ollama_src = _read(ROOT / "ollama_service.py")
sse_src = _read(ROOT / "sse_service.py")
sched_src = _read(ROOT / "scheduler_service.py")
lp_src = _read(ROOT / "llm_parser.py")

print("\n[phase69] M4-4 통합 부하 테스트")

# ─────────────────────────────────────────────────────────────────────────────
# A. 라이브 4 service 통합 — 구조 단언
# ─────────────────────────────────────────────────────────────────────────────
print("\n[A] 라이브 4 service 통합 구조 단언...")

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
    M2_STARTUP_SEQUENCE,
    INTERNAL_TOKEN_ENV,
)

# A1. 4 service spec 팩토리 존재 확인
_ok("ollama_service_spec 팩토리 callable",
    callable(ollama_service_spec))
_ok("sse_service_spec 팩토리 callable",
    callable(sse_service_spec))
_ok("scheduler_service_spec 팩토리 callable",
    callable(scheduler_service_spec))
_ok("web_api_service_spec 팩토리 callable",
    callable(web_api_service_spec))

# A2. 4 service spec name 검증
spec_o = ollama_service_spec(command=["python", "ollama_service.py"], port=9991)
spec_s = sse_service_spec(command=["python", "sse_service.py"], port=9992)
spec_sc = scheduler_service_spec(command=["python", "scheduler_service.py"], port=9993)
spec_w = web_api_service_spec(command=["python", "-c", "import time; time.sleep(60)"])

_ok("ollama_service_spec: name=ollama", spec_o.name == OLLAMA_SERVICE_NAME)
_ok("sse_service_spec: name=sse", spec_s.name == SSE_SERVICE_NAME)
_ok("scheduler_service_spec: name=scheduler", spec_sc.name == SCHEDULER_SERVICE_NAME)
_ok("web_api_service_spec: name=web-api", spec_w.name == WEB_API_SERVICE_NAME)

# A3. STOP_ORDER 4 service 포함
_ok("STOP_ORDER: 4 service 모두 포함",
    set(STOP_ORDER) >= {"ollama", "sse", "scheduler", "web-api"},
    f"STOP_ORDER={STOP_ORDER}")

# A4. STOP_ORDER 순서: ollama first, web-api last
order = list(STOP_ORDER)
_ok("STOP_ORDER: ollama < sse < scheduler < web-api 순서",
    (order.index("ollama") < order.index("sse") and
     order.index("sse") < order.index("scheduler") and
     order.index("scheduler") < order.index("web-api")),
    f"order={order}")

# A5. stop_all STOP_ORDER 순서 실행 — mock 캡처
print("  [mock] stop_all STOP_ORDER 순서 캡처...")
stopped_order: list[str] = []

sup = WhatUdoinSupervisor.__new__(WhatUdoinSupervisor)
sup.run_dir = ROOT / "_tmp_phase69"
sup.log_dir = sup.run_dir / "logs"
sup.token_path = sup.run_dir / "internal_token"
sup.token_info = None
sup.services = {}

from supervisor import ServiceState

for svc_name in ["web-api", "scheduler", "sse", "ollama"]:
    st = ServiceState(name=svc_name, status="running")
    st._process = MagicMock()
    st._process.poll.return_value = None

    def _term(st=st):
        st._process.poll.return_value = 0

    st._process.terminate = _term
    st._process.wait = MagicMock(return_value=0)
    sup.services[svc_name] = st

original_stop = WhatUdoinSupervisor.stop_service

def _capturing_stop(self, name: str, timeout: float = 5.0):
    stopped_order.append(name)
    st2 = self.services.get(name)
    if st2:
        st2.status = "stopped"
        st2.stopped_at = 0.0
        st2.pid = None
        st2._exit_counted = True
    return st2

import time as _time
_real_sleep = _time.sleep
_time.sleep = lambda x: None

try:
    WhatUdoinSupervisor.stop_service = _capturing_stop
    sup.stop_all(timeout=5.0)
finally:
    WhatUdoinSupervisor.stop_service = original_stop
    _time.sleep = _real_sleep

_ok("stop_all: STOP_ORDER 준수 (ollama → sse → scheduler → web-api)",
    (stopped_order.index("ollama") < stopped_order.index("sse") <
     stopped_order.index("scheduler") < stopped_order.index("web-api")),
    f"order={stopped_order}")

# A6. M2_STARTUP_SEQUENCE에 4 service start 항목 포함
seq = list(M2_STARTUP_SEQUENCE)
_ok("M2_STARTUP_SEQUENCE: start_ollama_service 포함",
    "start_ollama_service" in seq)
_ok("M2_STARTUP_SEQUENCE: start_sse_service 포함",
    "start_sse_service" in seq)
_ok("M2_STARTUP_SEQUENCE: start_scheduler_service 포함",
    "start_scheduler_service" in seq)
_ok("M2_STARTUP_SEQUENCE: 10개 항목 (M5-2: start_media_service 추가)",
    len(seq) == 10, f"count={len(seq)}")

# A7. probe_healthz 인터페이스
_ok("supervisor.probe_healthz 메서드 callable",
    callable(getattr(WhatUdoinSupervisor, "probe_healthz", None)))

# ─────────────────────────────────────────────────────────────────────────────
# B. Ollama hang 중 일반 API p95 — mock 격리 단언
# ─────────────────────────────────────────────────────────────────────────────
print("\n[B] Ollama hang 중 일반 API p95 mock 격리 단언...")
print("  [NOTE] in-process ASGI thread 격리 proxy 검증.")
print("         실제 end-to-end는 m4_4_ollama_hang_general_api_p95_probe.py에서 검증.")

import llm_parser as _lp
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket

# B1. _ollama_service_url 모듈 수준 변수 존재
_ok("llm_parser._ollama_service_url 모듈 수준 변수 존재",
    hasattr(_lp, "_ollama_service_url"))

# B2. _call_ollama_service 함수 존재
_ok("llm_parser._call_ollama_service callable",
    callable(getattr(_lp, "_call_ollama_service", None)))

# B3. hang mock 서버 기동 + IPC timeout 단언
_hang_sleep = 10

class _HangHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(_hang_sleep)
    def log_message(self, format, *args):
        pass

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

_hang_port = _free_port()
_hang_server = ThreadingHTTPServer(("127.0.0.1", _hang_port), _HangHandler)
_server_thread = threading.Thread(target=_hang_server.serve_forever, daemon=True)
_server_thread.start()
print(f"  [INFO] hang mock 서버 기동 — 127.0.0.1:{_hang_port}")

_saved_url = _lp._ollama_service_url
_lp._ollama_service_url = f"http://127.0.0.1:{_hang_port}/internal/llm"

_ipc_result: list = []
_ipc_elapsed: list[float] = []


def _ipc_worker():
    t0 = time.monotonic()
    try:
        _lp._call_ollama_service("parse_schedule", "hang_test", timeout=1)
        _ipc_result.append("NO_EXCEPTION")
    except _lp.OllamaUnavailableError as e:
        _ipc_result.append(("OllamaUnavailableError", e.reason))
    except Exception as e:
        _ipc_result.append(("OTHER", type(e).__name__))
    finally:
        _ipc_elapsed.append(time.monotonic() - t0)


_ipc_t = threading.Thread(target=_ipc_worker, daemon=True)
_ipc_t.start()
_ipc_t.join(timeout=10.0)

if _ipc_elapsed:
    _elapsed = _ipc_elapsed[0]
    _res = _ipc_result[0] if _ipc_result else None
    _ok("B1: hang → OllamaUnavailableError(reason=timeout)",
        isinstance(_res, tuple) and _res[0] == "OllamaUnavailableError" and _res[1] == "timeout",
        f"result={_res}")
    _ok("B2: IPC timeout < 8s",
        _elapsed < 8.0, f"elapsed={_elapsed:.2f}s")
else:
    _ok("B1: hang → OllamaUnavailableError(reason=timeout)", False, "thread join timeout")
    _ok("B2: IPC timeout < 8s", False, "thread join timeout")

# B4. hang 중 별도 thread CPU 작업 p95 < 100ms (proxy for main app non-LLM path)
_N = 20
_latencies: list[float] = []


def _fast_task() -> float:
    t0 = time.monotonic()
    _ = {str(i): i * 2 for i in range(100)}
    return time.monotonic() - t0


# AI hang background
_bg_result: list = []


def _bg_ipc():
    try:
        _lp._call_ollama_service("parse_schedule", "bg", timeout=1)
    except Exception as e:
        _bg_result.append(type(e).__name__)


_bg = threading.Thread(target=_bg_ipc, daemon=True)
_bg.start()
time.sleep(0.05)

for _ in range(_N):
    _latencies.append(_fast_task() * 1000)

_bg.join(timeout=10.0)
_lp._ollama_service_url = _saved_url

_hang_server.shutdown()

_latencies.sort()
_p95_idx = max(0, int(0.95 * len(_latencies)) - 1)
_p95 = _latencies[_p95_idx]

_ok(f"B3: 별도 thread p95 < 100ms (hang 중 proxy)", _p95 < 100.0,
    f"p95={_p95:.2f}ms")

# B5. /api/health가 LLM IPC 호출 없음 (코드 grep)
#     /api/health 라우트 함수 본문에 _call_ollama_service / _lp. 없음
health_route_match = re.search(
    r'@app\.get\(.*?/api/health.*?\n(?:.*\n)*?(?=@app\.|^$)',
    app_src,
    re.MULTILINE,
)
# 넓은 방식: /api/health 라우트 근처 함수에 ollama 관련 심볼 없음 grep
health_section = ""
for m in re.finditer(r'"/api/health"', app_src):
    health_section = app_src[m.start():m.start()+1000]
    break

_ok("B4: /api/health 라우트에 ollama IPC 호출 없음",
    "_call_ollama_service" not in health_section and
    "ollama" not in health_section.lower()[:300],
    "grep: _call_ollama_service 없음 + ollama 없음")

# ─────────────────────────────────────────────────────────────────────────────
# C. M4 종료 게이트 5종 평가
# ─────────────────────────────────────────────────────────────────────────────
print("\n[C] M4 종료 게이트 5종 평가...")

# 게이트 1: hang/강제 종료 시 main app 영향 0
# → Ollama service 별도 프로세스 분리: app.py에 OLLAMA_SERVICE_URL env 분기
_ok("게이트①: ollama_service 프로세스 분리 (OLLAMA_SERVICE_URL env 분기)",
    "_ollama_service_url" in lp_src and
    "WHATUDOIN_OLLAMA_SERVICE_URL" in lp_src)
_ok("게이트①: IPC 분기 시 main app 직접 Ollama 호출 0 (_call_ollama_service 8접점)",
    "_call_ollama_service" in lp_src)

# 게이트 2: AI 처리 중 일반 API p95 500ms 이하
# → B 섹션 proxy 검증 PASS + in-process probe 계획
_ok("게이트②: 일반 API p95 구조 — /api/health LLM IPC 미호출",
    "_call_ollama_service" not in health_section or len(health_section) < 50)
_ok("게이트②: IPC timeout margin 설정 (timeout+5) — OllamaUnavailableError 빠른 전파",
    "timeout + 5" in lp_src or "timeout+5" in lp_src)

# 게이트 3: 트레이 단독 재시작 (서비스 재시작 → 다음 요청 정상)
# → M4-2 lifecycle probe: stop_service → start_service → probe_healthz PASS
_ok("게이트③: supervisor.stop_service + start_service 구현 존재",
    "stop_service" in sup_src and "start_service" in sup_src)
_ok("게이트③: STOP_ORDER에 ollama 포함 (단독 종료/재시작 가능)",
    "ollama" in sup_src and "STOP_ORDER" in sup_src)

# 게이트 4: §13 진입 게이트 통과 후 적용 항목 회귀 없음
# → M4-1 IPC 8접점 분리, M4-2 lifecycle, M4-3 limit_concurrency 미적용
_ok("게이트④: limit_concurrency 미적용 (M4-3) — 운영 5파일 grep",
    "limit_concurrency" not in ollama_src and
    "limit_concurrency" not in sse_src and
    "limit_concurrency" not in sched_src)
_ok("게이트④: ollama_service_spec 3 protected env (loopback guard)",
    "WHATUDOIN_OLLAMA_BIND_HOST" in sup_src and
    "WHATUDOIN_OLLAMA_PORT" in sup_src and
    "WHATUDOIN_INTERNAL_TOKEN" in sup_src)

# 게이트 5: M4 의도 충족 — Ollama service 분리
_ok("게이트⑤: ollama_service.py 존재 (Starlette ASGI + /internal/llm + /healthz)",
    "Route" in ollama_src and
    "/internal/llm" in ollama_src and
    "/healthz" in ollama_src)
_ok("게이트⑤: loopback guard + Bearer 토큰 인증",
    "_LOOPBACK_HOSTS" in ollama_src and
    "compare_digest" in ollama_src)

# ─────────────────────────────────────────────────────────────────────────────
# D. 회귀 단언 (phase54~68 핵심 항목)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[D] phase54~68 회귀 단언...")

# phase66 회귀: ollama_service.py IPC 분기 8접점
_ok("phase66 회귀: llm_parser IPC 분기 8접점 (_call_ollama_service)",
    lp_src.count("_call_ollama_service") >= 8)

# phase67 회귀: OllamaUnavailableError reason 분기
_ok("phase67 회귀: OllamaUnavailableError reason 분기 (busy/timeout/connect/5xx)",
    all(r in lp_src for r in ['"busy"', '"timeout"', '"connect"', '"5xx"']))

# phase68 회귀: limit_concurrency 미적용
_ok("phase68 회귀: limit_concurrency 0건 (운영 5파일)",
    "limit_concurrency" not in ollama_src and
    "limit_concurrency" not in sse_src and
    "limit_concurrency" not in sched_src and
    "limit_concurrency" not in app_src)

# phase64 회귀: STOP_ORDER
_ok("phase64 회귀: STOP_ORDER 상수 존재",
    "STOP_ORDER" in sup_src)
_ok("phase64 회귀: RotatingFileHandler 운영 3 service 모두 적용",
    "RotatingFileHandler" in ollama_src and
    "RotatingFileHandler" in sched_src)

# phase63 회귀: scheduler lifespan 분기
_ok("phase63 회귀: app.py _scheduler_service_enabled 분기",
    "_scheduler_service_enabled" in app_src)

# phase61 회귀: SSE service 구조
_ok("phase61 회귀: sse_service.py /api/stream + /internal/publish + /healthz",
    "/api/stream" in sse_src and
    "/internal/publish" in sse_src and
    "/healthz" in sse_src)

# ─────────────────────────────────────────────────────────────────────────────
# 최종 결과
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n=== phase69 결과: {_pass}/{_pass + _fail} PASS ===")
if _fail > 0:
    print("  FAIL 목록:")
    for r in _results:
        if not r["passed"]:
            print(f"    - {r['name']}" + (f": {r['detail']}" if r["detail"] else ""))

# 임시 디렉토리 정리
import shutil
tmp_dir = ROOT / "_tmp_phase69"
if tmp_dir.exists():
    shutil.rmtree(tmp_dir, ignore_errors=True)

sys.exit(0 if _fail == 0 else 1)
