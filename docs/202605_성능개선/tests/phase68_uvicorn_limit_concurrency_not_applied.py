"""
M4-3 회귀 테스트: uvicorn limit_concurrency 미적용 잠금.

마스터 plan §12 sidecar 도입 후 (가) 분기 — limit_concurrency 미적용 채택.
M2~M4 sidecar 분리로 main app threadpool은 LLM/SSE/scheduler에 잠식되지
않으며, 사내 운영 환경(n≈1~5명) 폭주 시나리오 미관측. 보호는 §8 Ollama
세마포어 + §7 업로드 세마포어 + WAL/busy_timeout으로 충분. SSE service는
plan §12 명시적으로 미적용(SSE 연결 503 차단 시 알림 정합성 깨짐).

본 테스트는 운영 코드(main.py / app.py / sse_service.py / scheduler_service.py
/ ollama_service.py)에 limit_concurrency 인자가 0건임을 grep으로 잠근다.
limit_max_requests도 §12 금지 항목이라 0건 잠금.

Run:
    python tests/phase68_uvicorn_limit_concurrency_not_applied.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]

OPERATIONAL_FILES = (
    "main.py",
    "app.py",
    "sse_service.py",
    "scheduler_service.py",
    "ollama_service.py",
)

PROHIBITED_KEYS = ("limit_concurrency", "limit_max_requests")


def _collect_violations() -> list[tuple[str, str, int, str]]:
    violations: list[tuple[str, str, int, str]] = []
    for fname in OPERATIONAL_FILES:
        path = ROOT / fname
        if not path.exists():
            violations.append((fname, "FILE_MISSING", 0, ""))
            continue
        text = path.read_text(encoding="utf-8")
        for key in PROHIBITED_KEYS:
            for m in re.finditer(rf"\b{key}\b", text):
                line_no = text[: m.start()].count("\n") + 1
                start = text.rfind("\n", 0, m.start()) + 1
                end = text.find("\n", m.start())
                line_text = text[start : end if end > 0 else len(text)].strip()
                violations.append((fname, key, line_no, line_text))
    return violations


def _check_uvicorn_config_calls() -> list[str]:
    """uvicorn.Config / uvicorn.run / uvicorn.Server 호출이 검사 대상인지 확인."""
    found: list[str] = []
    for fname in OPERATIONAL_FILES:
        path = ROOT / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in (r"uvicorn\.Config\(", r"uvicorn\.run\(", r"uvicorn\.Server\("):
            for m in re.finditer(pattern, text):
                line_no = text[: m.start()].count("\n") + 1
                found.append(f"{fname}:{line_no} {m.group(0)}")
    return found


def main() -> int:
    print("=" * 64)
    print("phase68 — M4-3 uvicorn limit_concurrency 미적용 잠금")
    print("=" * 64)

    violations = _collect_violations()
    if violations:
        print("\n[FAIL] 위반 검출:")
        for fname, key, line_no, line_text in violations:
            print(f"  - {fname}:{line_no} [{key}] {line_text}")
        print("\n결과: FAIL")
        return 1

    config_calls = _check_uvicorn_config_calls()
    print(f"\n[INFO] 검사 대상 uvicorn 호출 {len(config_calls)}건:")
    for call in config_calls:
        print(f"  - {call}")

    if len(config_calls) < 4:
        print(f"\n[FAIL] 검사 대상이 너무 적음(최소 4건 — main HTTP/HTTPS + sse + scheduler + ollama)")
        return 1

    print("\n[PASS] limit_concurrency 0건")
    print("[PASS] limit_max_requests 0건 (§12 금지 항목)")
    print(f"[PASS] uvicorn Config/run/Server 호출 {len(config_calls)}건 모두 검사됨")

    print("\n" + "=" * 64)
    print(f"결과: 3 PASS, 0 FAIL — uvicorn limit_concurrency 미적용 채택 잠금")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
