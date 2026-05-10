"""Phase 66: M4-1 Ollama service relocation (standalone runner).

A. ollama_service.py 구조 단언 (import 부작용 0, 코드 grep)
B. llm_parser.py env 분기 단언 (IPC/in-process 경로 분기)
C. supervisor ollama_service_spec + STARTUP_SEQUENCE 단언
D. 회귀: phase54~65 핵심 항목 재확인

실행:
    python tests/phase66_ollama_service_relocation.py
"""
from __future__ import annotations

import importlib
import os
import sys
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


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
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


ollama_svc_src = _read(ROOT / "ollama_service.py")
llm_src        = _read(ROOT / "llm_parser.py")
sup_src        = _read(ROOT / "supervisor.py")
app_src        = _read(ROOT / "app.py")

print("\n[phase66] M4-1 Ollama service relocation")
print("=" * 60)

# ─────────────────────────────────────────────────────────────────────────────
# A. ollama_service.py 구조
# ─────────────────────────────────────────────────────────────────────────────
print("\n[A] ollama_service.py 구조 단언")

import ollama_service as _svc_mod

_ok("ollama_service import 성공", True)
_ok("모듈 수준 Starlette/Server 인스턴스 없음",
    not hasattr(_svc_mod, "app") and not hasattr(_svc_mod, "_server"))
_ok("if __name__ == '__main__' 진입점",
    'if __name__ == "__main__"' in ollama_svc_src)
_ok("main() 함수 정의",
    hasattr(_svc_mod, "main") and callable(_svc_mod.main))
_ok("/internal/llm 라우트 코드 존재",
    '"/internal/llm"' in ollama_svc_src)
_ok("/healthz 라우트 코드 존재",
    '"/healthz"' in ollama_svc_src)
_ok("RotatingFileHandler 사용",
    "RotatingFileHandler" in ollama_svc_src)
_ok("loopback bind 강제 (127.0.0.1)",
    "127.0.0.1" in ollama_svc_src)
_ok("Bearer 토큰 + compare_digest 인증",
    "Bearer" in ollama_svc_src and "compare_digest" in ollama_svc_src)
_ok("loopback 가드 (_LOOPBACK_HOSTS)",
    "_LOOPBACK_HOSTS" in ollama_svc_src)
_ok("401 응답 (토큰 불일치)",
    "401" in ollama_svc_src)
_ok("403 응답 (loopback 외부 IP)",
    "403" in ollama_svc_src)
_ok("healthz limiter 키 포함",
    '"limiter"' in ollama_svc_src)
_ok("healthz ollama_health 키 포함",
    '"ollama_health"' in ollama_svc_src)
_ok("healthz uptime_seconds 키 포함",
    '"uptime_seconds"' in ollama_svc_src)
_ok("ollama logs → ollama.app.log",
    "ollama.app.log" in ollama_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# B. llm_parser.py env 분기
# ─────────────────────────────────────────────────────────────────────────────
print("\n[B] llm_parser.py env 분기 단언")

_ok("_ollama_service_url 모듈 수준 변수 존재",
    "_ollama_service_url" in llm_src)
_ok("WHATUDOIN_OLLAMA_SERVICE_URL env 읽기",
    "WHATUDOIN_OLLAMA_SERVICE_URL" in llm_src)
_ok("_call_ollama_service 헬퍼 정의",
    "def _call_ollama_service" in llm_src)
_ok("parse_schedule IPC 분기",
    "parse_schedule" in llm_src and "_call_ollama_service" in llm_src)
_ok("refine_schedule IPC 분기",
    "refine_schedule" in llm_src)
_ok("generate_weekly_report IPC 분기",
    "weekly_report" in llm_src and "_call_ollama_service" in llm_src)
_ok("review_all_conflicts IPC 분기",
    "review_conflicts" in llm_src)
_ok("review_all_conflicts_with_funnel IPC 분기",
    "review_conflicts_funnel" in llm_src)
_ok("generate_checklist IPC 분기",
    "checklist" in llm_src)
_ok("generate_event_checklist_items IPC 분기",
    "event_checklist_items" in llm_src)
_ok("IPC 헬퍼 Authorization Bearer 첨부",
    "Authorization" in llm_src and "Bearer" in llm_src)
_ok("IPC 헬퍼 timeout + 5 margin",
    "timeout + 5" in llm_src)
_ok("IPC 헬퍼 busy → OllamaUnavailableError(reason=busy)",
    "busy" in llm_src and "OllamaUnavailableError" in llm_src)

# env 분기 동작 (mock)
if "WHATUDOIN_OLLAMA_SERVICE_URL" in os.environ:
    del os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"]

import llm_parser as _lp
importlib.reload(_lp)

_ok("env 미설정 시 _ollama_service_url == ''",
    _lp._ollama_service_url == "", f"got {_lp._ollama_service_url!r}")

os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"] = "http://127.0.0.1:8767/internal/llm"
importlib.reload(_lp)

_ok("env 설정 시 _ollama_service_url 올바르게 로드",
    _lp._ollama_service_url == "http://127.0.0.1:8767/internal/llm",
    f"got {_lp._ollama_service_url!r}")

del os.environ["WHATUDOIN_OLLAMA_SERVICE_URL"]
importlib.reload(_lp)

# ─────────────────────────────────────────────────────────────────────────────
# C. supervisor 단언
# ─────────────────────────────────────────────────────────────────────────────
print("\n[C] supervisor 단언")

import supervisor as _sup
importlib.reload(_sup)

_ok("OLLAMA_SERVICE_NAME = 'ollama'",
    _sup.OLLAMA_SERVICE_NAME == "ollama")
_ok("OLLAMA_SERVICE_DEFAULT_PORT = 8767",
    _sup.OLLAMA_SERVICE_DEFAULT_PORT == 8767)
_ok("OLLAMA_SERVICE_URL_ENV 정의",
    hasattr(_sup, "OLLAMA_SERVICE_URL_ENV"))
_ok("ollama_service_spec 함수 존재",
    hasattr(_sup, "ollama_service_spec") and callable(_sup.ollama_service_spec))

cmd = ["python", "ollama_service.py"]
spec = _sup.ollama_service_spec(cmd)

_ok("spec.name == 'ollama'",
    spec.name == "ollama", f"got {spec.name!r}")
_ok("spec BIND_HOST=127.0.0.1",
    spec.env.get(_sup.OLLAMA_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("spec PORT=8767",
    spec.env.get(_sup.OLLAMA_SERVICE_PORT_ENV) == "8767")
_ok("INTERNAL_TOKEN protected (spec.env에 없음)",
    _sup.INTERNAL_TOKEN_ENV not in spec.env)

# protected env 차단
spec_bad = _sup.ollama_service_spec(
    cmd,
    extra_env={
        _sup.OLLAMA_SERVICE_BIND_HOST_ENV: "0.0.0.0",
        _sup.OLLAMA_SERVICE_PORT_ENV: "9999",
        _sup.INTERNAL_TOKEN_ENV: "leaked",
        "SAFE_KEY": "safe_val",
    },
)
_ok("BIND_HOST override 차단",
    spec_bad.env.get(_sup.OLLAMA_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("PORT override 차단",
    spec_bad.env.get(_sup.OLLAMA_SERVICE_PORT_ENV) == "8767")
_ok("INTERNAL_TOKEN override 차단",
    _sup.INTERNAL_TOKEN_ENV not in spec_bad.env)
_ok("비보호 env 통과",
    spec_bad.env.get("SAFE_KEY") == "safe_val")

# STARTUP_SEQUENCE
seq = list(_sup.M2_STARTUP_SEQUENCE)
_ok("start_ollama_service 포함", "start_ollama_service" in seq)
_ok("start_ollama_service가 start_scheduler_service 다음",
    "start_ollama_service" in seq and "start_scheduler_service" in seq and
    seq.index("start_ollama_service") == seq.index("start_scheduler_service") + 1)

# web_api_internal_service_env
web_env = _sup.web_api_internal_service_env()
_ok("OLLAMA_SERVICE_URL 자동 주입",
    _sup.OLLAMA_SERVICE_URL_ENV in web_env)
_ok("OLLAMA_SERVICE_URL /internal/llm 포함",
    "/internal/llm" in web_env.get(_sup.OLLAMA_SERVICE_URL_ENV, ""))

# ─────────────────────────────────────────────────────────────────────────────
# D. 회귀: phase54~65 핵심 항목
# ─────────────────────────────────────────────────────────────────────────────
print("\n[D] 회귀: phase54~65 핵심 항목")

# M1c-ULTRA limiter 존재 (in-process 경로 회귀)
_ok("_OllamaLimiter 클래스 존재", "_OllamaLimiter" in llm_src)
_ok("_acquire_or_raise 함수 존재", "_acquire_or_raise" in llm_src)
_ok("OllamaUnavailableError 클래스 존재", "OllamaUnavailableError" in llm_src)

# supervisor M3-3 패턴 유지
_ok("STOP_ORDER에 ollama 포함",
    "ollama" in _sup.STOP_ORDER)
_ok("STOP_ORDER 순서: ollama → media → sse → scheduler → web-api (M5-2 이후)",
    list(_sup.STOP_ORDER[:5]) == ["ollama", "media", "sse", "scheduler", "web-api"])

# scheduler_service_spec 회귀
sched_spec = _sup.scheduler_service_spec(["python", "scheduler_service.py"])
_ok("scheduler_service_spec name == 'scheduler'",
    sched_spec.name == "scheduler")

# web_api_internal_service_env SCHEDULER_SERVICE 유지
_ok("web_api_internal_service_env SCHEDULER_SERVICE_ENABLE=1 유지",
    web_env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")

# app.py 회귀: _scheduler_service_enabled 분기 여전히 존재
_ok("app.py _scheduler_service_enabled 분기 유지",
    "_scheduler_service_enabled" in app_src)

# llm_parser env 미설정 fallback (기존 동작 100% 유지)
_ok("env 미설정 시 _session.post 기존 경로 존재 코드",
    "_session.post" in llm_src and "_acquire_or_raise" in llm_src)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
