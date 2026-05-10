"""
분리 3단계 회귀: main.py에 Ollama sidecar 활성화 토글.

1단계(Scheduler) + 2단계(Media)와 동일 패턴으로 Ollama service를 별도
프로세스로 spawn하는 토글을 추가한다. supervisor 인스턴스는 1·2·3단계
공유 — 어떤 sidecar든 활성화돼 있으면 인스턴스 + 토큰 발급 한 번만.

토글: WHATUDOIN_ENABLE_OLLAMA_SIDECAR=1
  → main.py 시작 시 supervisor.ollama_service spawn(127.0.0.1:8767) +
    WHATUDOIN_OLLAMA_SERVICE_URL 자동 주입(llm_parser IPC 분기 활성화)
  → 종료 시 supervisor.stop_all()로 1·2단계 산출물과 함께 graceful

토글 미설정: 기존 fallback 동작 100% 유지(in-process Ollama HTTP 호출).

본 회귀 테스트는 다음을 잠근다:
  1. WHATUDOIN_ENABLE_OLLAMA_SIDECAR 토글 코드 grep
  2. _ollama_sidecar_enabled 분기 패턴
  3. supervisor 인스턴스가 1·2·3단계 공유
  4. ollama_service_spec import + start_service spawn 코드 grep
  5. OLLAMA_SERVICE_URL_ENV / OLLAMA_SERVICE_DEFAULT_PORT 자동 주입
  6. supervisor.ollama_service_spec 시그니처/smoke
  7. 1·2단계 분기 보존(phase76/phase77 회귀 잠금)
  8. 셋 다 미설정 시 fallback 보존(_supervisor_instance=None)

Run:
    python tests/phase78_sidecar_stage3_ollama.py
"""

from __future__ import annotations

import sys
from pathlib import Path

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
        print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))


def _check_main_grep() -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}

    # 토글 env 이름 grep
    checks["ollama_toggle_env_present"] = "WHATUDOIN_ENABLE_OLLAMA_SIDECAR" in src

    # 분기 변수 패턴
    checks["ollama_branch_pattern"] = (
        "_ollama_sidecar_enabled" in src
        and 'os.environ.get("WHATUDOIN_ENABLE_OLLAMA_SIDECAR"' in src
    )

    # supervisor 인스턴스 공유 — scheduler/media/ollama 어느 하나(혹은 4단계도) 활성화 시 한 번만 생성
    # (4단계 이후 _frontend_routing_enabled도 조건에 추가될 수 있음)
    checks["supervisor_shared_construction_three_way"] = (
        "_scheduler_sidecar_enabled or _media_sidecar_enabled or _ollama_sidecar_enabled" in src
    )

    # ollama_service_spec import + spawn
    checks["ollama_service_spec_imported"] = (
        "ollama_service_spec" in src
        and "OLLAMA_SERVICE_URL_ENV" in src
        and "OLLAMA_SERVICE_DEFAULT_PORT" in src
    )
    checks["ollama_spawn_called"] = (
        "_supervisor_instance.start_service(_ollama_spec)" in src
        and "ollama_service_spec(" in src
    )

    # OLLAMA_SERVICE_URL_ENV 자동 주입
    checks["ollama_url_env_injected"] = (
        "os.environ[OLLAMA_SERVICE_URL_ENV] = _ollama_url" in src
        and "/internal/llm" in src
    )

    # 1단계 / 2단계 분기 보존 (회귀 잠금)
    checks["scheduler_branch_preserved"] = (
        "if _scheduler_sidecar_enabled:" in src
        and "_supervisor_instance.start_service(_scheduler_spec)" in src
    )
    checks["media_branch_preserved"] = (
        "if _media_sidecar_enabled:" in src
        and "_supervisor_instance.start_service(_media_spec)" in src
    )

    # fallback default
    checks["fallback_default_none"] = "_supervisor_instance = None" in src

    # 종료 시 stop_all 호출
    checks["stop_all_on_shutdown"] = (
        "_supervisor_instance.stop_all(timeout=5.0)" in src
    )

    return checks


def _check_ollama_service_spec_signature() -> dict:
    """supervisor.ollama_service_spec이 main.py가 호출하는 시그니처를 지원하는지."""
    import importlib
    import supervisor as sv
    importlib.reload(sv)

    checks: dict[str, bool] = {}

    checks["ollama_url_env_const_exported"] = hasattr(sv, "OLLAMA_SERVICE_URL_ENV")
    checks["ollama_default_port_const_exported"] = hasattr(sv, "OLLAMA_SERVICE_DEFAULT_PORT")
    checks["spec_factory_callable"] = callable(getattr(sv, "ollama_service_spec", None))

    try:
        spec = sv.ollama_service_spec(command=["python", "ollama_service.py"])
        checks["spec_smoke_call"] = (
            spec.name == "ollama"
            and spec.env.get("WHATUDOIN_OLLAMA_BIND_HOST") == "127.0.0.1"
            and spec.env.get("WHATUDOIN_OLLAMA_PORT") == str(sv.OLLAMA_SERVICE_DEFAULT_PORT)
        )
    except Exception as exc:
        checks["spec_smoke_call"] = False
        print(f"    (note: spec smoke 예외: {exc})")

    return checks


def _check_branch_isolation() -> dict:
    """sidecar spawn이 토글 분기 가드 안에 있고 default 환경에서 부작용 0."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}

    ollama_block_start = src.find("if _ollama_sidecar_enabled:")
    next_block = src.find("    import uvicorn", ollama_block_start)
    ollama_block = src[ollama_block_start:next_block] if next_block > 0 else ""

    checks["ollama_spawn_in_branch_guard"] = (
        "_supervisor_instance.start_service(_ollama_spec)" in ollama_block
        and "ollama_service_spec(" in ollama_block
    )

    checks["toggle_default_empty"] = (
        'os.environ.get("WHATUDOIN_ENABLE_OLLAMA_SIDECAR", "")' in src
    )

    return checks


def main() -> int:
    print("=" * 64)
    print("phase78 - 분리 3단계: Ollama sidecar 활성화 토글 잠금")
    print("=" * 64)

    print("\n[A] main.py 토글 + supervisor 통합 코드 grep")
    for name, passed in _check_main_grep().items():
        _ok(name, passed)

    print("\n[B] supervisor.ollama_service_spec 시그니처")
    for name, passed in _check_ollama_service_spec_signature().items():
        _ok(name, passed)

    print("\n[C] sidecar spawn이 토글 분기 가드 안에 있음 (fallback 보존)")
    for name, passed in _check_branch_isolation().items():
        _ok(name, passed)

    print("\n" + "=" * 64)
    print(f"결과: {_pass} PASS, {_fail} FAIL")
    print("=" * 64)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
