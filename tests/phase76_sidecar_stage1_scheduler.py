"""
분리 1단계 회귀: main.py에 Scheduler sidecar 활성화 토글.

배경: M2~M6에서 분리 코드는 박혔지만 main.py 진입점은 supervisor를 사용
하지 않아 일상 운영에서는 fallback 모드(단일 프로세스)로 동작했다. 본
단계는 단계적 활성화의 첫 step — 가장 안전한 Scheduler service만 sidecar로
spawn하도록 main.py에 env 토글을 추가한다.

토글: WHATUDOIN_ENABLE_SCHEDULER_SIDECAR=1
  → main.py 시작 시 supervisor.scheduler_service spawn + WHATUDOIN_SCHEDULER_SERVICE=1
    env 자동 설정으로 Web API lifespan 분기 활성화
  → 종료 시 supervisor.stop_all(timeout=5.0)으로 graceful shutdown

토글 미설정: 기존 fallback 동작 100% 유지(VSCode 디버그/일상 운영 회귀 0).

본 회귀 테스트는 다음을 잠근다:
  1. main.py에 WHATUDOIN_ENABLE_SCHEDULER_SIDECAR 토글 코드 grep
  2. 토글 시 supervisor import + WhatUdoinSupervisor 인스턴스 + scheduler_service_spec 호출
  3. 토글 시 SCHEDULER_SERVICE_ENABLE_ENV 자동 주입
  4. 종료 시 supervisor.stop_all() 호출 코드 grep
  5. 토글 미설정 시 fallback 분기 보존(_supervisor_instance = None)
  6. main.py import 부작용 0(토글 미설정 상태 default)

Run:
    python tests/phase76_sidecar_stage1_scheduler.py
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
    checks["toggle_env_present"] = "WHATUDOIN_ENABLE_SCHEDULER_SIDECAR" in src

    # 분기 코드 패턴
    checks["toggle_branch_pattern"] = (
        '_scheduler_sidecar_enabled' in src
        and 'os.environ.get("WHATUDOIN_ENABLE_SCHEDULER_SIDECAR"' in src
    )

    # supervisor import (토글 분기 안)
    checks["supervisor_imported_in_branch"] = (
        'from supervisor import' in src
        and 'WhatUdoinSupervisor' in src
        and 'scheduler_service_spec' in src
        and 'SCHEDULER_SERVICE_ENABLE_ENV' in src
    )

    # supervisor 인스턴스 생성
    checks["supervisor_instance_created"] = (
        '_supervisor_instance = WhatUdoinSupervisor(run_dir=_run_dir())' in src
        or '_supervisor_instance = WhatUdoinSupervisor(' in src
    )

    # ensure_internal_token 호출
    checks["ensure_internal_token_called"] = (
        '_supervisor_instance.ensure_internal_token()' in src
    )

    # scheduler_service_spec + start_service 호출
    checks["scheduler_spawn_called"] = (
        '_supervisor_instance.start_service(_scheduler_spec)' in src
        and 'scheduler_service_spec(' in src
    )

    # SCHEDULER_SERVICE_ENABLE_ENV 자동 주입
    checks["scheduler_enable_env_injected"] = (
        'os.environ[SCHEDULER_SERVICE_ENABLE_ENV] = "1"' in src
    )

    # 종료 시 stop_all 호출
    checks["stop_all_on_shutdown"] = (
        '_supervisor_instance.stop_all(timeout=5.0)' in src
    )

    # fallback default 보존: 토글 미설정 시 _supervisor_instance = None
    checks["fallback_default_none"] = (
        '_supervisor_instance = None' in src
    )

    # Path import 추가됨
    checks["path_imported"] = "from pathlib import Path" in src

    return checks


def _check_scheduler_service_spec_signature() -> dict:
    """supervisor.scheduler_service_spec이 main.py가 호출하는 시그니처를 지원하는지."""
    import importlib
    import supervisor as sv
    importlib.reload(sv)

    checks: dict[str, bool] = {}

    # SCHEDULER_SERVICE_ENABLE_ENV 상수 노출
    checks["enable_env_const_exported"] = hasattr(sv, "SCHEDULER_SERVICE_ENABLE_ENV")

    # scheduler_service_spec 함수 존재
    checks["spec_factory_callable"] = callable(getattr(sv, "scheduler_service_spec", None))

    # WhatUdoinSupervisor 클래스 노출
    checks["supervisor_class_exported"] = hasattr(sv, "WhatUdoinSupervisor")

    # spec 호출 (smoke)
    try:
        spec = sv.scheduler_service_spec(command=["python", "scheduler_service.py"])
        checks["spec_smoke_call"] = (
            spec.name == "scheduler"
            and "WHATUDOIN_SCHEDULER_SERVICE" in spec.env
            and spec.env["WHATUDOIN_SCHEDULER_SERVICE"] == "1"
        )
    except Exception as exc:
        checks["spec_smoke_call"] = False
        print(f"    (note: spec smoke 예외: {exc})")

    return checks


def _check_main_import_no_sidespawn() -> dict:
    """main.py를 모듈로 import해도 토글 미설정 상태에서는 sidecar spawn이 발생하지 않음.

    main.py 본체는 if __name__ == "__main__": 같은 패턴이 아니더라도, sidecar 분기가
    if 토글 검사로 보호되어 있으므로 default 환경에서 부작용이 없어야 한다.
    """
    checks: dict[str, bool] = {}

    # main.py를 import하지 않고 grep으로만 확인 — 부작용 회피
    src = (ROOT / "main.py").read_text(encoding="utf-8")

    # sidecar 분기가 _scheduler_sidecar_enabled 가드 안에 있는지
    branch_block = ""
    start = src.find("_scheduler_sidecar_enabled")
    if start >= 0:
        # 분기 끝 추정 — 다음 import uvicorn 라인
        end = src.find("import uvicorn", start)
        branch_block = src[start:end] if end > 0 else ""

    checks["spawn_inside_toggle_branch"] = (
        "if _scheduler_sidecar_enabled:" in branch_block
        and "_supervisor_instance.start_service" in branch_block
    )

    return checks


def main() -> int:
    print("=" * 64)
    print("phase76 - 분리 1단계: Scheduler sidecar 활성화 토글 잠금")
    print("=" * 64)

    print("\n[A] main.py 토글 + supervisor 통합 코드 grep")
    for name, passed in _check_main_grep().items():
        _ok(name, passed)

    print("\n[B] supervisor.scheduler_service_spec 시그니처")
    for name, passed in _check_scheduler_service_spec_signature().items():
        _ok(name, passed)

    print("\n[C] sidecar spawn이 토글 분기 가드 안에 있음 (fallback 보존)")
    for name, passed in _check_main_import_no_sidespawn().items():
        _ok(name, passed)

    print("\n" + "=" * 64)
    print(f"결과: {_pass} PASS, {_fail} FAIL")
    print("=" * 64)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
