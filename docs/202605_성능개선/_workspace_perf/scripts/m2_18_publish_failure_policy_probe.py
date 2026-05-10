"""M2-18 publish 실패 유실 정책 probe.

검증 항목:
  1. IPC unreachable → sse_publish_failure +1
  2. IPC unreachable → logger.warning 호출 (caplog)
  3. logger.warning extra에 token/Authorization 값 포함 금지
  4. get_failure_snapshot() 시그니처 (count/last_event/last_reason/last_at)
  5. /healthz JSON에 sse_publish_failures + sse_publish_last_event + sse_publish_last_at 키 존재
  6. publisher.publish silent — 호출자 코드 흐름 끊기지 않음
  7. in-process 브랜치(wu_broker)도 silent fail

Run:
    python _workspace/perf/scripts/m2_18_publish_failure_policy_probe.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
# helper: capture log records
# ─────────────────────────────────────────────────────────────────────────────

class _CapHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self.records.append(record)


# ─────────────────────────────────────────────────────────────────────────────
# 1-3. IPC unreachable → 카운터 +1, warning 로그, token 누출 없음
# ─────────────────────────────────────────────────────────────────────────────

def test_ipc_failure_counter_and_log():
    print("\n[1-3] IPC unreachable → 카운터 + 로그 + token 누출 없음")
    import importlib
    import publisher

    # fresh state
    importlib.reload(publisher)

    secret_token = "super-secret-token-abc123"
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = "http://127.0.0.1:19997/internal/publish"
    os.environ["WHATUDOIN_INTERNAL_TOKEN"] = secret_token

    handler = _CapHandler()
    pub_logger = logging.getLogger("publisher")
    pub_logger.addHandler(handler)
    pub_logger.setLevel(logging.DEBUG)

    before = publisher.sse_publish_failure
    publisher.publish("test.event", {"id": 1})
    after = publisher.sse_publish_failure

    _ok("[1] sse_publish_failure +1", after == before + 1,
        f"before={before} after={after}")

    # 2. logger.warning 호출 확인
    warnings = [r for r in handler.records if r.levelno == logging.WARNING]
    _ok("[2] logger.warning 호출됨", len(warnings) >= 1,
        f"records={[(r.levelno, r.getMessage()) for r in handler.records]}")

    # 3. token raw 값 / Authorization 포함 금지
    if warnings:
        rec = warnings[-1]
        record_dict = vars(rec)
        record_str = json.dumps({k: str(v) for k, v in record_dict.items()
                                 if isinstance(v, (str, int, float, bool, type(None)))},
                                ensure_ascii=False)
        _ok("[3a] token raw 값 포함 금지",
            secret_token not in record_str,
            f"token found in record: {record_str[:200]}")
        _ok("[3b] Authorization 값 포함 금지",
            "Authorization" not in record_str and "Bearer" not in record_str,
            f"Authorization found: {record_str[:200]}")

    pub_logger.removeHandler(handler)
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)
    os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)


# ─────────────────────────────────────────────────────────────────────────────
# 4. get_failure_snapshot() 시그니처
# ─────────────────────────────────────────────────────────────────────────────

def test_snapshot_signature():
    print("\n[4] get_failure_snapshot() 시그니처")
    import importlib
    import publisher
    importlib.reload(publisher)

    snap = publisher.get_failure_snapshot()
    _ok("[4a] count key", "count" in snap, str(snap))
    _ok("[4b] last_event key", "last_event" in snap, str(snap))
    _ok("[4c] last_reason key", "last_reason" in snap, str(snap))
    _ok("[4d] last_at key", "last_at" in snap, str(snap))
    _ok("[4e] count is int", isinstance(snap["count"], int), str(type(snap["count"])))


# ─────────────────────────────────────────────────────────────────────────────
# 5. /healthz 키 존재
# ─────────────────────────────────────────────────────────────────────────────

def test_healthz_keys():
    print("\n[5] /healthz에 sse_publish_failures 키 존재")
    from starlette.testclient import TestClient
    import app as app_mod
    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        r = c.get("/healthz")
        _ok("[5a] /healthz → 200", r.status_code == 200, str(r.status_code))
        data = r.json()
        _ok("[5b] sse_publish_failures key", "sse_publish_failures" in data, str(data))
        _ok("[5c] sse_publish_last_event key", "sse_publish_last_event" in data, str(data))
        _ok("[5d] sse_publish_last_at key", "sse_publish_last_at" in data, str(data))
        _ok("[5e] sse_publish_failures is int",
            isinstance(data.get("sse_publish_failures"), int),
            str(type(data.get("sse_publish_failures"))))


# ─────────────────────────────────────────────────────────────────────────────
# 6. publisher.publish silent — 호출자 코드 흐름 끊기지 않음 (IPC 모드)
# ─────────────────────────────────────────────────────────────────────────────

def test_publish_silent_ipc():
    print("\n[6] publisher.publish silent (IPC 모드)")
    import importlib
    import publisher
    importlib.reload(publisher)

    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = "http://127.0.0.1:19997/internal/publish"
    raised = False
    try:
        publisher.publish("test.silent", {"x": 1})
        publisher.publish("test.silent2", {"x": 2})
    except Exception as exc:
        raised = True

    _ok("[6] publish 2회 호출 — raise 없음", not raised)
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)


# ─────────────────────────────────────────────────────────────────────────────
# 7. in-process 브랜치(wu_broker) silent fail
# ─────────────────────────────────────────────────────────────────────────────

def test_publish_silent_inprocess():
    print("\n[7] publisher.publish silent (in-process 브랜치)")
    import importlib
    import publisher
    importlib.reload(publisher)

    # wu_broker.publish가 raise하는 상황 시뮬레이션
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)
    raised = False
    before = publisher.sse_publish_failure
    with patch("publisher.wu_broker") as mock_broker:
        mock_broker.publish.side_effect = RuntimeError("loop closed")
        try:
            publisher.publish("test.inproc", {"y": 1})
        except Exception:
            raised = True
    after = publisher.sse_publish_failure

    _ok("[7a] raise 없음", not raised)
    _ok("[7b] sse_publish_failure +1", after == before + 1,
        f"before={before} after={after}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("m2_18_publish_failure_policy_probe")
    print("=" * 65)
    test_ipc_failure_counter_and_log()
    test_snapshot_signature()
    test_healthz_keys()
    test_publish_silent_ipc()
    test_publish_silent_inprocess()
    print("\n" + "=" * 65)
    print(f"TOTAL: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
    print("=" * 65)
    if _fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
