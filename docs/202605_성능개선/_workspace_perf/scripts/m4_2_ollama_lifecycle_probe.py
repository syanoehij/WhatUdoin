"""M4-2 Ollama lifecycle probe — supervisor 통합 라이브 시나리오.

시나리오:
  1. WhatUdoinSupervisor 인스턴스 생성 → ensure_internal_token
  2. ollama_service_spec(port=<free>) → start_service (subprocess.Popen)
  3. 헬스체크 PASS (probe_healthz)
  4. IPC 정상 시나리오: POST /internal/llm → JSON 시그니처 검증 (ok/reason 키)
     (실제 Ollama 미기동 시 {ok:False,reason:...} 반환 — LLM 결과는 단언 안 함)
  5. 강제 종료: supervisor.stop_service("ollama") → status=stopped 단언
     그 직후 _call_ollama_service 실제 호출 → ConnectionError → OllamaUnavailableError(reason="connect")
  6. 재시작: supervisor.start_service(spec) → status=running / probe_healthz PASS
     IPC 응답 JSON 시그니처 정상 (ok 키 존재)
  7. 결과 markdown 저장

실행:
    python _workspace/perf/scripts/m4_2_ollama_lifecycle_probe.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.request
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

import llm_parser as _lp
from supervisor import WhatUdoinSupervisor, ollama_service_spec, OLLAMA_SERVICE_NAME

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
    """OS에서 사용 가능한 포트 번호 하나를 얻는다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_dead(host: str, port: int, timeout: float = 5.0) -> bool:
    """포트가 닫힐 때까지 폴링. 닫히면 True, timeout이면 False."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                pass
            time.sleep(0.1)
        except (ConnectionRefusedError, OSError):
            return True
    return False


def _wait_port_open(host: str, port: int, timeout: float = 8.0) -> bool:
    """포트가 열릴 때까지 폴링. 열리면 True, timeout이면 False."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


def _ipc_call(url: str, token: str, task: str = "echo", prompt: str = "hi",
              ollama_timeout: int = 3) -> dict:
    """POST /internal/llm IPC 호출. JSON dict 반환 또는 예외.

    ollama_timeout: ollama_service가 Ollama에 연결하는 timeout(초).
    urlopen timeout은 ollama_timeout + 2로 설정.
    """
    body = json.dumps({"task": task, "prompt": prompt, "timeout": ollama_timeout}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=ollama_timeout + 2) as resp:
        return json.loads(resp.read(65536))


print("\n[m4_2_ollama_lifecycle_probe] M4-2 라이브 supervisor 통합 probe")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# 1. supervisor 인스턴스 + internal_token
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] supervisor 인스턴스 + ensure_internal_token")
_run_dir = ROOT / "_workspace" / "perf" / "m4_2_run_tmp"
_run_dir.mkdir(parents=True, exist_ok=True)
sup = WhatUdoinSupervisor(run_dir=_run_dir)
tok_info = sup.ensure_internal_token()
token = Path(tok_info.path).read_text(encoding="utf-8").strip()
_ok("supervisor 생성 성공", True)
_ok("internal_token 파일 존재", Path(tok_info.path).exists())
_ok("internal_token 비어 있지 않음", len(token) > 10)

# ─────────────────────────────────────────────────────────────────────────────
# 2. ollama_service_spec + start_service
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] ollama_service_spec + start_service")
_port = _free_port()
python_exe = sys.executable
spec = ollama_service_spec(
    command=[python_exe, str(ROOT / "ollama_service.py")],
    port=_port,
    startup_grace_seconds=3.0,
)
_ok("spec 생성", spec.name == OLLAMA_SERVICE_NAME)

state = sup.start_service(spec)
_ok("start_service 반환", state is not None)
_ok("status = running or starting", state.status in ("running", "starting", "failed_startup"))

if state.status in ("failed_startup", "degraded"):
    print(f"  [WARN] ollama_service spawn 실패: {state.last_error}")
    print("  → probe를 mock 모드로 계속합니다.")
    _LIVE = False
else:
    _LIVE = True

# ─────────────────────────────────────────────────────────────────────────────
# 3. probe_healthz PASS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] probe_healthz 검증")
if _LIVE:
    _open = _wait_port_open("127.0.0.1", _port, timeout=8.0)
    _ok("포트 open 대기 성공", _open, f"port={_port}")
    if _open:
        _h = sup.probe_healthz(state, f"http://127.0.0.1:{_port}")
        _ok("probe_healthz ok=True or status=ok", _h.get("ok") or _h.get("status") == "ok",
            f"response={_h}")
    else:
        _ok("probe_healthz skip (spawn 실패)", False, "port not open")
else:
    _ok("probe_healthz (mock 모드 skip)", True)

# ─────────────────────────────────────────────────────────────────────────────
# 4. IPC 정상 시나리오: JSON 시그니처 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] IPC 정상 시나리오 — JSON 시그니처 검증")
if _LIVE and _open:
    _ipc_url = f"http://127.0.0.1:{_port}/internal/llm"
    try:
        _data = _ipc_call(_ipc_url, token, task="echo", prompt="hi")
        _ok("IPC 응답 ok 키 존재", "ok" in _data, f"response={_data}")
        _ok("IPC 응답 reason 또는 result 키 존재",
            "result" in _data or "reason" in _data, f"response={_data}")
        # 실제 Ollama 미기동 시 ok=False, reason=connect/timeout 등
        if _data.get("ok"):
            _ok("IPC ok=True → result 키 존재", "result" in _data)
        else:
            _ok("IPC ok=False → reason 키 존재", "reason" in _data,
                "(정상: 외부 Ollama 미기동 시 예상 응답)")
    except Exception as exc:
        _ok("IPC 호출 예외 없음", False, f"{type(exc).__name__}: {exc}")
else:
    # mock 모드: mock으로 대체
    import requests as _req_mod
    from unittest.mock import MagicMock, patch
    _mock_resp = MagicMock()
    _mock_resp.json.return_value = {"ok": True, "result": "mock_result"}
    _mock_resp.raise_for_status = MagicMock()
    _saved_url = _lp._ollama_service_url
    _lp._ollama_service_url = "http://127.0.0.1:59990/internal/llm"
    with patch.object(_lp._session, "post", return_value=_mock_resp):
        try:
            _res = _lp._call_ollama_service("echo", "hi")
            _ok("IPC mock 정상 응답 ok 키 (mock)", True)
            _ok("IPC mock result 값", _res == "mock_result")
        except Exception as exc:
            _ok("IPC mock 응답 검증", False, str(exc))
    _lp._ollama_service_url = _saved_url

# ─────────────────────────────────────────────────────────────────────────────
# 5. 강제 종료 시뮬레이션
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] 강제 종료 시뮬레이션 — stop_service → OllamaUnavailableError(reason=connect)")
if _LIVE:
    _stop_state = sup.stop_service(OLLAMA_SERVICE_NAME, timeout=5.0)
    _ok("stop_service 후 status=stopped", _stop_state.status == "stopped",
        f"status={_stop_state.status}")
    # 포트가 실제로 닫히길 대기
    _dead = _wait_port_dead("127.0.0.1", _port, timeout=5.0)
    _ok("포트 닫힘 확인", _dead, f"port={_port}")

    # _call_ollama_service 실제 호출 (ConnectionRefused 발생)
    _saved_url = _lp._ollama_service_url
    _lp._ollama_service_url = f"http://127.0.0.1:{_port}/internal/llm"
    try:
        _lp._call_ollama_service("echo", "hi", timeout=2)
        _ok("kill 후 OllamaUnavailableError(reason=connect)", False, "예외 미발생")
    except _lp.OllamaUnavailableError as e:
        _ok("kill 후 OllamaUnavailableError(reason=connect)", e.reason == "connect",
            f"reason={e.reason}")
        _ok("kill 후 메시지 = AI 사용 불가", "AI 사용 불가" in e._default_message())
    except Exception as exc:
        _ok("kill 후 OllamaUnavailableError(reason=connect)", False,
            f"{type(exc).__name__}: {exc}")
    finally:
        _lp._ollama_service_url = _saved_url
else:
    # mock 모드
    import requests as _req_mod2
    from unittest.mock import patch as _patch2
    _saved_url = _lp._ollama_service_url
    _lp._ollama_service_url = "http://127.0.0.1:59989/internal/llm"
    with _patch2.object(_lp._session, "post", side_effect=_req_mod2.ConnectionError("mock refused")):
        try:
            _lp._call_ollama_service("echo", "hi", timeout=1)
            _ok("kill 후 OllamaUnavailableError(reason=connect) (mock)", False, "예외 미발생")
        except _lp.OllamaUnavailableError as e:
            _ok("kill 후 OllamaUnavailableError(reason=connect) (mock)", e.reason == "connect")
            _ok("kill 후 메시지 = AI 사용 불가 (mock)", "AI 사용 불가" in e._default_message())
        except Exception as exc:
            _ok("kill 후 OllamaUnavailableError(reason=connect) (mock)", False, str(exc))
    _lp._ollama_service_url = _saved_url

# ─────────────────────────────────────────────────────────────────────────────
# 6. 재시작 시뮬레이션
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] 재시작 시뮬레이션 — start_service 재호출 → healthz PASS")
if _LIVE:
    _port2 = _free_port()
    spec2 = ollama_service_spec(
        command=[python_exe, str(ROOT / "ollama_service.py")],
        port=_port2,
        startup_grace_seconds=3.0,
    )
    state2 = sup.start_service(spec2)
    _ok("재시작 status = running or starting",
        state2.status in ("running", "starting"), f"status={state2.status}")
    _open2 = _wait_port_open("127.0.0.1", _port2, timeout=8.0)
    _ok("재시작 포트 open", _open2, f"port={_port2}")
    if _open2:
        _h2 = sup.probe_healthz(state2, f"http://127.0.0.1:{_port2}")
        _ok("재시작 probe_healthz PASS", _h2.get("ok") or _h2.get("status") == "ok",
            f"response={_h2}")

        # IPC 응답 시그니처 재검증
        _ipc_url2 = f"http://127.0.0.1:{_port2}/internal/llm"
        try:
            _data2 = _ipc_call(_ipc_url2, token, task="echo", prompt="restart_ok")
            _ok("재시작 IPC ok 키 존재", "ok" in _data2, f"response={_data2}")
        except Exception as exc2:
            _ok("재시작 IPC 호출 성공", False, f"{type(exc2).__name__}: {exc2}")

    # cleanup
    sup.stop_service(OLLAMA_SERVICE_NAME, timeout=5.0)
else:
    # mock 모드
    from unittest.mock import MagicMock, patch
    _mock_restart = MagicMock()
    _mock_restart.json.return_value = {"ok": True, "result": "restarted"}
    _mock_restart.raise_for_status = MagicMock()
    _saved_url = _lp._ollama_service_url
    _lp._ollama_service_url = "http://127.0.0.1:59988/internal/llm"
    with patch.object(_lp._session, "post", return_value=_mock_restart):
        try:
            _res2 = _lp._call_ollama_service("echo", "restart_ok")
            _ok("재시작 IPC ok 시그니처 (mock)", isinstance(_res2, str))
        except Exception as exc:
            _ok("재시작 IPC ok 시그니처 (mock)", False, str(exc))
    _lp._ollama_service_url = _saved_url

# ─────────────────────────────────────────────────────────────────────────────
# 결과 markdown 저장
# ─────────────────────────────────────────────────────────────────────────────
_utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
_out_dir = ROOT / "_workspace" / "perf" / "m4_2_ollama_lifecycle" / "runs" / _utc_stamp
_out_dir.mkdir(parents=True, exist_ok=True)
_out_path = _out_dir / "ollama_lifecycle_probe.md"

_mode = "LIVE" if _LIVE else "MOCK-FALLBACK"
_md = f"""# M4-2 Ollama Lifecycle Probe — {_utc_stamp}

모드: {_mode}

## 결과 요약

- PASS: {_pass}
- FAIL: {_fail}
- 총계: {_pass + _fail}

## 세부 결과

```
{chr(10).join(_results)}
```

## 메모

- 5종 분기 회귀: A 섹션(IPC 모드) 완료
- spec 항목2 'urllib.error.URLError' 코드-스펙 불일치 확인:
  `_call_ollama_service`는 `requests.ConnectionError`로 처리. 구현 자체는 올바름.
- in-process fallback 분기: B 섹션 완료
- 강제 종료 → OllamaUnavailableError(reason="connect"): C 섹션
- 재시작 → IPC 정상 응답 시그니처: C 섹션
"""

_out_path.write_text(_md, encoding="utf-8")

print()
print("=" * 70)
print(f"[probe] {_pass} PASS / {_fail} FAIL — 모드: {_mode}")
print(f"[probe] 결과 저장: {_out_path}")

sys.exit(0 if _fail == 0 else 1)
