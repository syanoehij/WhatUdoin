"""Phase 70: M5-1+M5-2 Media service relocation (standalone runner).

A. media_service.py 구조 단언 (import 부작용 0, 코드 grep)
B. app.py env 분기 단언 (IPC/in-process 경로 분기 코드 존재)
C. supervisor media_service_spec + STARTUP_SEQUENCE + STOP_ORDER 단언
D. 회귀: phase54~69 핵심 항목 재확인

실행:
    python tests/phase70_m5_media_service_relocation.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
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
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _read(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace")


media_svc_src = _read(ROOT / "media_service.py")
sup_src        = _read(ROOT / "supervisor.py")
app_src        = _read(ROOT / "app.py")

print("\n[phase70] M5-1+M5-2 Media service relocation")
print("=" * 60)

# ─────────────────────────────────────────────────────────────────────────────
# A. media_service.py 구조
# ─────────────────────────────────────────────────────────────────────────────
print("\n[A] media_service.py 구조 단언")

import media_service as _svc_mod

_ok("media_service import 성공", True)
_ok("모듈 수준 Starlette/Server 인스턴스 없음",
    not hasattr(_svc_mod, "app") and not hasattr(_svc_mod, "_server"))
_ok("if __name__ == '__main__' 진입점",
    'if __name__ == "__main__"' in media_svc_src)
_ok("main() 함수 정의",
    hasattr(_svc_mod, "main") and callable(_svc_mod.main))
_ok("/internal/process 라우트 코드 존재",
    '"/internal/process"' in media_svc_src)
_ok("/healthz 라우트 코드 존재",
    '"/healthz"' in media_svc_src)
_ok("RotatingFileHandler 사용",
    "RotatingFileHandler" in media_svc_src)
_ok("loopback bind 강제 (127.0.0.1)",
    "127.0.0.1" in media_svc_src)
_ok("Bearer 토큰 + compare_digest 인증",
    "Bearer" in media_svc_src and "compare_digest" in media_svc_src)
_ok("loopback 가드 (_LOOPBACK_HOSTS)",
    "_LOOPBACK_HOSTS" in media_svc_src)
_ok("401 응답 (토큰 불일치)", "401" in media_svc_src)
_ok("403 응답 (loopback 외부 IP)", "403" in media_svc_src)
_ok("healthz staging_root 키 포함", '"staging_root"' in media_svc_src)
_ok("healthz processed_count 키 포함", '"processed_count"' in media_svc_src)
_ok("healthz uptime_seconds 키 포함", '"uptime_seconds"' in media_svc_src)
_ok("media logs → media.app.log", "media.app.log" in media_svc_src)
_ok("DB write 0건 (sqlite3.connect 없음)", "sqlite3.connect" not in media_svc_src)
_ok("DB write 0건 (db. 참조 없음)", "db." not in media_svc_src)
_ok("DB write 0건 (database. 참조 없음)", "database." not in media_svc_src)
_ok("staging path 정규화 (_safe_staging_path)", "_safe_staging_path" in media_svc_src)
_ok("Path.resolve() + is_relative_to 우회 차단",
    ".resolve()" in media_svc_src and "is_relative_to" in media_svc_src)
_ok("PIL.Image.verify 이미지 검증", ".verify()" in media_svc_src)
_ok("dimensions 응답 (w, h)",
    '"w"' in media_svc_src and '"h"' in media_svc_src)
_ok("SHA-256 해시 16자", "sha256" in media_svc_src and "[:16]" in media_svc_src)
_ok("이미지 ext whitelist (.png,.jpg,.jpeg,.gif,.webp)",
    all(e in media_svc_src for e in [".png", ".jpg", ".jpeg", ".gif", ".webp"]))
_ok("첨부 ext whitelist (.pdf,.zip,.7z)",
    all(e in media_svc_src for e in [".pdf", ".zip", ".7z"]))
_ok("WHATUDOIN_STAGING_ROOT env 읽기", "WHATUDOIN_STAGING_ROOT" in media_svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# B. app.py env 분기
# ─────────────────────────────────────────────────────────────────────────────
print("\n[B] app.py env 분기 단언")

_ok("_MEDIA_SERVICE_URL 모듈 수준 변수 존재", "_MEDIA_SERVICE_URL" in app_src)
_ok("WHATUDOIN_MEDIA_SERVICE_URL env 읽기", "WHATUDOIN_MEDIA_SERVICE_URL" in app_src)
_ok("_call_media_service 헬퍼 정의", "def _call_media_service" in app_src)
_ok("env 미설정 시 in-process fallback (not _MEDIA_SERVICE_URL)",
    "not _MEDIA_SERVICE_URL" in app_src)
_ok("env 미설정 시 PIL 직접 검증", "not _MEDIA_SERVICE_URL" in app_src and "PIL" in app_src)
_ok("env 설정 시 staging 파일 저장 + IPC 호출",
    "staging_file" in app_src and "_call_media_service" in app_src)
_ok("staging file cleanup (finally + unlink)", "unlink" in app_src)
_ok("STAGING_ROOT 정의 + mkdir", "STAGING_ROOT" in app_src and "mkdir" in app_src)
_ok("IPC 헬퍼 Authorization Bearer 첨부",
    "Authorization" in app_src and "Bearer" in app_src)
_ok("IPC timeout=30s", "timeout=30" in app_src)
_ok("IPC ConnectionError → 500 + 사용 불가 안내",
    "RuntimeError" in app_src and "사용 불가" in app_src)
_ok("staging → MEETINGS_DIR 이동은 Web API (rename)",
    "rename" in app_src)

# ─────────────────────────────────────────────────────────────────────────────
# C. supervisor 단언
# ─────────────────────────────────────────────────────────────────────────────
print("\n[C] supervisor 단언")

import supervisor as _sup
importlib.reload(_sup)

_ok("MEDIA_SERVICE_NAME = 'media'",
    _sup.MEDIA_SERVICE_NAME == "media")
_ok("MEDIA_SERVICE_DEFAULT_PORT = 8768",
    _sup.MEDIA_SERVICE_DEFAULT_PORT == 8768)
_ok("MEDIA_SERVICE_URL_ENV 정의", hasattr(_sup, "MEDIA_SERVICE_URL_ENV"))
_ok("MEDIA_SERVICE_STAGING_ROOT_ENV 정의", hasattr(_sup, "MEDIA_SERVICE_STAGING_ROOT_ENV"))
_ok("media_service_spec 함수 존재",
    hasattr(_sup, "media_service_spec") and callable(_sup.media_service_spec))

cmd = ["python", "media_service.py"]
spec = _sup.media_service_spec(cmd)

_ok("spec.name == 'media'", spec.name == "media", f"got {spec.name!r}")
_ok("spec BIND_HOST=127.0.0.1",
    spec.env.get(_sup.MEDIA_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("spec PORT=8768",
    spec.env.get(_sup.MEDIA_SERVICE_PORT_ENV) == "8768")
_ok("INTERNAL_TOKEN protected (spec.env에 없음)",
    _sup.INTERNAL_TOKEN_ENV not in spec.env)

# protected env 차단 (4개)
spec_bad = _sup.media_service_spec(
    cmd,
    extra_env={
        _sup.MEDIA_SERVICE_BIND_HOST_ENV: "0.0.0.0",
        _sup.MEDIA_SERVICE_PORT_ENV: "9999",
        _sup.INTERNAL_TOKEN_ENV: "leaked",
        _sup.MEDIA_SERVICE_STAGING_ROOT_ENV: "/evil",
        "SAFE_KEY": "safe_val",
    },
)
_ok("BIND_HOST override 차단",
    spec_bad.env.get(_sup.MEDIA_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("PORT override 차단",
    spec_bad.env.get(_sup.MEDIA_SERVICE_PORT_ENV) == "8768")
_ok("INTERNAL_TOKEN override 차단",
    _sup.INTERNAL_TOKEN_ENV not in spec_bad.env)
_ok("STAGING_ROOT override 차단 (/evil 아님)",
    spec_bad.env.get(_sup.MEDIA_SERVICE_STAGING_ROOT_ENV) != "/evil")
_ok("비보호 env 통과 (SAFE_KEY)",
    spec_bad.env.get("SAFE_KEY") == "safe_val")

# STOP_ORDER: ollama/media/sse/scheduler/web-api 순서 포함, 5종 이상
# (4단계 이후 front-router가 추가될 수 있음 — 5+ 허용)
_stop = list(_sup.STOP_ORDER)
_ok("STOP_ORDER ollama/media/sse/scheduler/web-api 포함 5종 이상",
    len(_stop) >= 5 and all(x in _stop for x in ["ollama", "media", "sse", "scheduler", "web-api"]),
    f"got {_stop!r}")

# STARTUP_SEQUENCE 10항목
seq = list(_sup.M2_STARTUP_SEQUENCE)
_ok("start_media_service 포함", "start_media_service" in seq, f"seq={seq}")
_ok("항목 수 10개", len(seq) == 10, f"count={len(seq)}")
_ok("start_media_service가 start_ollama_service 다음",
    "start_media_service" in seq and "start_ollama_service" in seq and
    seq.index("start_media_service") == seq.index("start_ollama_service") + 1)
_ok("verify_health_and_publish_status가 마지막",
    seq[-1] == "verify_health_and_publish_status", f"last={seq[-1]!r}")

# web_api_internal_service_env
web_env = _sup.web_api_internal_service_env()
_ok("MEDIA_SERVICE_URL 자동 주입",
    _sup.MEDIA_SERVICE_URL_ENV in web_env)
_ok("MEDIA_SERVICE_URL /internal/process 포함",
    "/internal/process" in web_env.get(_sup.MEDIA_SERVICE_URL_ENV, ""))

# ─────────────────────────────────────────────────────────────────────────────
# D. 회귀: phase54~69 핵심 항목
# ─────────────────────────────────────────────────────────────────────────────
print("\n[D] 회귀: phase54~69 핵심 항목")

# M4-1: ollama 관련 유지
_ok("OLLAMA_SERVICE_URL 자동 주입 유지",
    _sup.OLLAMA_SERVICE_URL_ENV in web_env and
    "/internal/llm" in web_env.get(_sup.OLLAMA_SERVICE_URL_ENV, ""))

# scheduler 관련 유지
_ok("SCHEDULER_SERVICE_ENABLE=1 유지",
    web_env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")

# ollama_service_spec 회귀
ollama_spec = _sup.ollama_service_spec(["python", "ollama_service.py"])
_ok("ollama_service_spec name == 'ollama'",
    ollama_spec.name == "ollama")

# app.py 회귀: _scheduler_service_enabled 분기 여전히 존재
_ok("app.py _scheduler_service_enabled 분기 유지",
    "_scheduler_service_enabled" in app_src)

# app.py 회귀: _MEDIA_SERVICE_URL 미설정 시 PIL 기존 경로 코드 존재
_ok("env 미설정 fallback: PIL 직접 검증 코드 보존",
    "PIL" in app_src and "write_bytes" in app_src)

# supervisor STOP_ORDER에 기존 4개 모두 포함
for svc in ("ollama", "sse", "scheduler", "web-api"):
    _ok(f"STOP_ORDER에 {svc} 포함", svc in _sup.STOP_ORDER)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
