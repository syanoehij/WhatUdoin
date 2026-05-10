"""Phase 67: M4-2 통합 UX 회귀 + Ollama lifecycle mock 검증 (standalone runner).

A. 통합 UX 5종 분기 회귀 (IPC 모드 + in-process 모드 양쪽)
   분기 목록:
     1. IPC ConnectionError → OllamaUnavailableError(reason="connect")
     2. IPC Timeout        → OllamaUnavailableError(reason="timeout")
     3. IPC 응답 {ok:False, reason:"busy", slots:{used,max}} → OllamaUnavailableError(reason="busy", slots=(used,max))
     4. IPC 응답 {ok:False, reason:"timeout"|"connect"|"5xx"} → OllamaUnavailableError(reason=동일)
     5. IPC 정상 → str 반환
   spec 항목2 "urllib.error.URLError" 코드-스펙 불일치 메모:
     _call_ollama_service는 requests.ConnectionError를 잡는다. urllib.error.URLError 핸들러 없음.
     이는 스펙 문서의 표현 오류(requests 기반 구현 의도)임. 현 코드 동작 자체는 올바름.

B. in-process fallback 5종 분기 회귀
   1. _acquire_or_raise busy → OllamaUnavailableError(reason="busy", slots)
   2. requests.Timeout → OllamaUnavailableError(reason="timeout")  [get_available_models_with_status]
   3. requests.ConnectionError → OllamaUnavailableError(reason="connect")
   4. 5xx 응답 → OllamaUnavailableError(reason="5xx")
   5. busy 메시지 = "AI 사용 중 (N/N), 잠시 후 다시 시도해주세요."
      그 외 = "AI 사용 불가. 잠시 후 다시 시도해주세요."

C. Lifecycle mock 시뮬레이션
   C1. 서비스 kill → _call_ollama_service ConnectionError → OllamaUnavailableError(reason="connect")
   C2. 서비스 재시작 → IPC 정상 응답 (JSON 시그니처 검증)
   C3. Hang 시뮬레이션 mock: timeout 1초 설정 시 OllamaUnavailableError(reason="timeout")

실행:
    python tests/phase67_m4_2_ollama_lifecycle_ux.py
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


print("\n[phase67] M4-2 통합 UX 회귀 + Ollama lifecycle mock")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# 모듈 로드
# ─────────────────────────────────────────────────────────────────────────────
import requests as _real_requests
import llm_parser as _lp

# ─────────────────────────────────────────────────────────────────────────────
# A. IPC 모드 5종 분기 회귀
# ─────────────────────────────────────────────────────────────────────────────
print("\n[A] IPC 모드 (_call_ollama_service) 5종 분기 회귀")

# 테스트 전: _ollama_service_url을 임시 더미 URL로 설정 (IPC 분기 진입 확인)
_DUMMY_IPC_URL = "http://127.0.0.1:59999/internal/llm"

# A1. IPC ConnectionError → OllamaUnavailableError(reason="connect")
print("  [A1] IPC ConnectionError")
with patch.object(_lp._session, "post", side_effect=_real_requests.ConnectionError("refused")):
    _saved = _lp._ollama_service_url
    _lp._ollama_service_url = _DUMMY_IPC_URL
    try:
        _lp._call_ollama_service("parse_schedule", "test")
        _ok("IPC ConnectionError → OllamaUnavailableError", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("IPC ConnectionError → OllamaUnavailableError(reason=connect)", e.reason == "connect")
        _ok("IPC ConnectionError 메시지 = AI 사용 불가", e._default_message() == "AI 사용 불가. 잠시 후 다시 시도해주세요.")
    except Exception as e:
        _ok("IPC ConnectionError → OllamaUnavailableError", False, f"wrong exception: {type(e).__name__}: {e}")
    finally:
        _lp._ollama_service_url = _saved

# A2. IPC Timeout → OllamaUnavailableError(reason="timeout")
print("  [A2] IPC Timeout")
with patch.object(_lp._session, "post", side_effect=_real_requests.Timeout("timed out")):
    _saved = _lp._ollama_service_url
    _lp._ollama_service_url = _DUMMY_IPC_URL
    try:
        _lp._call_ollama_service("parse_schedule", "test")
        _ok("IPC Timeout → OllamaUnavailableError", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("IPC Timeout → OllamaUnavailableError(reason=timeout)", e.reason == "timeout")
        _ok("IPC Timeout 메시지 = AI 사용 불가", e._default_message() == "AI 사용 불가. 잠시 후 다시 시도해주세요.")
    except Exception as e:
        _ok("IPC Timeout → OllamaUnavailableError", False, f"wrong exception: {type(e).__name__}: {e}")
    finally:
        _lp._ollama_service_url = _saved

# A3. IPC 응답 {ok:False, reason:"busy", slots:{used:2, max:3}} → OllamaUnavailableError(reason="busy", slots=(2,3))
print("  [A3] IPC 응답 busy")
_mock_busy_resp = MagicMock()
_mock_busy_resp.json.return_value = {"ok": False, "reason": "busy", "slots": {"used": 2, "max": 3}}
_mock_busy_resp.raise_for_status = MagicMock()
with patch.object(_lp._session, "post", return_value=_mock_busy_resp):
    _saved = _lp._ollama_service_url
    _lp._ollama_service_url = _DUMMY_IPC_URL
    try:
        _lp._call_ollama_service("parse_schedule", "test")
        _ok("IPC busy → OllamaUnavailableError", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("IPC busy → OllamaUnavailableError(reason=busy)", e.reason == "busy")
        _ok("IPC busy slots = (2,3)", e.slots == (2, 3))
        _ok("IPC busy 메시지 = AI 사용 중 (2/3)", e._default_message() == "AI 사용 중 (2/3), 잠시 후 다시 시도해주세요.")
    except Exception as e:
        _ok("IPC busy → OllamaUnavailableError", False, f"wrong exception: {type(e).__name__}: {e}")
    finally:
        _lp._ollama_service_url = _saved

# A4. IPC 응답 {ok:False, reason:<other>} → OllamaUnavailableError(reason=동일)
print("  [A4] IPC 응답 ok=False 기타 reason")
for _reason in ("timeout", "connect", "5xx"):
    _mock_other_resp = MagicMock()
    _mock_other_resp.json.return_value = {"ok": False, "reason": _reason}
    _mock_other_resp.raise_for_status = MagicMock()
    with patch.object(_lp._session, "post", return_value=_mock_other_resp):
        _saved = _lp._ollama_service_url
        _lp._ollama_service_url = _DUMMY_IPC_URL
        try:
            _lp._call_ollama_service("parse_schedule", "test")
            _ok(f"IPC reason={_reason} → OllamaUnavailableError", False, "예외 미발생")
        except _lp.OllamaUnavailableError as e:
            _ok(f"IPC reason={_reason} → OllamaUnavailableError(reason={_reason})", e.reason == _reason)
            _ok(f"IPC reason={_reason} 메시지 = AI 사용 불가", e._default_message() == "AI 사용 불가. 잠시 후 다시 시도해주세요.")
        except Exception as e:
            _ok(f"IPC reason={_reason} → OllamaUnavailableError", False, f"wrong exception: {type(e).__name__}: {e}")
        finally:
            _lp._ollama_service_url = _saved

# A5. IPC 정상 응답 → str 반환
print("  [A5] IPC 정상 응답")
_mock_ok_resp = MagicMock()
_mock_ok_resp.json.return_value = {"ok": True, "result": "parsed_output"}
_mock_ok_resp.raise_for_status = MagicMock()
with patch.object(_lp._session, "post", return_value=_mock_ok_resp):
    _saved = _lp._ollama_service_url
    _lp._ollama_service_url = _DUMMY_IPC_URL
    try:
        result = _lp._call_ollama_service("parse_schedule", "test")
        _ok("IPC 정상 응답 → str 반환", isinstance(result, str) and result == "parsed_output")
    except Exception as e:
        _ok("IPC 정상 응답 → str 반환", False, f"{type(e).__name__}: {e}")
    finally:
        _lp._ollama_service_url = _saved

# spec 항목2 불일치 메모 출력
print()
print("  [NOTE] spec 항목2 'urllib.error.URLError' 코드-스펙 불일치:")
print("         _call_ollama_service는 requests.ConnectionError를 잡는다.")
print("         urllib.error.URLError 핸들러 없음 (requests 기반 구현이 올바른 의도).")

# ─────────────────────────────────────────────────────────────────────────────
# B. in-process fallback 5종 분기 회귀
# ─────────────────────────────────────────────────────────────────────────────
print("\n[B] in-process fallback 5종 분기 회귀 (WHATUDOIN_OLLAMA_SERVICE_URL 미설정)")

# in-process 모드 보장: _ollama_service_url을 빈 문자열로 임시 설정
_orig_svc_url = _lp._ollama_service_url
_lp._ollama_service_url = ""

# B1. _acquire_or_raise busy → OllamaUnavailableError(reason="busy", slots)
print("  [B1] in-process busy (_acquire_or_raise)")
# capacity=1인 limiter를 강제 포화
_orig_cap = _lp._ollama_limiter._capacity
_lp._ollama_limiter._capacity = 1
_lp._ollama_limiter._in_use = 1  # 직접 설정으로 포화 시뮬레이션
try:
    _lp._acquire_or_raise()
    _ok("in-process busy → OllamaUnavailableError", False, "예외 미발생")
except _lp.OllamaUnavailableError as e:
    _ok("in-process busy → OllamaUnavailableError(reason=busy)", e.reason == "busy")
    _ok("in-process busy slots not None", e.slots is not None)
    msg = e._default_message()
    _ok("in-process busy 메시지 = AI 사용 중 (N/N)", "AI 사용 중" in msg and "잠시 후 다시 시도해주세요." in msg)
except Exception as e:
    _ok("in-process busy → OllamaUnavailableError", False, f"{type(e).__name__}: {e}")
finally:
    _lp._ollama_limiter._in_use = 0
    _lp._ollama_limiter._capacity = _orig_cap

# B2. requests.Timeout (in-process get_available_models_with_status)
print("  [B2] in-process requests.Timeout → OllamaUnavailableError(reason=timeout)")
with patch.object(_lp._session, "get", side_effect=_real_requests.Timeout("timed out")):
    try:
        _lp.get_available_models_with_status()
        _ok("in-process Timeout → OllamaUnavailableError", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("in-process Timeout → OllamaUnavailableError(reason=timeout)", e.reason == "timeout")
        _ok("in-process Timeout 메시지 = AI 사용 불가", "AI 사용 불가" in e._default_message())
    except Exception as e:
        _ok("in-process Timeout → OllamaUnavailableError", False, f"{type(e).__name__}: {e}")

# B3. requests.ConnectionError (in-process)
print("  [B3] in-process requests.ConnectionError → OllamaUnavailableError(reason=connect)")
with patch.object(_lp._session, "get", side_effect=_real_requests.ConnectionError("refused")):
    try:
        _lp.get_available_models_with_status()
        _ok("in-process ConnectionError → OllamaUnavailableError", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("in-process ConnectionError → OllamaUnavailableError(reason=connect)", e.reason == "connect")
        _ok("in-process ConnectionError 메시지 = AI 사용 불가", "AI 사용 불가" in e._default_message())
    except Exception as e:
        _ok("in-process ConnectionError → OllamaUnavailableError", False, f"{type(e).__name__}: {e}")

# B4. 5xx 응답 (in-process)
print("  [B4] in-process 5xx → OllamaUnavailableError(reason=5xx)")
_mock_5xx = MagicMock()
_mock_5xx.status_code = 503
with patch.object(_lp._session, "get", return_value=_mock_5xx):
    try:
        _lp.get_available_models_with_status()
        _ok("in-process 5xx → OllamaUnavailableError", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("in-process 5xx → OllamaUnavailableError(reason=5xx)", e.reason == "5xx")
        _ok("in-process 5xx 메시지 = AI 사용 불가", "AI 사용 불가" in e._default_message())
    except Exception as e:
        _ok("in-process 5xx → OllamaUnavailableError", False, f"{type(e).__name__}: {e}")

# B5. 메시지 분기 확인
print("  [B5] OllamaUnavailableError._default_message() 분기")
_busy_err = _lp.OllamaUnavailableError(reason="busy", slots=(1, 1))
_timeout_err = _lp.OllamaUnavailableError(reason="timeout")
_connect_err = _lp.OllamaUnavailableError(reason="connect")
_5xx_err = _lp.OllamaUnavailableError(reason="5xx")
_ok("busy(1/1) 메시지", _busy_err._default_message() == "AI 사용 중 (1/1), 잠시 후 다시 시도해주세요.")
_ok("timeout 메시지 = AI 사용 불가", _timeout_err._default_message() == "AI 사용 불가. 잠시 후 다시 시도해주세요.")
_ok("connect 메시지 = AI 사용 불가", _connect_err._default_message() == "AI 사용 불가. 잠시 후 다시 시도해주세요.")
_ok("5xx 메시지 = AI 사용 불가", _5xx_err._default_message() == "AI 사용 불가. 잠시 후 다시 시도해주세요.")

# in-process URL 원복
_lp._ollama_service_url = _orig_svc_url

# ─────────────────────────────────────────────────────────────────────────────
# C. Lifecycle mock 시뮬레이션 (mock 격리 — 실제 subprocess spawn 없음)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[C] Lifecycle mock 시뮬레이션")

# C1. 서비스 kill 시뮬레이션: IPC URL 설정 + ConnectionError → OllamaUnavailableError(reason="connect")
print("  [C1] 강제 종료 시뮬레이션: ConnectionError → OllamaUnavailableError(reason=connect)")
with patch.object(_lp._session, "post", side_effect=_real_requests.ConnectionError("connection refused")):
    _lp._ollama_service_url = "http://127.0.0.1:59998/internal/llm"
    try:
        _lp._call_ollama_service("parse_schedule", "test")
        _ok("kill 후 ConnectionError → OllamaUnavailableError", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("kill 후 ConnectionError → OllamaUnavailableError(reason=connect)", e.reason == "connect")
        _ok("kill 후 메시지 = AI 사용 불가", "AI 사용 불가" in e._default_message())
    except Exception as e:
        _ok("kill 후 ConnectionError → OllamaUnavailableError", False, f"{type(e).__name__}: {e}")
    finally:
        _lp._ollama_service_url = _orig_svc_url

# C2. 재시작 후 IPC 정상 응답 시뮬레이션
print("  [C2] 재시작 후 IPC 정상 응답 시뮬레이션")
_mock_restart_resp = MagicMock()
_mock_restart_resp.json.return_value = {"ok": True, "result": "hello_from_service"}
_mock_restart_resp.raise_for_status = MagicMock()
with patch.object(_lp._session, "post", return_value=_mock_restart_resp):
    _lp._ollama_service_url = "http://127.0.0.1:59997/internal/llm"
    try:
        result = _lp._call_ollama_service("echo", "hello")
        _ok("재시작 후 정상 응답 수신", result == "hello_from_service")
        _ok("재시작 후 응답 ok/result 키 검증", isinstance(result, str))
    except Exception as e:
        _ok("재시작 후 정상 응답 수신", False, f"{type(e).__name__}: {e}")
    finally:
        _lp._ollama_service_url = _orig_svc_url

# C3. Hang 시뮬레이션: timeout=1 설정 시 Timeout 예외 → OllamaUnavailableError(reason="timeout")
print("  [C3] Hang 시뮬레이션: IPC timeout=1 → OllamaUnavailableError(reason=timeout)")
with patch.object(_lp._session, "post", side_effect=_real_requests.Timeout("request timed out")):
    _lp._ollama_service_url = "http://127.0.0.1:59996/internal/llm"
    try:
        _lp._call_ollama_service("parse_schedule", "test", timeout=1)
        _ok("Hang → OllamaUnavailableError(reason=timeout)", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("Hang → OllamaUnavailableError(reason=timeout)", e.reason == "timeout")
        _ok("Hang 메시지 = AI 사용 불가", "AI 사용 불가" in e._default_message())
    except Exception as e:
        _ok("Hang → OllamaUnavailableError(reason=timeout)", False, f"{type(e).__name__}: {e}")
    finally:
        _lp._ollama_service_url = _orig_svc_url

# ─────────────────────────────────────────────────────────────────────────────
# 결과
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print(f"[phase67] 결과: {_pass} PASS / {_fail} FAIL / {_pass + _fail} 총계")
if _fail > 0:
    print("[phase67] FAIL 항목 있음 — 위 FAIL 라인 확인 필요")
    sys.exit(1)
else:
    print("[phase67] 전체 PASS")
    sys.exit(0)
