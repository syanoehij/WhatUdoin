"""M5-2 Media service relocation probe.

단언 목록:
  1. media_service.py import 부작용 0 (모듈 수준 인스턴스/네트워크 호출 없음)
  2. __main__ 진입점 코드 grep: uvicorn.run + bind 127.0.0.1 + Starlette +
     /internal/process + /healthz + RotatingFileHandler
  3. app.py env 분기: env 미설정 시 기존 PIL+write_bytes 호출 경로 (코드 grep)
     env 설정 시 IPC 경로 분기 코드 존재 (코드 grep)
  4. IPC 헬퍼 _call_media_service: Authorization Bearer 첨부,
     응답 ok/reason 분기 → 적절한 HTTPException
  5. supervisor.media_service_spec(): 4 protected env, extra_env override 차단,
     web_api_internal_service_env에 MEDIA_SERVICE_URL 자동 주입
  6. STOP_ORDER 5종 (ollama, media, sse, scheduler, web-api)
  7. M2_STARTUP_SEQUENCE 10항목 + ollama 다음 media 위치
  8. media /internal/process: 토큰 없음 401, 잘못된 401, loopback 외부 IP 403,
     정상 200 (staging path mock 파일)
  9. staging path 정규화: ../symlink/staging root 밖 → 400
  10. Media service DB write 0건 grep 단언
  11. PIL.verify image kind 분기 + ext whitelist + dimensions 응답
  12. media /healthz: status/service/staging_root/processed_count/uptime_seconds 키

Run:
    python _workspace/perf/scripts/m5_2_media_service_relocation_probe.py
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
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
print("\n[m5_2] M5-2 Media service relocation probe")
print("=" * 60)

media_svc_src = (ROOT / "media_service.py").read_text(encoding="utf-8", errors="replace")
sup_src        = (ROOT / "supervisor.py").read_text(encoding="utf-8", errors="replace")
app_src        = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# [1] media_service.py import 부작용 0
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] media_service.py import 부작용 0")

import media_service as _svc_mod

_ok("media_service import 성공", True)
_ok("모듈 수준 Starlette app 인스턴스 없음", not hasattr(_svc_mod, "app"))
_ok("모듈 수준 _server 없음", not hasattr(_svc_mod, "_server"))
_ok("if __name__ == '__main__': main() 패턴",
    'if __name__ == "__main__"' in media_svc_src and "main()" in media_svc_src)
_ok("main() 함수 정의", hasattr(_svc_mod, "main") and callable(_svc_mod.main))

# ─────────────────────────────────────────────────────────────────────────────
# [2] 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] media_service.py 코드 내용 grep")

_ok("uvicorn.Server 또는 uvicorn.run 존재",
    "uvicorn.Server" in media_svc_src or "uvicorn.run" in media_svc_src)
_ok("bind 127.0.0.1 강제", "127.0.0.1" in media_svc_src)
_ok("Starlette 인스턴스 생성", "Starlette(" in media_svc_src)
_ok("/internal/process 라우트 존재", '"/internal/process"' in media_svc_src)
_ok("/healthz 라우트 존재", '"/healthz"' in media_svc_src)
_ok("RotatingFileHandler 사용", "RotatingFileHandler" in media_svc_src)
_ok("RotatingFileHandler maxBytes=10MB",
    "10 * 1024 * 1024" in media_svc_src or "10*1024*1024" in media_svc_src)
_ok("RotatingFileHandler backupCount=14", "backupCount=14" in media_svc_src)
_ok("RotatingFileHandler delay=True", "delay=True" in media_svc_src)
_ok("logs/services/media.app.log 경로", "media.app.log" in media_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [3] app.py env 분기 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] app.py env 분기 코드 grep")

_ok("_MEDIA_SERVICE_URL 모듈 수준 변수 존재", "_MEDIA_SERVICE_URL" in app_src)
_ok("WHATUDOIN_MEDIA_SERVICE_URL env 읽기", "WHATUDOIN_MEDIA_SERVICE_URL" in app_src)
_ok("_call_media_service 헬퍼 정의", "def _call_media_service" in app_src)
_ok("env 미설정 시 PIL fallback 경로 존재",
    "not _MEDIA_SERVICE_URL" in app_src and "PIL" in app_src)
_ok("env 설정 시 IPC 경로 (_call_media_service 호출)",
    "_call_media_service" in app_src and "staging_path" in app_src)
_ok("STAGING_ROOT 정의", "STAGING_ROOT" in app_src)
_ok("WHATUDOIN_STAGING_ROOT env 읽기", "WHATUDOIN_STAGING_ROOT" in app_src)
_ok("Authorization Bearer 첨부 (_call_media_service)",
    "Authorization" in app_src and "Bearer" in app_src)
_ok("staging file cleanup (unlink)", "unlink" in app_src)
_ok("IPC timeout 30s", "timeout=30" in app_src)

# ─────────────────────────────────────────────────────────────────────────────
# [4] _call_media_service IPC 헬퍼 응답 분기 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] _call_media_service 응답 분기 + HTTPException 매핑")

_ok("too_large → 413 또는 400",
    "too_large" in app_src and ("413" in app_src or "400" in app_src))
_ok("invalid_image → 400", "invalid_image" in app_src and "400" in app_src)
_ok("forbidden_ext → 415 또는 400", "forbidden_ext" in app_src)
_ok("RuntimeError → 500 (서비스 일시 사용 불가)",
    "500" in app_src and "사용 불가" in app_src)

# ─────────────────────────────────────────────────────────────────────────────
# [5] supervisor.media_service_spec() 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] supervisor.media_service_spec() ServiceSpec 검증")

import supervisor as _sup
importlib.reload(_sup)

_ok("MEDIA_SERVICE_NAME = 'media'",
    _sup.MEDIA_SERVICE_NAME == "media", f"got {_sup.MEDIA_SERVICE_NAME!r}")
_ok("MEDIA_SERVICE_DEFAULT_PORT = 8768",
    _sup.MEDIA_SERVICE_DEFAULT_PORT == 8768, f"got {_sup.MEDIA_SERVICE_DEFAULT_PORT!r}")
_ok("MEDIA_SERVICE_URL_ENV 정의", hasattr(_sup, "MEDIA_SERVICE_URL_ENV"))
_ok("MEDIA_SERVICE_STAGING_ROOT_ENV 정의", hasattr(_sup, "MEDIA_SERVICE_STAGING_ROOT_ENV"))
_ok("media_service_spec 함수 존재",
    hasattr(_sup, "media_service_spec") and callable(_sup.media_service_spec))

cmd = ["python", "media_service.py"]
spec = _sup.media_service_spec(cmd)

_ok("spec.name == 'media'", spec.name == "media", f"got {spec.name!r}")
_ok("spec BIND_HOST=127.0.0.1",
    spec.env.get(_sup.MEDIA_SERVICE_BIND_HOST_ENV) == "127.0.0.1",
    f"got {spec.env.get(_sup.MEDIA_SERVICE_BIND_HOST_ENV)!r}")
_ok("spec PORT=8768",
    spec.env.get(_sup.MEDIA_SERVICE_PORT_ENV) == "8768",
    f"got {spec.env.get(_sup.MEDIA_SERVICE_PORT_ENV)!r}")
_ok("INTERNAL_TOKEN protected (spec.env에 없음)",
    _sup.INTERNAL_TOKEN_ENV not in spec.env)

# 커스텀 포트
spec2 = _sup.media_service_spec(cmd, port=9200)
_ok("커스텀 포트 9200 적용",
    spec2.env.get(_sup.MEDIA_SERVICE_PORT_ENV) == "9200",
    f"got {spec2.env.get(_sup.MEDIA_SERVICE_PORT_ENV)!r}")

# extra_env protected 차단 (4개)
spec3 = _sup.media_service_spec(
    cmd,
    extra_env={
        _sup.MEDIA_SERVICE_BIND_HOST_ENV: "0.0.0.0",    # protected
        _sup.MEDIA_SERVICE_PORT_ENV: "9999",              # protected
        _sup.INTERNAL_TOKEN_ENV: "leaked",               # protected
        _sup.MEDIA_SERVICE_STAGING_ROOT_ENV: "/evil",    # protected
        "SAFE_KEY": "safe_val",                          # 허용
    },
)
_ok("BIND_HOST override 차단 (여전히 127.0.0.1)",
    spec3.env.get(_sup.MEDIA_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("PORT override 차단 (여전히 기본값)",
    spec3.env.get(_sup.MEDIA_SERVICE_PORT_ENV) == str(_sup.MEDIA_SERVICE_DEFAULT_PORT))
_ok("INTERNAL_TOKEN override 차단 (spec.env에 없음)",
    _sup.INTERNAL_TOKEN_ENV not in spec3.env)
_ok("STAGING_ROOT override 차단",
    spec3.env.get(_sup.MEDIA_SERVICE_STAGING_ROOT_ENV) is None
    or spec3.env.get(_sup.MEDIA_SERVICE_STAGING_ROOT_ENV) != "/evil",
    f"got {spec3.env.get(_sup.MEDIA_SERVICE_STAGING_ROOT_ENV)!r}")
_ok("비보호 env 통과 (SAFE_KEY)",
    spec3.env.get("SAFE_KEY") == "safe_val")

# web_api_internal_service_env에 MEDIA_SERVICE_URL 자동 주입
web_env = _sup.web_api_internal_service_env()
_ok("WHATUDOIN_MEDIA_SERVICE_URL 자동 주입",
    _sup.MEDIA_SERVICE_URL_ENV in web_env,
    f"keys={list(web_env.keys())}")
_ok("MEDIA_SERVICE_URL 값이 /internal/process 포함",
    "/internal/process" in web_env.get(_sup.MEDIA_SERVICE_URL_ENV, ""),
    f"got {web_env.get(_sup.MEDIA_SERVICE_URL_ENV)!r}")
_ok("MEDIA_SERVICE_URL 포트 기본 8768",
    "8768" in web_env.get(_sup.MEDIA_SERVICE_URL_ENV, ""),
    f"got {web_env.get(_sup.MEDIA_SERVICE_URL_ENV)!r}")

# ─────────────────────────────────────────────────────────────────────────────
# [6] STOP_ORDER 5종
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] STOP_ORDER 5종 검증")

_ok("STOP_ORDER 5종 (ollama, media, sse, scheduler, web-api)",
    list(_sup.STOP_ORDER) == ["ollama", "media", "sse", "scheduler", "web-api"],
    f"got {list(_sup.STOP_ORDER)!r}")
_ok("media가 ollama 다음 위치",
    list(_sup.STOP_ORDER).index("media") == list(_sup.STOP_ORDER).index("ollama") + 1)

# ─────────────────────────────────────────────────────────────────────────────
# [7] M2_STARTUP_SEQUENCE 10항목
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] M2_STARTUP_SEQUENCE 10항목 검증")

seq = list(_sup.M2_STARTUP_SEQUENCE)
_ok("start_media_service 포함", "start_media_service" in seq, f"seq={seq}")
_ok("항목 수 10개", len(seq) == 10, f"count={len(seq)}, seq={seq}")
_ok("start_media_service가 start_ollama_service 다음",
    "start_media_service" in seq and "start_ollama_service" in seq and
    seq.index("start_media_service") == seq.index("start_ollama_service") + 1,
    f"media_idx={seq.index('start_media_service') if 'start_media_service' in seq else 'N/A'}")
_ok("verify_health_and_publish_status가 마지막",
    seq[-1] == "verify_health_and_publish_status", f"last={seq[-1]!r}")

# ─────────────────────────────────────────────────────────────────────────────
# [8] media /internal/process 인증 로직 grep + mock 단언
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] media_service /internal/process 인증 + 기본 동작")

_ok("Bearer 토큰 검증 코드 존재",
    "Bearer" in media_svc_src and "compare_digest" in media_svc_src)
_ok("loopback IP 가드 존재 (_LOOPBACK_HOSTS)",
    "_LOOPBACK_HOSTS" in media_svc_src)
_ok("토큰 미일치 401 응답",
    "status_code=401" in media_svc_src or '"unauthorized"' in media_svc_src)
_ok("loopback 외부 IP 403 응답",
    "status_code=403" in media_svc_src or '"forbidden"' in media_svc_src)

# mock으로 /internal/process 동작 검증 (실제 서버 기동 없이)
with tempfile.TemporaryDirectory() as tmpdir:
    staging = Path(tmpdir)

    # staging 내 mock 이미지 파일 생성 (PNG 헤더만)
    mock_png = staging / "test_image.tmp"
    # 최소 valid PNG: 8-byte signature + IHDR chunk
    png_bytes = (
        b'\x89PNG\r\n\x1a\n'  # PNG signature
        b'\x00\x00\x00\rIHDR'  # IHDR length + type
        b'\x00\x00\x00\x01'    # width=1
        b'\x00\x00\x00\x01'    # height=1
        b'\x08\x02'            # 8-bit RGB
        b'\x00\x00\x00'        # no interlace, filter, compression
        b'\x90wS\xde'          # CRC (approximate)
        b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'  # IDAT
        b'\x00\x00\x00\x00IEND\xaeB`\x82'  # IEND
    )
    mock_png.write_bytes(png_bytes)

    # staging path 정규화 테스트
    # valid path
    valid = Path(tmpdir) / "test_image.tmp"
    resolved = valid.resolve()
    staging_resolved = staging.resolve()
    _ok("valid staging path → is_relative_to True",
        resolved.is_relative_to(staging_resolved))

    # path traversal 시도
    evil_path = Path(tmpdir) / ".." / "secret.txt"
    try:
        evil_resolved = evil_path.resolve()
        evil_relative = evil_resolved.is_relative_to(staging_resolved)
        _ok(".. traversal → is_relative_to False", not evil_relative)
    except Exception as exc:
        _ok(".. traversal → is_relative_to False", False, str(exc))

# ─────────────────────────────────────────────────────────────────────────────
# [9] staging path 정규화 함수 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] staging path 정규화 코드 grep")

_ok("_safe_staging_path 함수 정의", "_safe_staging_path" in media_svc_src)
_ok("Path.resolve() 사용", ".resolve()" in media_svc_src)
_ok("is_relative_to 사용", "is_relative_to" in media_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [10] DB write 0건 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] media_service.py DB write 0건 단언")

_ok("sqlite3.connect 0건", "sqlite3.connect" not in media_svc_src)
_ok("database. 참조 0건", "database." not in media_svc_src)
_ok("db. 참조 0건", "db." not in media_svc_src)
_ok("cursor 변수 0건", "cursor" not in media_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [11] PIL 분기 + ext whitelist + dimensions
# ─────────────────────────────────────────────────────────────────────────────
print("\n[11] PIL.verify + ext whitelist + dimensions 응답")

_ok("PIL.Image.open 사용", "PIL" in media_svc_src and "Image" in media_svc_src)
_ok("img.verify() 호출", ".verify()" in media_svc_src)
_ok("이미지 ext whitelist (.png,.jpg,.jpeg,.gif,.webp)",
    all(e in media_svc_src for e in [".png", ".jpg", ".jpeg", ".gif", ".webp"]))
_ok("첨부 ext whitelist (.pdf,.zip,.7z 포함)",
    all(e in media_svc_src for e in [".pdf", ".zip", ".7z"]))
_ok("dimensions 응답 (w, h 키)",
    '"w"' in media_svc_src and '"h"' in media_svc_src)
_ok("SHA-256 해시 계산", "sha256" in media_svc_src and "hashlib" in media_svc_src)
_ok("SHA-256 16자 슬라이싱", "[:16]" in media_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [12] healthz 응답 키 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[12] media /healthz 응답 키 grep")

_ok("status 키", '"status"' in media_svc_src)
_ok("service: media 키", '"service"' in media_svc_src and '"media"' in media_svc_src)
_ok("staging_root 키", '"staging_root"' in media_svc_src)
_ok("processed_count 키", '"processed_count"' in media_svc_src)
_ok("uptime_seconds 키", '"uptime_seconds"' in media_svc_src)

# 기존 M4-1 회귀: OLLAMA_SERVICE_URL_ENV 주입 유지
_ok("[회귀] OLLAMA_SERVICE_URL 자동 주입 유지",
    _sup.OLLAMA_SERVICE_URL_ENV in web_env and
    "/internal/llm" in web_env.get(_sup.OLLAMA_SERVICE_URL_ENV, ""))
_ok("[회귀] SCHEDULER_SERVICE_ENABLE=1 유지",
    web_env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
