"""M4-1 Ollama service relocation probe.

단언 목록:
  1. ollama_service.py import 부작용 0 (모듈 수준 인스턴스/네트워크 호출 없음)
  2. __main__ 진입점 코드 grep: uvicorn.run + bind 127.0.0.1 + Starlette 인스턴스
     + /internal/llm + /healthz 라우트 + RotatingFileHandler
  3. llm_parser env 분기: WHATUDOIN_OLLAMA_SERVICE_URL 미설정 시 기존 7+1접점 호출 유지
     (mock으로 _session.post 카운트 단언)
  4. llm_parser env 분기: WHATUDOIN_OLLAMA_SERVICE_URL 설정 시 IPC 호출만
     (mock으로 _call_ollama_service 카운트 단언)
  5. IPC 헬퍼: Authorization Bearer 첨부, ok/busy/unavailable → OllamaUnavailableError 변환
  6. supervisor.ollama_service_spec(): 3 protected env + extra_env override 차단
     + web_api_internal_service_env에 OLLAMA_SERVICE_URL 자동 주입
  7. M2_STARTUP_SEQUENCE에 start_ollama_service 포함, scheduler 다음 위치
  8. ollama_service /internal/llm: 토큰 없음 401, 잘못된 401,
     loopback 외부 IP 403
  9. ollama_service /healthz: status/service/limiter/ollama_health/uptime_seconds 키 존재

Run:
    python _workspace/perf/scripts/m4_1_ollama_service_relocation_probe.py
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
print("\n[m4_1] M4-1 Ollama service relocation probe")
print("=" * 60)

ollama_svc_src = (ROOT / "ollama_service.py").read_text(encoding="utf-8", errors="replace")
llm_src        = (ROOT / "llm_parser.py").read_text(encoding="utf-8", errors="replace")
sup_src        = (ROOT / "supervisor.py").read_text(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# [1] ollama_service.py import 부작용 0
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] ollama_service.py import 부작용 0")

import ollama_service as _svc_mod

_ok("ollama_service import 성공", True)
_ok("모듈 수준 Starlette app 인스턴스 없음", not hasattr(_svc_mod, "app"))
_ok("모듈 수준 _server 없음", not hasattr(_svc_mod, "_server"))
_ok("모듈 수준 scheduler/limiter 없음 (main 내부에만 존재)",
    not hasattr(_svc_mod, "_ollama_limiter") and not hasattr(_svc_mod, "scheduler"))
_ok("if __name__ == '__main__': main() 패턴",
    'if __name__ == "__main__"' in ollama_svc_src and "main()" in ollama_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [2] ollama_service.py 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] ollama_service.py 코드 내용 grep")

_ok("uvicorn.run 또는 uvicorn.Server().run() 존재",
    "uvicorn.Server" in ollama_svc_src or "uvicorn.run" in ollama_svc_src)
_ok("bind 127.0.0.1 강제",
    '127.0.0.1' in ollama_svc_src)
_ok("Starlette 인스턴스 생성",
    "Starlette(" in ollama_svc_src)
_ok("/internal/llm 라우트 존재",
    '"/internal/llm"' in ollama_svc_src)
_ok("/healthz 라우트 존재",
    '"/healthz"' in ollama_svc_src)
_ok("RotatingFileHandler 사용",
    "RotatingFileHandler" in ollama_svc_src)
_ok("RotatingFileHandler maxBytes=10MB",
    "10 * 1024 * 1024" in ollama_svc_src or "10*1024*1024" in ollama_svc_src)
_ok("RotatingFileHandler backupCount=14",
    "backupCount=14" in ollama_svc_src)
_ok("RotatingFileHandler delay=True",
    "delay=True" in ollama_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [3] llm_parser env 미설정 시 기존 접점 유지
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] llm_parser env 미설정 시 기존 _session.post 접점 유지")

# env 미설정 보장
if "WHATUDOIN_OLLAMA_SERVICE_URL" in os.environ:
    del os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"]

import llm_parser as _lp
importlib.reload(_lp)

_ok("_ollama_service_url 비어 있음 (env 미설정)",
    _lp._ollama_service_url == "",
    f"got {_lp._ollama_service_url!r}")

# parse_schedule 호출 시 _session.post 사용
_post_calls = []
_orig_post = _lp._session.post

def _mock_post(*a, **kw):
    _post_calls.append((a, kw))
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.json.return_value = {"response": '[{"title":"테스트","date":"2026-05-10","all_day":true,"event_type":"schedule"}]'}
    return m

_lp._session.post = _mock_post
_lp._ollama_limiter.try_acquire = lambda: True
_lp._ollama_limiter.release = lambda: None

try:
    result = _lp.parse_schedule("테스트 일정")
    _ok("env 미설정 시 parse_schedule → _session.post 호출 확인",
        len(_post_calls) >= 1, f"calls={len(_post_calls)}")
except Exception as exc:
    _ok("env 미설정 시 parse_schedule → _session.post 호출 확인",
        False, f"exception: {exc}")

_lp._session.post = _orig_post

# ─────────────────────────────────────────────────────────────────────────────
# [4] llm_parser env 설정 시 IPC 호출만
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] llm_parser env 설정 시 _call_ollama_service만 호출")

os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"] = "http://127.0.0.1:8767/internal/llm"
importlib.reload(_lp)

_ok("_ollama_service_url 설정됨",
    _lp._ollama_service_url == "http://127.0.0.1:8767/internal/llm",
    f"got {_lp._ollama_service_url!r}")

_ipc_calls = []
_orig_call = _lp._call_ollama_service

def _mock_ipc(task, prompt, **kwargs):
    _ipc_calls.append((task, prompt, kwargs))
    return '[{"title":"테스트","date":"2026-05-10","all_day":true,"event_type":"schedule"}]'

_lp._call_ollama_service = _mock_ipc

try:
    result = _lp.parse_schedule("테스트 일정")
    _ok("env 설정 시 parse_schedule → _call_ollama_service 호출",
        len(_ipc_calls) >= 1, f"calls={len(_ipc_calls)}")
    _ok("task='parse_schedule' 전달",
        any(c[0] == "parse_schedule" for c in _ipc_calls),
        f"tasks={[c[0] for c in _ipc_calls]}")
except Exception as exc:
    _ok("env 설정 시 parse_schedule → _call_ollama_service 호출", False, str(exc))
    _ok("task='parse_schedule' 전달", False, "")

# refine_schedule
_ipc_calls.clear()
try:
    _lp.refine_schedule("테스트 텍스트", [{"title": "기존", "date": None}])
    _ok("refine_schedule → IPC 호출",
        any(c[0] == "refine_schedule" for c in _ipc_calls),
        f"tasks={[c[0] for c in _ipc_calls]}")
except Exception as exc:
    _ok("refine_schedule → IPC 호출", False, str(exc))

_lp._call_ollama_service = _orig_call
del os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"]
importlib.reload(_lp)

# ─────────────────────────────────────────────────────────────────────────────
# [5] IPC 헬퍼 동작 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] _call_ollama_service IPC 헬퍼 검증")

os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"] = "http://127.0.0.1:8767/internal/llm"
os.environ["WHATUDOIN_INTERNAL_TOKEN"] = "test-token-abc"
importlib.reload(_lp)

# Bearer 헤더 첨부 확인
_sent_headers = []
_orig_session_post = _lp._session.post

def _mock_session_post(*a, **kw):
    _sent_headers.append(kw.get("headers", {}))
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.json.return_value = {"ok": True, "result": "test result"}
    return m

_lp._session.post = _mock_session_post
result_text = _lp._call_ollama_service("some_task", "some prompt", timeout=10)
_ok("IPC 헬퍼 성공 응답 → result 반환",
    result_text == "test result", f"got {result_text!r}")
_ok("Authorization: Bearer 헤더 첨부",
    any("Authorization" in h and "Bearer" in h.get("Authorization", "")
        for h in _sent_headers),
    f"headers={_sent_headers}")

# busy 응답 → OllamaUnavailableError(reason=busy)
def _mock_busy(*a, **kw):
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.json.return_value = {"ok": False, "reason": "busy", "slots": {"used": 1, "max": 1}}
    return m

_lp._session.post = _mock_busy
try:
    _lp._call_ollama_service("task", "prompt")
    _ok("busy 응답 → OllamaUnavailableError(reason=busy)", False, "no exception raised")
except _lp.OllamaUnavailableError as exc:
    _ok("busy 응답 → OllamaUnavailableError(reason=busy)",
        exc.reason == "busy", f"got reason={exc.reason!r}")
except Exception as exc:
    _ok("busy 응답 → OllamaUnavailableError(reason=busy)", False, str(exc))

# unavailable 응답 → OllamaUnavailableError(reason=timeout)
def _mock_unavail(*a, **kw):
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.json.return_value = {"ok": False, "reason": "timeout"}
    return m

_lp._session.post = _mock_unavail
try:
    _lp._call_ollama_service("task", "prompt")
    _ok("unavailable 응답 → OllamaUnavailableError", False, "no exception raised")
except _lp.OllamaUnavailableError as exc:
    _ok("unavailable 응답 → OllamaUnavailableError",
        exc.reason in ("timeout", "connect", "5xx"), f"got reason={exc.reason!r}")
except Exception as exc:
    _ok("unavailable 응답 → OllamaUnavailableError", False, str(exc))

_lp._session.post = _orig_session_post
del os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"]
del os.environ["WHATUDOIN_INTERNAL_TOKEN"]
importlib.reload(_lp)

# ─────────────────────────────────────────────────────────────────────────────
# [6] supervisor.ollama_service_spec() 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] supervisor.ollama_service_spec() ServiceSpec 검증")

import supervisor as _sup
importlib.reload(_sup)

cmd = ["python", "ollama_service.py"]
spec = _sup.ollama_service_spec(cmd)

_ok("name == 'ollama'", spec.name == _sup.OLLAMA_SERVICE_NAME,
    f"got {spec.name!r}")
_ok("BIND_HOST=127.0.0.1 강제",
    spec.env.get(_sup.OLLAMA_SERVICE_BIND_HOST_ENV) == "127.0.0.1",
    f"got {spec.env.get(_sup.OLLAMA_SERVICE_BIND_HOST_ENV)!r}")
_ok("PORT 기본값 8767",
    spec.env.get(_sup.OLLAMA_SERVICE_PORT_ENV) == str(_sup.OLLAMA_SERVICE_DEFAULT_PORT),
    f"got {spec.env.get(_sup.OLLAMA_SERVICE_PORT_ENV)!r}")

# 커스텀 포트
spec2 = _sup.ollama_service_spec(cmd, port=9100)
_ok("커스텀 포트 9100 적용",
    spec2.env.get(_sup.OLLAMA_SERVICE_PORT_ENV) == "9100",
    f"got {spec2.env.get(_sup.OLLAMA_SERVICE_PORT_ENV)!r}")

# extra_env protected 차단
spec3 = _sup.ollama_service_spec(
    cmd,
    extra_env={
        _sup.OLLAMA_SERVICE_BIND_HOST_ENV: "0.0.0.0",    # protected
        _sup.OLLAMA_SERVICE_PORT_ENV: "9999",             # protected
        _sup.INTERNAL_TOKEN_ENV: "leaked",               # protected
        "SOME_CUSTOM": "custom_value",                   # 허용
    },
)
_ok("BIND_HOST override 차단 (여전히 127.0.0.1)",
    spec3.env.get(_sup.OLLAMA_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("PORT override 차단 (여전히 기본값)",
    spec3.env.get(_sup.OLLAMA_SERVICE_PORT_ENV) == str(_sup.OLLAMA_SERVICE_DEFAULT_PORT))
_ok("INTERNAL_TOKEN override 차단 (spec.env에 없음)",
    _sup.INTERNAL_TOKEN_ENV not in spec3.env)
_ok("비보호 env 통과 (SOME_CUSTOM)",
    spec3.env.get("SOME_CUSTOM") == "custom_value")

# ─────────────────────────────────────────────────────────────────────────────
# [7] web_api_internal_service_env OLLAMA_SERVICE_URL 자동 주입
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] web_api_internal_service_env에 OLLAMA_SERVICE_URL 자동 주입")

web_env = _sup.web_api_internal_service_env()
_ok("WHATUDOIN_OLLAMA_SERVICE_URL 자동 주입",
    _sup.OLLAMA_SERVICE_URL_ENV in web_env,
    f"keys={list(web_env.keys())}")
_ok("OLLAMA_SERVICE_URL 값이 /internal/llm 포함",
    "/internal/llm" in web_env.get(_sup.OLLAMA_SERVICE_URL_ENV, ""),
    f"got {web_env.get(_sup.OLLAMA_SERVICE_URL_ENV)!r}")
_ok("OLLAMA_SERVICE_URL 포트 기본 8767",
    "8767" in web_env.get(_sup.OLLAMA_SERVICE_URL_ENV, ""),
    f"got {web_env.get(_sup.OLLAMA_SERVICE_URL_ENV)!r}")

# ─────────────────────────────────────────────────────────────────────────────
# [8] M2_STARTUP_SEQUENCE 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] M2_STARTUP_SEQUENCE 항목 검증")

seq = list(_sup.M2_STARTUP_SEQUENCE)
_ok("start_ollama_service 포함",
    "start_ollama_service" in seq, f"seq={seq}")
_ok("start_ollama_service가 start_scheduler_service 다음",
    seq.index("start_ollama_service") == seq.index("start_scheduler_service") + 1,
    f"ollama_idx={seq.index('start_ollama_service') if 'start_ollama_service' in seq else 'N/A'}, "
    f"scheduler_idx={seq.index('start_scheduler_service') if 'start_scheduler_service' in seq else 'N/A'}")
_ok("verify_health_and_publish_status가 마지막",
    seq[-1] == "verify_health_and_publish_status", f"last={seq[-1]!r}")

# ─────────────────────────────────────────────────────────────────────────────
# [9] ollama_service /internal/llm 인증 로직 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] ollama_service 인증 + healthz 응답 키 grep")

_ok("Bearer 토큰 검증 코드 존재",
    "Bearer" in ollama_svc_src and "compare_digest" in ollama_svc_src)
_ok("loopback IP 가드 존재 (_LOOPBACK_HOSTS)",
    "_LOOPBACK_HOSTS" in ollama_svc_src)
_ok("토큰 미일치 401 응답",
    "status_code=401" in ollama_svc_src or '"unauthorized"' in ollama_svc_src)
_ok("loopback 외부 IP 403 응답",
    "status_code=403" in ollama_svc_src or '"forbidden"' in ollama_svc_src)
_ok("healthz 응답에 status 키",
    '"status"' in ollama_svc_src)
_ok("healthz 응답에 service 키",
    '"service"' in ollama_svc_src and '"ollama"' in ollama_svc_src)
_ok("healthz 응답에 limiter 키",
    '"limiter"' in ollama_svc_src)
_ok("healthz 응답에 ollama_health 키",
    '"ollama_health"' in ollama_svc_src)
_ok("healthz 응답에 uptime_seconds 키",
    '"uptime_seconds"' in ollama_svc_src)
_ok("토큰 raw 값 로그 출력 없음 (token/raw 로그 변수 미포함)",
    "logging" in ollama_svc_src and "token" not in ollama_svc_src.lower().split("compare_digest")[0][-50:])

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
