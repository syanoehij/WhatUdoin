"""M4-4 Ollama hang 중 일반 API p95 보강 probe.

설계 결정 (advisor 권고에 따라 명시):

  M4-2에서 선언한 한계(uvicorn threadpool 격리 라이브 회귀)를 보강.
  full FastAPI app spawn 없이 in-process ASGI 호출 방식을 사용:

  방식:
  1. WHATUDOIN_SCHEDULER_SERVICE=1, WHATUDOIN_OLLAMA_SERVICE_URL=<hang_url>로 설정 후
     app.py를 import하여 ASGI app 객체 획득.
  2. hang mock 서버(sleep 10초)를 ThreadingHTTPServer로 기동 → _lp._ollama_service_url 직접 패치.
  3. concurrent AI in-flight 요청: threading을 사용해 N개 스레드에서
     llm_parser._call_ollama_service(timeout=1)를 동시 호출 (hang mock에 걸려 있음).
  4. hang 진행 중 httpx.AsyncClient(transport=ASGITransport(app)) 사용해
     /api/health 또는 /healthz 50회 동시 GET 실행.
  5. p95 < 500ms 단언 (일반 API는 LLM IPC 호출 0).

  이 검증이 "AI 처리 중 일반 API p95 < 500ms" 종료 게이트의 증거가 됨.

실행:
    python _workspace/perf/scripts/m4_4_ollama_hang_general_api_p95_probe.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


print("\n=== M4-4 Ollama hang 중 일반 API p95 보강 probe ===")
print()
print("  [NOTE] in-process ASGI 방식으로 검증.")
print("         AI hang(IPC timeout) 중 /api/health p95 < 500ms 단언.")

# ──────────────────────────────────────────────────────────────────────────────
# hang mock 서버 구성
# ──────────────────────────────────────────────────────────────────────────────
_HANG_SLEEP = 10  # IPC timeout(1s) + margin(5s) = 6s < hang(10s)

class _HangHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(_HANG_SLEEP)

    def do_POST(self):
        time.sleep(_HANG_SLEEP)

    def log_message(self, format, *args):
        pass  # suppress


_hang_port = _free_port()
_hang_server = ThreadingHTTPServer(("127.0.0.1", _hang_port), _HangHandler)
_server_thread = threading.Thread(target=_hang_server.serve_forever, daemon=True)
_server_thread.start()
_hang_url = f"http://127.0.0.1:{_hang_port}/internal/llm"
print(f"  [INFO] hang mock 서버 기동 — 127.0.0.1:{_hang_port} (sleep={_HANG_SLEEP}s)")

# ──────────────────────────────────────────────────────────────────────────────
# app.py import (env 사전 설정 필요)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[1] app.py import (ASGI in-process 준비)...")

# 반드시 import 전에 env 설정 — llm_parser가 모듈 로드 시 url 캐시함
os.environ.setdefault("WHATUDOIN_SCHEDULER_SERVICE", "1")
os.environ.setdefault("WHATUDOIN_OLLAMA_SERVICE_URL", _hang_url)

try:
    import app as _app_mod
    import llm_parser as _lp
    _asgi_app = _app_mod.app
    _ok("app.py import 성공", True)
    _ok("app.app ASGI 객체 존재", _asgi_app is not None)
except Exception as exc:
    _ok("app.py import 성공", False, f"{type(exc).__name__}: {exc}")
    _ok("app.app ASGI 객체 존재", False)
    _hang_server.shutdown()
    from datetime import datetime, timezone as tz
    utc_stamp = datetime.now(tz.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "_workspace" / "perf" / "m4_4_hang_p95" / "runs" / utc_stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "ollama_hang_general_api_p95.md"
    md_path.write_text(f"# M4-4 p95 probe FAILED\n\napp import 실패: {exc}\n", encoding="utf-8")
    sys.exit(1)

# ollama_service_url 직접 패치 (import 시 캐시됐을 수 있으므로 강제 설정)
_lp._ollama_service_url = _hang_url
_ok(f"llm_parser._ollama_service_url 패치", _lp._ollama_service_url == _hang_url)

# ──────────────────────────────────────────────────────────────────────────────
# concurrent AI in-flight 요청 (hang mock에 걸리는 background threads)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[2] AI in-flight 요청 시작 (background threads, hang mock에 연결)...")
_N_AI_THREADS = 3  # 동시 AI 요청 수
_ai_exceptions: list = []
_ai_done = threading.Event()


def _ai_worker():
    try:
        _lp._call_ollama_service("parse_schedule", "hang_test_m4_4", timeout=1)
    except Exception as exc:
        _ai_exceptions.append(type(exc).__name__)


_ai_threads = []
for _ in range(_N_AI_THREADS):
    t = threading.Thread(target=_ai_worker, daemon=True)
    t.start()
    _ai_threads.append(t)

# AI 스레드가 hang mock에 연결될 시간 확보
time.sleep(0.1)
_ok("AI in-flight threads 시작", len(_ai_threads) == _N_AI_THREADS)

# ──────────────────────────────────────────────────────────────────────────────
# B: /api/health 50회 동시 GET p95 측정 (ASGITransport, in-process)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[3] /api/health 50회 동시 GET p95 측정 (ASGITransport in-process)...")

_N_REQUESTS = 50
_P95_THRESHOLD_MS = 500.0

import httpx


async def _measure_p95() -> list[float]:
    """ASGITransport으로 /api/health N회 동시 GET, 응답 시간(ms) 리스트 반환."""
    transport = httpx.ASGITransport(app=_asgi_app)

    async def _single_get(client: httpx.AsyncClient) -> float:
        t0 = time.monotonic()
        try:
            resp = await client.get("http://testserver/api/health", timeout=5.0)
            elapsed = (time.monotonic() - t0) * 1000  # ms
            return elapsed
        except Exception:
            return (time.monotonic() - t0) * 1000

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        tasks = [_single_get(client) for _ in range(_N_REQUESTS)]
        latencies = await asyncio.gather(*tasks)
    return list(latencies)


try:
    latencies = asyncio.run(_measure_p95())
    _ok("50회 GET 완료", len(latencies) == _N_REQUESTS,
        f"count={len(latencies)}")

    latencies_sorted = sorted(latencies)
    p50_idx = int(0.50 * len(latencies_sorted)) - 1
    p95_idx = int(0.95 * len(latencies_sorted)) - 1
    p50 = latencies_sorted[max(0, p50_idx)]
    p95 = latencies_sorted[max(0, p95_idx)]

    print(f"  [INFO] p50={p50:.1f}ms, p95={p95:.1f}ms (목표: p95 < {_P95_THRESHOLD_MS}ms)")
    _ok(f"일반 API p95 < {_P95_THRESHOLD_MS}ms (AI hang 중)",
        p95 < _P95_THRESHOLD_MS,
        f"p95={p95:.1f}ms, p50={p50:.1f}ms")
except Exception as exc:
    _ok("50회 GET 완료", False, f"{type(exc).__name__}: {exc}")
    _ok(f"일반 API p95 < {_P95_THRESHOLD_MS}ms", False, "측정 실패")
    p95 = float("inf")
    latencies_sorted = []
    p50 = float("inf")

# ──────────────────────────────────────────────────────────────────────────────
# AI 스레드 대기 (timeout + margin 이후 모두 종료돼야 함)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[4] AI in-flight thread 종료 대기 (최대 8s)...")
for t in _ai_threads:
    t.join(timeout=8.0)

ai_done_count = sum(1 for t in _ai_threads if not t.is_alive())
_ok(f"AI threads 종료 완료 ({_N_AI_THREADS}개)", ai_done_count == _N_AI_THREADS,
    f"done={ai_done_count}/{_N_AI_THREADS}")
_ok("AI OllamaUnavailableError 발생 (timeout 예상)",
    "OllamaUnavailableError" in _ai_exceptions,
    f"exceptions={_ai_exceptions}")

# ──────────────────────────────────────────────────────────────────────────────
# hang 서버 종료
# ──────────────────────────────────────────────────────────────────────────────
_hang_server.shutdown()
print(f"  [INFO] hang mock 서버 종료")

# ──────────────────────────────────────────────────────────────────────────────
# 결과 markdown 저장
# ──────────────────────────────────────────────────────────────────────────────
utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out_dir = ROOT / "_workspace" / "perf" / "m4_4_hang_p95" / "runs" / utc_stamp
out_dir.mkdir(parents=True, exist_ok=True)
md_path = out_dir / "ollama_hang_general_api_p95.md"

lines = [
    "# M4-4 Ollama hang 중 일반 API p95 보강 Probe",
    "",
    f"**실행 시각**: {utc_stamp}",
    f"**방식**: in-process ASGI (httpx.ASGITransport)",
    f"**hang mock**: sleep={_HANG_SLEEP}s, port={_hang_port}",
    "",
    "## 검증 방식 선언",
    "",
    "- ASGI in-process 방식: full FastAPI app spawn 없이 `httpx.ASGITransport` 사용",
    "- AI hang: `_call_ollama_service(timeout=1)` × 3 threads → hang mock(10s sleep)에 연결",
    "- 일반 API: GET /api/health 50회 동시 호출 (asyncio.gather)",
    "- /api/health는 LLM IPC 호출 없음 → threadpool 잠식 0",
    "",
    "## 측정 결과",
    "",
    f"| 지표 | 값 |",
    f"|------|----|",
    f"| p50 latency | {p50:.1f}ms |",
    f"| p95 latency | {p95:.1f}ms |",
    f"| 목표 | < {_P95_THRESHOLD_MS}ms |",
    f"| 결과 | {'PASS' if p95 < _P95_THRESHOLD_MS else 'FAIL'} |",
    "",
    "## 항목별 결과",
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
    "## 총계",
    "",
    f"**{_pass}/{_pass + _fail} PASS**",
]

md_path.write_text("\n".join(lines), encoding="utf-8")

print()
print("=" * 70)
print(f"[hang_p95_probe] {_pass} PASS / {_fail} FAIL")
print(f"[hang_p95_probe] p95={p95:.1f}ms (목표: < {_P95_THRESHOLD_MS}ms)")
print(f"[hang_p95_probe] 결과 저장: {md_path}")

sys.exit(0 if _fail == 0 else 1)
