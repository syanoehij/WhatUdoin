"""M4-2 Ollama hang 시뮬레이션 probe.

설계 결정 (advisor 권고에 따라 명시):
  전체 FastAPI app spawn 없이, 더 약한 클레임을 검증한다:
  - _call_ollama_service(timeout=1)를 thread에서 실행 → hang mock 서버에 연결됨
  - timeout=1+5(margin)=6초 이내에 OllamaUnavailableError(reason="timeout") raise
  - 별도 thread에서 간단한 CPU 작업(sleep 없는 dict 생성) → p95 < 100ms 단언
  이 테스트는 "threadpool에서 다른 FastAPI 라우트가 hang에 영향받지 않는다"의 proxy이지
  완전한 end-to-end app 검증은 아님. 보고서에 명시함.

hang mock 서버:
  - 127.0.0.1:<free> 바인딩
  - GET/POST 모두 10초 sleep 후 응답 없음
  - stdlib http.server.ThreadingHTTPServer 사용 (concurrent client 지원)

실행:
    python _workspace/perf/scripts/m4_2_ollama_hang_probe.py
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests as _real_requests
import llm_parser as _lp

_pass = 0
_fail = 0
_results: list[str] = []


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        msg = f"  [PASS] {name}"
    else:
        _fail += 1
        msg = f"  [FAIL] {name}" + (f" — {detail}" if detail else "")
    print(msg)
    _results.append(msg)


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


print("\n[m4_2_ollama_hang_probe] M4-2 Hang 시뮬레이션 probe")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# hang mock 서버 구성
# ─────────────────────────────────────────────────────────────────────────────
_HANG_SLEEP = 10  # 10초 sleep → timeout=1+5(margin)=6s보다 길다

class _HangHandler(BaseHTTPRequestHandler):
    """GET/POST 모두 10초 sleep 후 아무것도 반환하지 않는다."""

    def do_GET(self):
        time.sleep(_HANG_SLEEP)

    def do_POST(self):
        time.sleep(_HANG_SLEEP)

    def log_message(self, format, *args):
        pass  # 로그 억제


_hang_port = _free_port()
_hang_server = ThreadingHTTPServer(("127.0.0.1", _hang_port), _HangHandler)
_server_thread = threading.Thread(target=_hang_server.serve_forever, daemon=True)
_server_thread.start()
print(f"  [INFO] hang mock 서버 시작 — 127.0.0.1:{_hang_port} (sleep={_HANG_SLEEP}s)")

# ─────────────────────────────────────────────────────────────────────────────
# H1. IPC timeout 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[H1] IPC timeout=1 → OllamaUnavailableError(reason=timeout) 단언")

_saved_url = _lp._ollama_service_url
_lp._ollama_service_url = f"http://127.0.0.1:{_hang_port}/internal/llm"

_ipc_timeout_result: list = []
_ipc_elapsed: list[float] = []


def _ipc_call_thread():
    t0 = time.monotonic()
    try:
        _lp._call_ollama_service("parse_schedule", "test", timeout=1)
        _ipc_timeout_result.append("NO_EXCEPTION")
    except _lp.OllamaUnavailableError as e:
        _ipc_timeout_result.append(("OllamaUnavailableError", e.reason))
    except Exception as e:
        _ipc_timeout_result.append(("OTHER", type(e).__name__, str(e)))
    finally:
        _ipc_elapsed.append(time.monotonic() - t0)


_ipc_thread = threading.Thread(target=_ipc_call_thread, daemon=True)
_ipc_thread.start()
# timeout=1 → margin=1+5=6s → 최대 7초 대기
_ipc_thread.join(timeout=10.0)

if not _ipc_elapsed:
    _ok("IPC hang → timeout 예외 발생 (10s 내)", False, "thread join timeout")
else:
    _elapsed = _ipc_elapsed[0]
    _res = _ipc_timeout_result[0] if _ipc_timeout_result else None

    if isinstance(_res, tuple) and _res[0] == "OllamaUnavailableError":
        _ok("IPC hang → OllamaUnavailableError(reason=timeout)", _res[1] == "timeout",
            f"reason={_res[1]}")
    else:
        _ok("IPC hang → OllamaUnavailableError(reason=timeout)", False, f"result={_res}")

    # timeout margin: 1(IPC timeout) + 5(margin) = 6s
    _ok("IPC timeout 발생 시각 < 8s", _elapsed < 8.0, f"elapsed={_elapsed:.2f}s")

# URL 원복
_lp._ollama_service_url = _saved_url

# ─────────────────────────────────────────────────────────────────────────────
# H2. hang 중 별도 thread 응답성 검증 (threadpool 잠식 0 proxy 테스트)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[H2] hang 중 별도 작업 p95 < 100ms 단언 (threadpool 격리 proxy)")
print("  [NOTE] 이 검증은 전체 FastAPI app 없이 thread 격리만 검증하는 약한 proxy입니다.")
print("         (full app spawn 없이 합리적 시간 내 완료 가능한 대안)")

_N_SAMPLES = 20
_latencies: list[float] = []


def _fast_task() -> float:
    """CPU 바운드 작업 — 외부 IO 없이 dict 생성. 응답 시간 측정."""
    t0 = time.monotonic()
    # 간단한 연산 (실제 app의 non-LLM 라우트 proxy)
    _ = {str(i): i * 2 for i in range(100)}
    return time.monotonic() - t0


# IPC hang thread 시작 (배경)
_lp._ollama_service_url = f"http://127.0.0.1:{_hang_port}/internal/llm"
_hang_bg_result: list = []


def _ipc_hang_bg():
    try:
        _lp._call_ollama_service("parse_schedule", "bg_test", timeout=1)
    except Exception as e:
        _hang_bg_result.append(type(e).__name__)


_hang_bg_thread = threading.Thread(target=_ipc_hang_bg, daemon=True)
_hang_bg_thread.start()
time.sleep(0.05)  # hang thread가 연결되기를 잠깐 기다림

# hang 중 N회 fast_task 측정
for _ in range(_N_SAMPLES):
    _latencies.append(_fast_task() * 1000)  # ms 단위

_hang_bg_thread.join(timeout=10.0)
_lp._ollama_service_url = _saved_url

_latencies.sort()
_p95_idx = int(0.95 * len(_latencies)) - 1
_p95 = _latencies[max(0, _p95_idx)]

_ok(f"별도 thread p95 < 100ms", _p95 < 100.0, f"p95={_p95:.2f}ms")
_ok(f"샘플 수 = {_N_SAMPLES}", len(_latencies) == _N_SAMPLES)

# ─────────────────────────────────────────────────────────────────────────────
# hang 서버 종료
# ─────────────────────────────────────────────────────────────────────────────
_hang_server.shutdown()
print(f"  [INFO] hang mock 서버 종료")

# ─────────────────────────────────────────────────────────────────────────────
# 결과 markdown 저장
# ─────────────────────────────────────────────────────────────────────────────
_utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
_out_dir = ROOT / "_workspace" / "perf" / "m4_2_ollama_lifecycle" / "runs" / _utc_stamp
_out_dir.mkdir(parents=True, exist_ok=True)
_out_path = _out_dir / "ollama_hang_probe.md"

_md = f"""# M4-2 Hang 시뮬레이션 Probe — {_utc_stamp}

## 검증 방식 선언

전체 FastAPI app spawn 없이 thread 격리 proxy 테스트 사용.
- `_call_ollama_service(timeout=1)`을 thread에서 실행 → hang mock 서버
- 별도 thread에서 CPU 작업 응답 시간 p95 < 100ms 단언
- 이는 "threadpool에서 non-LLM 라우트가 hang에 영향받지 않는다"의 proxy이며
  완전한 FastAPI end-to-end 검증은 아님.

## 결과 요약

- PASS: {_pass}
- FAIL: {_fail}
- 총계: {_pass + _fail}

## 세부 결과

```
{chr(10).join(_results)}
```

## 측정값

- IPC timeout 발생 소요 시간: {_ipc_elapsed[0]:.2f}s (있을 경우)
- fast_task p95 latency: {_p95:.2f}ms (목표: < 100ms)
- 샘플 수: {len(_latencies)}
"""

_out_path.write_text(_md, encoding="utf-8")

print()
print("=" * 70)
print(f"[hang_probe] {_pass} PASS / {_fail} FAIL")
print(f"[hang_probe] 결과 저장: {_out_path}")

sys.exit(0 if _fail == 0 else 1)
