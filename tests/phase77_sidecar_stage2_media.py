"""
분리 2단계 회귀: main.py에 Media sidecar 활성화 토글.

배경: 1단계(Scheduler sidecar)와 동일 패턴으로 Media service를 별도
프로세스로 spawn하는 토글을 추가한다. supervisor 인스턴스는 1단계와
공유 — 어떤 sidecar든 활성화돼 있으면 인스턴스 + 토큰 발급 한 번만.

토글: WHATUDOIN_ENABLE_MEDIA_SIDECAR=1
  → main.py 시작 시 supervisor.media_service spawn(127.0.0.1:8768) +
    WHATUDOIN_MEDIA_SERVICE_URL 자동 주입(app.py 업로드 핸들러 IPC 분기)
  → 종료 시 supervisor.stop_all()로 1단계 산출물과 함께 graceful

토글 미설정: 기존 fallback 동작 100% 유지(in-process PIL.verify + write_bytes).

본 회귀 테스트는 다음을 잠근다:
  1. WHATUDOIN_ENABLE_MEDIA_SIDECAR 토글 코드 grep
  2. _media_sidecar_enabled 분기 패턴
  3. supervisor 인스턴스가 1단계+2단계 공유(scheduler 또는 media 어느 한쪽
     활성화 시 한 번만 생성)
  4. media_service_spec import + start_service spawn 코드 grep
  5. MEDIA_SERVICE_URL_ENV / MEDIA_SERVICE_DEFAULT_PORT 자동 주입
  6. supervisor.media_service_spec 시그니처/smoke
  7. 1단계+2단계 둘 다 미설정 시 fallback 보존(_supervisor_instance=None)

Run:
    python tests/phase77_sidecar_stage2_media.py
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
    checks["media_toggle_env_present"] = "WHATUDOIN_ENABLE_MEDIA_SIDECAR" in src

    # 분기 변수 패턴
    checks["media_branch_pattern"] = (
        "_media_sidecar_enabled" in src
        and 'os.environ.get("WHATUDOIN_ENABLE_MEDIA_SIDECAR"' in src
    )

    # supervisor 인스턴스 공유 — scheduler 또는 media 활성화 시 한 번만 생성
    checks["supervisor_shared_construction"] = (
        "if _scheduler_sidecar_enabled or _media_sidecar_enabled:" in src
        and "_supervisor_instance = WhatUdoinSupervisor(run_dir=_run_dir())" in src
    )

    # media_service_spec import + spawn
    checks["media_service_spec_imported"] = (
        "from supervisor import" in src
        and "media_service_spec" in src
        and "MEDIA_SERVICE_URL_ENV" in src
        and "MEDIA_SERVICE_DEFAULT_PORT" in src
    )
    checks["media_spawn_called"] = (
        "_supervisor_instance.start_service(_media_spec)" in src
        and "media_service_spec(" in src
    )

    # MEDIA_SERVICE_URL_ENV 자동 주입
    checks["media_url_env_injected"] = (
        "os.environ[MEDIA_SERVICE_URL_ENV] = _media_url" in src
        and "/internal/process" in src
    )

    # 1단계 분기는 그대로 보존
    checks["scheduler_branch_preserved"] = (
        "if _scheduler_sidecar_enabled:" in src
        and "_supervisor_instance.start_service(_scheduler_spec)" in src
        and 'os.environ[SCHEDULER_SERVICE_ENABLE_ENV] = "1"' in src
    )

    # 둘 다 미설정 시 fallback default
    checks["fallback_default_none"] = (
        "_supervisor_instance = None" in src
    )

    # 종료 시 stop_all 호출은 그대로 (1단계에서 추가됨)
    checks["stop_all_on_shutdown"] = (
        "_supervisor_instance.stop_all(timeout=5.0)" in src
    )

    return checks


def _check_media_service_spec_signature() -> dict:
    """supervisor.media_service_spec이 main.py가 호출하는 시그니처를 지원하는지."""
    import importlib
    import supervisor as sv
    importlib.reload(sv)

    checks: dict[str, bool] = {}

    checks["media_url_env_const_exported"] = hasattr(sv, "MEDIA_SERVICE_URL_ENV")
    checks["media_default_port_const_exported"] = hasattr(sv, "MEDIA_SERVICE_DEFAULT_PORT")
    checks["spec_factory_callable"] = callable(getattr(sv, "media_service_spec", None))

    try:
        spec = sv.media_service_spec(command=["python", "media_service.py"])
        checks["spec_smoke_call"] = (
            spec.name == "media"
            and spec.env.get("WHATUDOIN_MEDIA_BIND_HOST") == "127.0.0.1"
            and spec.env.get("WHATUDOIN_MEDIA_PORT") == str(sv.MEDIA_SERVICE_DEFAULT_PORT)
        )
    except Exception as exc:
        checks["spec_smoke_call"] = False
        print(f"    (note: spec smoke 예외: {exc})")

    return checks


def _check_branch_isolation() -> dict:
    """sidecar spawn이 토글 분기 가드 안에 있고 default 환경에서 부작용 0."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}

    # media spawn이 if _media_sidecar_enabled: 분기 안에 있음
    media_block_start = src.find("if _media_sidecar_enabled:")
    next_block = src.find("    import uvicorn", media_block_start)
    media_block = src[media_block_start:next_block] if next_block > 0 else ""

    checks["media_spawn_in_branch_guard"] = (
        "_supervisor_instance.start_service(_media_spec)" in media_block
        and "media_service_spec(" in media_block
    )

    # 토글 변수 default empty string fallback
    checks["toggle_default_empty"] = (
        'os.environ.get("WHATUDOIN_ENABLE_MEDIA_SIDECAR", "")' in src
    )

    return checks


def main() -> int:
    print("=" * 64)
    print("phase77 - 분리 2단계: Media sidecar 활성화 토글 잠금")
    print("=" * 64)

    print("\n[A] main.py 토글 + supervisor 통합 코드 grep")
    for name, passed in _check_main_grep().items():
        _ok(name, passed)

    print("\n[B] supervisor.media_service_spec 시그니처")
    for name, passed in _check_media_service_spec_signature().items():
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
