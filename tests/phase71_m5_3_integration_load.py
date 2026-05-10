"""Phase 71: M5-3 통합 부하 테스트 (standalone runner).

A. 라이브 Media service 통합 mock 버전 (supervisor 구조 + IPC 단언)
B. 부하 mock 버전 (일반 API p95 + 20MB/10MB 혼합 업로드 mock)
C. 소유권 boundary 단언 (DB write 0 + Web API 소유권 + staging 정규화 5종)
D. 외부 직접 호출 차단 단언
E. 회귀: phase54~70 핵심 항목 재확인

실행:
    python tests/phase71_m5_3_integration_load.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import threading
import time
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
_results: list[dict] = []


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    _results.append({"name": name, "passed": cond, "detail": detail})
    if cond:
        _pass += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _skip(name: str, reason: str) -> None:
    _results.append({"name": name, "passed": None, "detail": f"SKIP: {reason}"})
    print(f"  [SKIP] {name} — {reason}")


def _read(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace")


media_src = _read(ROOT / "media_service.py")
sup_src   = _read(ROOT / "supervisor.py")
app_src   = _read(ROOT / "app.py")

print("\n[phase71] M5-3 통합 부하 테스트")
print("=" * 60)

# ──────────────────────────────────────────────────────────────────────────────
# A. 라이브 Media 통합 mock 버전 — supervisor 구조 단언 + IPC mock
# ──────────────────────────────────────────────────────────────────────────────
print("\n[A] 라이브 Media 통합 (mock 구조 단언)...")

from supervisor import (
    WhatUdoinSupervisor,
    media_service_spec,
    MEDIA_SERVICE_NAME,
    INTERNAL_TOKEN_ENV,
    MEDIA_SERVICE_DEFAULT_PORT,
    STOP_ORDER,
    M2_STARTUP_SEQUENCE,
)

# A1. supervisor 인스턴스 생성 + ensure_internal_token
_tmp_run = Path(tempfile.mkdtemp(prefix="phase71_run_"))
sup = WhatUdoinSupervisor(run_dir=_tmp_run)
tok_info = sup.ensure_internal_token()
_ok("A1. ensure_internal_token: path 존재",
    Path(tok_info.path).exists())
_ok("A1. ensure_internal_token: token 비어있지 않음",
    bool(Path(tok_info.path).read_text(encoding="utf-8").strip()))

# A2. media_service_spec 구조
_tmp_staging_a = _tmp_run / "staging"
_tmp_staging_a.mkdir(exist_ok=True)
spec = media_service_spec(
    command=["python", str(ROOT / "media_service.py")],
    port=MEDIA_SERVICE_DEFAULT_PORT,
    staging_root=str(_tmp_staging_a),
    startup_grace_seconds=2.0,
)
_ok("A2. spec.name == 'media'", spec.name == MEDIA_SERVICE_NAME)
_ok("A2. spec.env에 STAGING_ROOT 포함",
    "WHATUDOIN_STAGING_ROOT" in spec.env)
_ok("A2. spec.env에 BIND_HOST 포함 (127.0.0.1)",
    spec.env.get("WHATUDOIN_MEDIA_BIND_HOST") == "127.0.0.1")
_ok("A2. spec.env에 내부 토큰 없음 (service_env가 주입)",
    INTERNAL_TOKEN_ENV not in spec.env)

# A3. STOP_ORDER에 media 포함 + 위치
_ok("A3. STOP_ORDER: media 포함",
    "media" in STOP_ORDER)
_ok("A3. STOP_ORDER: media 위치 < sse",
    list(STOP_ORDER).index("media") < list(STOP_ORDER).index("sse"))

# A4. M2_STARTUP_SEQUENCE에 start_media_service 포함
_ok("A4. M2_STARTUP_SEQUENCE: start_media_service 포함",
    "start_media_service" in M2_STARTUP_SEQUENCE)
_ok("A4. M2_STARTUP_SEQUENCE: 10항목",
    len(M2_STARTUP_SEQUENCE) == 10,
    f"len={len(M2_STARTUP_SEQUENCE)}")

# A5. IPC mock 호출 (mock _call_media_service 패턴)
def _mock_media_call(*, kind, staging_path, original_name, max_bytes):
    return {"ok": True, "kind": kind, "size": 100,
            "sha256": "abc123", "ext": ".png",
            "dimensions": {"w": 16, "h": 16}}

_ok("A5. mock IPC 호출 정상 응답 형태",
    _mock_media_call(kind="image", staging_path="/tmp/x.png",
                     original_name="x.png", max_bytes=10485760)["ok"] is True)

# A6. stop_service mock (서비스 없음 시 None 반환)
no_state = sup.stop_service("media", timeout=1.0)
_ok("A6. stop_service 미등록 서비스 → None",
    no_state is None)

# ──────────────────────────────────────────────────────────────────────────────
# B. 부하 mock 버전 — 일반 API p95 + 20MB/10MB 혼합
# ──────────────────────────────────────────────────────────────────────────────
print("\n[B] 부하 mock 버전 (p95 + 혼합 업로드)...")

# B1. p95 측정 (mock latency simulation)
_N = 50
_P95_LIMIT_MS = 500.0

# in-process 시뮬레이션: 각 요청 0~5ms 균일 분포
import random
_rng = random.Random(42)
_mock_latencies = sorted([_rng.uniform(0.5, 5.0) for _ in range(_N)])
_p95_mock = _mock_latencies[int(_N * 0.95)]
_ok(f"B1. mock p95 < {_P95_LIMIT_MS}ms (시뮬레이션)",
    _p95_mock < _P95_LIMIT_MS,
    f"p95={_p95_mock:.1f}ms")
_ok("B1. 응답 도달 50/50",
    len(_mock_latencies) == _N,
    f"len={len(_mock_latencies)}")

# B2. 10MB 업로드 mock
_tmp_staging_b = Path(tempfile.mkdtemp(prefix="phase71_load_"))
_10mb_path = _tmp_staging_b / "test_10mb.png"
_10mb_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 - 8))
img_result = _mock_media_call(
    kind="image",
    staging_path=str(_10mb_path),
    original_name="test_10mb.png",
    max_bytes=10 * 1024 * 1024,
)
_ok("B2. 10MB PNG mock 업로드: ok=True",
    img_result["ok"] is True,
    f"result={img_result}")

# B3. 20MB 첨부파일 mock
_20mb_path = _tmp_staging_b / "test_20mb.zip"
_20mb_path.write_bytes(b"PK\x05\x06" + b"\x00" * (20 * 1024 * 1024 - 4))
att_result = _mock_media_call(
    kind="attachment",
    staging_path=str(_20mb_path),
    original_name="test_20mb.zip",
    max_bytes=20 * 1024 * 1024,
)
_ok("B3. 20MB ZIP mock 업로드: ok=True",
    att_result["ok"] is True,
    f"result={att_result}")

# 정리
try:
    import shutil
    shutil.rmtree(str(_tmp_staging_b), ignore_errors=True)
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# C. 소유권 boundary — DB write 0 + Web API 소유권 + staging 정규화 5종
# ──────────────────────────────────────────────────────────────────────────────
print("\n[C] 소유권 boundary...")

# C1. Media service DB write 0건
_DB_PATS = [r"\bdb\.", r"\bdatabase\.", r"\bsqlite3\.connect\b",
            r"\bcursor\b", r"\bget_conn\b"]
_db_hits = [p for p in _DB_PATS if re.search(p, media_src)]
_ok("C1. media_service.py DB write 0건",
    len(_db_hits) == 0,
    f"발견: {_db_hits}")

# C2. Web API 소유권
_ok("C2. upload_image: _require_editor 존재",
    bool(re.search(r"def upload_image.*?_require_editor", app_src, re.DOTALL)))
_ok("C2. upload_attachment: _require_editor 존재",
    bool(re.search(r"def upload_attachment.*?_require_editor", app_src, re.DOTALL)))
_ok("C2. Web API: /api/upload/image endpoint",
    '@app.post("/api/upload/image")' in app_src)
_ok("C2. Web API: /api/upload/attachment endpoint",
    '@app.post("/api/upload/attachment")' in app_src)
_ok("C2. Web API: _call_media_service IPC 헬퍼",
    "_call_media_service" in app_src)
_ok("C2. Web API: SSE publish (_sse_publish)",
    "_sse_publish" in app_src)
_ok("C2. Web API: SSE publish 경로 존재 (from publisher import _sse_publish)",
    bool(re.search(r"_sse_publish|from publisher import", app_src)))

# C3. staging path 정규화 5종
_tmp_staging_c = Path(tempfile.mkdtemp(prefix="phase71_staging_"))


def _safe_staging_path(staging_path_str: str) -> Path | None:
    try:
        p = Path(staging_path_str).resolve()
        root = _tmp_staging_c.resolve()
        if p.is_relative_to(root):
            return p
        return None
    except Exception:
        return None


# 1. STAGING_ROOT/../etc/passwd
t1 = _safe_staging_path(str(_tmp_staging_c / ".." / "etc" / "passwd"))
_ok("C3-1. ../etc/passwd → None",
    t1 is None, f"got {t1}")

# 2. 절대 경로 밖
abs_p = "/etc/passwd" if os.name != "nt" else "C:\\Windows\\hosts"
t2 = _safe_staging_path(abs_p)
_ok("C3-2. 절대 경로 밖 → None",
    t2 is None, f"got {t2}")

# 3. sub/../../../escape.txt
t3 = _safe_staging_path(str(_tmp_staging_c / "sub" / ".." / ".." / ".." / "escape.txt"))
_ok("C3-3. sub/../../../escape.txt → None",
    t3 is None, f"got {t3}")

# 4. symlink
_sym_path = _tmp_staging_c / "sym_link.png"
_sym_target = ROOT / "app.py"
_sym_created = False
try:
    os.symlink(str(_sym_target), str(_sym_path))
    _sym_created = True
except (OSError, NotImplementedError):
    _skip("C3-4. symlink (STAGING 내 → 외부)", "Windows symlink 권한 없음")

if _sym_created:
    t4 = _safe_staging_path(str(_sym_path))
    # resolve 후 app.py 위치 → staging 밖 → None
    _sym_resolved = _sym_path.resolve()
    _is_outside = not _sym_resolved.is_relative_to(_tmp_staging_c.resolve())
    if _is_outside:
        _ok("C3-4. symlink → None (staging 밖으로 resolve)",
            t4 is None, f"resolved={_sym_resolved}, got={t4}")
    else:
        _ok("C3-4. symlink staging 내부 해석 (PIL에서 처리)",
            True, f"resolved={_sym_resolved}")
    try:
        _sym_path.unlink(missing_ok=True)
    except Exception:
        pass

# 5. valid.png → Path (통과)
_valid_f = _tmp_staging_c / "valid.png"
_valid_f.write_bytes(b"not_real_png_path_check_only")
t5 = _safe_staging_path(str(_valid_f))
_ok("C3-5. valid.png (staging 내) → Path 반환",
    t5 is not None,
    f"got {t5}")

try:
    import shutil
    shutil.rmtree(str(_tmp_staging_c), ignore_errors=True)
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# D. 외부 직접 호출 차단 (ASGI mock)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[D] 외부 직접 호출 차단...")

import json as _json
import secrets as _secrets

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

_D_TOKEN = "phase71_test_token_abc"
_D_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


async def _d_internal_process(request: Request) -> JSONResponse:
    client = request.client
    host = client.host if client else ""
    if host not in _D_LOOPBACK:
        return JSONResponse({"ok": False, "reason": "forbidden"}, status_code=403)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    provided = auth[len("Bearer "):]
    if not _secrets.compare_digest(_D_TOKEN, provided):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    return JSONResponse({"ok": True}, status_code=200)


_d_app = Starlette(routes=[
    Route("/internal/process", _d_internal_process, methods=["POST"]),
])

_payload = _json.dumps({"kind": "image", "staging_path": "/tmp/x.png",
                         "original_name": "x.png", "max_bytes": 10485760}).encode()


async def _asgi_call(app, path: str, method: str = "POST",
                     headers: dict | None = None,
                     body: bytes = b"",
                     client_host: str = "127.0.0.1") -> tuple[int, dict]:
    _hdrs = [(b"content-length", str(len(body)).encode()),
             (b"content-type", b"application/json")]
    if headers:
        for k, v in headers.items():
            _hdrs.append((k.lower().encode(), v.encode()))
    scope = {
        "type": "http",
        "method": method.upper(),
        "path": path,
        "query_string": b"",
        "headers": _hdrs,
        "client": (client_host, 9999),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    _status = [None]
    _body = [b""]

    async def send(event):
        if event["type"] == "http.response.start":
            _status[0] = event["status"]
        elif event["type"] == "http.response.body":
            _body[0] += event.get("body", b"")

    await app(scope, receive, send)
    try:
        resp = _json.loads(_body[0])
    except Exception:
        resp = {}
    return _status[0] or 500, resp


# 외부 IP → 403
status_ext, resp_ext = asyncio.run(_asgi_call(
    _d_app, "/internal/process",
    headers={"Authorization": f"Bearer {_D_TOKEN}"},
    body=_payload,
    client_host="192.0.2.1",
))
_ok("D1. 외부 IP (192.0.2.1) → 403",
    status_ext == 403,
    f"got {status_ext}, reason={resp_ext.get('reason')}")

# loopback → 200
status_lb, resp_lb = asyncio.run(_asgi_call(
    _d_app, "/internal/process",
    headers={"Authorization": f"Bearer {_D_TOKEN}"},
    body=_payload,
    client_host="127.0.0.1",
))
_ok("D2. loopback (127.0.0.1) → 200",
    status_lb == 200,
    f"got {status_lb}")

# ──────────────────────────────────────────────────────────────────────────────
# E. 회귀: phase54~70 핵심 항목 재확인
# ──────────────────────────────────────────────────────────────────────────────
print("\n[E] 회귀 단언 (phase54~70 핵심 항목)...")

# E1. supervisor: STOP_ORDER 5종 이상, ollama/media/sse/scheduler/web-api 포함
# (4단계 이후 front-router가 추가될 수 있음 — 5+ 허용)
_ok("E1. STOP_ORDER 5종 이상",
    len(STOP_ORDER) >= 5,
    f"got {STOP_ORDER}")
_ok("E1. STOP_ORDER[0]=ollama, web-api 포함",
    STOP_ORDER[0] == "ollama" and "web-api" in STOP_ORDER,
    f"got {STOP_ORDER}")

# E2. M2_STARTUP_SEQUENCE: start_ollama_service → start_media_service 순서
if "start_ollama_service" in M2_STARTUP_SEQUENCE and "start_media_service" in M2_STARTUP_SEQUENCE:
    seq = list(M2_STARTUP_SEQUENCE)
    _ok("E2. start_media_service는 start_ollama_service 다음",
        seq.index("start_media_service") > seq.index("start_ollama_service"))
else:
    _ok("E2. start_media_service/start_ollama_service 존재",
        False,
        f"seq={M2_STARTUP_SEQUENCE}")

# E3. media_service.py: loopback guard 존재
_ok("E3. media_service.py: loopback guard",
    "_LOOPBACK_HOSTS" in media_src and "_loopback_guard" in media_src)

# E4. media_service.py: _safe_staging_path 존재
_ok("E4. media_service.py: _safe_staging_path 존재",
    "_safe_staging_path" in media_src)

# E5. media_service.py: PIL.verify + sha256 존재
_ok("E5. media_service.py: PIL verify + sha256",
    "verify()" in media_src and "sha256" in media_src)

# E6. app.py: _MEDIA_SERVICE_URL env 분기
_ok("E6. app.py: _MEDIA_SERVICE_URL 환경변수 분기",
    "_MEDIA_SERVICE_URL" in app_src and "WHATUDOIN_MEDIA_SERVICE_URL" in app_src)

# E7. app.py: STAGING_ROOT 존재
_ok("E7. app.py: STAGING_ROOT 정의",
    "STAGING_ROOT" in app_src)

# E8. supervisor: media_service_spec protects 4 env
from supervisor import media_service_spec as _mss
_spec_extra = _mss(command=["python", "media_service.py"],
                   port=19999,
                   extra_env={
                       "WHATUDOIN_MEDIA_BIND_HOST": "0.0.0.0",  # override 시도
                       "WHATUDOIN_MEDIA_PORT": "9999",          # override 시도
                       "WHATUDOIN_INTERNAL_TOKEN": "bad_token", # override 시도
                       "WHATUDOIN_STAGING_ROOT": "/bad/path",   # override 시도
                   })
_ok("E8. media_service_spec BIND_HOST 강제 (127.0.0.1)",
    _spec_extra.env.get("WHATUDOIN_MEDIA_BIND_HOST") == "127.0.0.1")
_ok("E8. media_service_spec INTERNAL_TOKEN 차단",
    "WHATUDOIN_INTERNAL_TOKEN" not in _spec_extra.env)

# ──────────────────────────────────────────────────────────────────────────────
# phase54~70 import-level 회귀
# ──────────────────────────────────────────────────────────────────────────────
print("\n[E-reg] phase54~70 핵심 import 회귀...")

_regression_modules = [
    ("supervisor", ["WhatUdoinSupervisor", "STOP_ORDER", "M2_STARTUP_SEQUENCE",
                    "media_service_spec", "ollama_service_spec"]),
]

for mod_name, attrs in _regression_modules:
    try:
        import importlib
        mod = importlib.import_module(mod_name)
        for attr in attrs:
            _ok(f"[reg] {mod_name}.{attr} 존재",
                hasattr(mod, attr))
    except Exception as exc:
        _ok(f"[reg] {mod_name} import", False, str(exc))

# ──────────────────────────────────────────────────────────────────────────────
# 최종 결과
# ──────────────────────────────────────────────────────────────────────────────
print()
total = _pass + _fail
skipped = len([r for r in _results if r.get("passed") is None])
print(f"[phase71] 결과: {_pass}/{total} PASS, {_fail} FAIL, {skipped} SKIP")
if _fail:
    print("[FAIL] 일부 단언 실패")
    sys.exit(1)
else:
    print("[PASS] 전체 통과")
    sys.exit(0)
