"""
M2-18 SSE publish 실패 유실 정책 테스트 (standalone runner).

시나리오:
  1. IPC unreachable → sse_publish_failure +1
  2. IPC unreachable → logger.warning 호출
  3. logger.warning extra에 token/Authorization 0건
  4. get_failure_snapshot() 시그니처(count/last_event/last_reason/last_at)
  5. snapshot last_event/last_reason/last_at 실패 후 채워짐
  6. /healthz JSON에 sse_publish_failures + sse_publish_last_event + sse_publish_last_at 키 존재
  7. publisher.publish IPC 모드 silent — raise 없음
  8. publisher.publish in-process 모드 silent — wu_broker raise 시도에도 raise 없음

Run:
    python tests/phase60_sse_publish_failure_policy.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

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


class _CapHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self.records.append(record)


# ─────────────────────────────────────────────────────────────────────────────
# 1-3. IPC unreachable → 카운터 + 로그 + token 누출 없음
# ─────────────────────────────────────────────────────────────────────────────

def test_ipc_failure():
    print("\n[1-3] IPC unreachable → 카운터/로그/token 누출 없음")
    import importlib
    import publisher

    importlib.reload(publisher)

    secret_token = "phase60-secret-token-xyz"
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = "http://127.0.0.1:19997/internal/publish"
    os.environ["WHATUDOIN_INTERNAL_TOKEN"] = secret_token

    handler = _CapHandler()
    pub_logger = logging.getLogger("publisher")
    pub_logger.addHandler(handler)
    pub_logger.setLevel(logging.DEBUG)

    before = publisher.sse_publish_failure
    publisher.publish("schedule.created", {"id": 99})
    after = publisher.sse_publish_failure

    _ok("[1] sse_publish_failure +1", after == before + 1,
        f"before={before} after={after}")

    warnings = [r for r in handler.records if r.levelno == logging.WARNING]
    _ok("[2] logger.warning 호출됨", len(warnings) >= 1,
        f"warning count={len(warnings)}")

    if warnings:
        rec = warnings[-1]
        serializable = {k: str(v) for k, v in vars(rec).items()
                        if isinstance(v, (str, int, float, bool, type(None)))}
        rec_str = json.dumps(serializable, ensure_ascii=False)
        _ok("[3a] token raw 값 누출 없음",
            secret_token not in rec_str,
            f"token in record: {rec_str[:300]}")
        _ok("[3b] Authorization/Bearer 누출 없음",
            "Authorization" not in rec_str and "Bearer" not in rec_str,
            f"header in record: {rec_str[:300]}")

    pub_logger.removeHandler(handler)
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)
    os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)


# ─────────────────────────────────────────────────────────────────────────────
# 4-5. get_failure_snapshot() 시그니처 + 메타 채워짐
# ─────────────────────────────────────────────────────────────────────────────

def test_snapshot():
    print("\n[4-5] get_failure_snapshot() 시그니처 + 메타")
    import importlib
    import publisher

    importlib.reload(publisher)

    snap_empty = publisher.get_failure_snapshot()
    _ok("[4a] count key", "count" in snap_empty, str(snap_empty))
    _ok("[4b] last_event key", "last_event" in snap_empty, str(snap_empty))
    _ok("[4c] last_reason key", "last_reason" in snap_empty, str(snap_empty))
    _ok("[4d] last_at key", "last_at" in snap_empty, str(snap_empty))

    # 실패 유도 후 메타 확인
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = "http://127.0.0.1:19997/internal/publish"
    publisher.publish("task.updated", {"id": 7})
    snap = publisher.get_failure_snapshot()

    _ok("[5a] count >= 1", snap["count"] >= 1, str(snap["count"]))
    _ok("[5b] last_event 채워짐", snap["last_event"] is not None, str(snap["last_event"]))
    _ok("[5c] last_reason 채워짐", snap["last_reason"] is not None, str(snap["last_reason"]))
    _ok("[5d] last_at is float", isinstance(snap["last_at"], float), str(type(snap["last_at"])))

    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)


# ─────────────────────────────────────────────────────────────────────────────
# 6. /healthz 키 존재
# ─────────────────────────────────────────────────────────────────────────────

def test_healthz_keys():
    print("\n[6] /healthz sse_publish_* 키 존재")
    from starlette.testclient import TestClient
    import app as app_mod

    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        r = c.get("/healthz")
        _ok("[6a] 200 OK", r.status_code == 200, str(r.status_code))
        data = r.json()
        _ok("[6b] sse_publish_failures key", "sse_publish_failures" in data, str(data))
        _ok("[6c] sse_publish_last_event key", "sse_publish_last_event" in data, str(data))
        _ok("[6d] sse_publish_last_at key", "sse_publish_last_at" in data, str(data))
        _ok("[6e] sse_publish_failures is int",
            isinstance(data.get("sse_publish_failures"), int),
            str(type(data.get("sse_publish_failures"))))


# ─────────────────────────────────────────────────────────────────────────────
# 6b. DB rollback 회귀 — _sse_publish 호출이 get_conn() 블록 바깥에 있음을 정적 검증
# ─────────────────────────────────────────────────────────────────────────────

def test_no_publish_inside_get_conn():
    """_sse_publish( 호출이 with get_conn() 블록 안에 있지 않음을 정적으로 검증.

    알고리즘: app.py를 줄 단위로 읽어 indent-stack 추적.
    'with get_conn()' 라인이 보이면 해당 블록의 기준 indent 기록.
    다음 줄 indent가 기준보다 작거나 같아지면 블록이 닫힌 것으로 간주.
    _sse_publish( 가 등장한 줄이 열린 get_conn 블록 안이면 FAIL.
    """
    print("\n[6b] DB rollback 회귀 — _sse_publish가 get_conn() 블록 밖에 있음")
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    lines = app_src.splitlines()

    violations = []
    # open get_conn 블록의 기준 indent 스택 (None이면 바깥)
    conn_block_indents: list[int] = []

    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    for i, raw in enumerate(lines, 1):
        stripped = raw.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        cur_indent = _indent(raw)

        # 열린 블록이 닫혔는지 확인 (indent 감소 시)
        conn_block_indents = [b for b in conn_block_indents if cur_indent > b]

        # get_conn() with 블록 시작 감지
        if "with get_conn(" in stripped and stripped.lstrip().startswith("with "):
            conn_block_indents.append(cur_indent)

        # _sse_publish 호출 위치
        if "_sse_publish(" in stripped:
            if conn_block_indents:
                violations.append((i, stripped.strip()[:80]))

    _ok("[6b] _sse_publish 호출 — get_conn 블록 내부 0건",
        len(violations) == 0,
        f"위반: {violations}")

    # 호출 수 스냅샷 고정 (회귀 감지용)
    call_count = sum(1 for line in lines if "_sse_publish(" in line)
    _ok("[6c] _sse_publish 호출 수 37개 고정",
        call_count == 37,
        f"실제 호출 수: {call_count} (변경 시 이 단언을 의도적으로 업데이트)")


# ─────────────────────────────────────────────────────────────────────────────
# 7. IPC silent
# ─────────────────────────────────────────────────────────────────────────────

def test_ipc_silent():
    print("\n[7] publisher.publish IPC 모드 silent")
    import importlib
    import publisher

    importlib.reload(publisher)
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = "http://127.0.0.1:19997/internal/publish"

    raised = False
    try:
        publisher.publish("event.a", {"k": 1})
        publisher.publish("event.b", {"k": 2})
        publisher.publish("event.c", {"k": 3})
    except Exception as exc:
        raised = True

    _ok("[7] 3회 publish — raise 없음", not raised)
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)


# ─────────────────────────────────────────────────────────────────────────────
# 8. in-process silent
# ─────────────────────────────────────────────────────────────────────────────

def test_inprocess_silent():
    print("\n[8] publisher.publish in-process silent (wu_broker raise 시뮬레이션)")
    import importlib
    import publisher

    importlib.reload(publisher)
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)

    before = publisher.sse_publish_failure
    raised = False
    with patch("publisher.wu_broker") as mock_broker:
        mock_broker.publish.side_effect = RuntimeError("event loop closed")
        try:
            publisher.publish("docs.changed", {"action": "update"})
        except Exception:
            raised = True
    after = publisher.sse_publish_failure

    _ok("[8a] raise 없음", not raised)
    _ok("[8b] sse_publish_failure +1", after == before + 1,
        f"before={before} after={after}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("phase60 - M2-18 SSE publish 실패 유실 정책")
    print("=" * 65)
    test_ipc_failure()
    test_snapshot()
    test_healthz_keys()
    test_no_publish_inside_get_conn()
    test_ipc_silent()
    test_inprocess_silent()
    print("\n" + "=" * 65)
    print(f"TOTAL: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
    print("=" * 65)
    if _fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
