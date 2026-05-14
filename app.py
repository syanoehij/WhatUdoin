from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
import ipaddress
import json
import os
import hashlib
import re
import secrets
import uuid

import requests as _requests

import io
import zipfile
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request, HTTPException, Response, UploadFile, File
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import database as db
import llm_parser
import auth
import crypto
import passwords
import backup
from broker import wu_broker
import publisher as _publisher_mod
from publisher import publish as _sse_publish
from permissions import _can_read_doc, _can_read_checklist
from mcp_server import mcp, mount_mcp, verify_bearer_token, _mcp_user

scheduler = AsyncIOScheduler()

# M3-2: Scheduler service 분기
# 설정 시 → Web API lifespan은 APScheduler 시작/job 등록/finalize 즉시호출 skip
# 미설정 → 단일 프로세스 fallback, 기존 동작 그대로
_scheduler_service_enabled = bool(os.environ.get("WHATUDOIN_SCHEDULER_SERVICE"))

# ── 경로 해석 ─────────────────────────────────────────────
# PyInstaller 번들: WHATUDOIN_BASE_DIR = sys._MEIPASS (읽기전용 자원)
#                   WHATUDOIN_RUN_DIR  = exe 옆 디렉토리 (쓰기 가능)
# 개발 실행:        두 값 모두 소스 파일 디렉토리
_BASE_DIR = Path(os.environ.get("WHATUDOIN_BASE_DIR", Path(__file__).parent))
_RUN_DIR  = Path(os.environ.get("WHATUDOIN_RUN_DIR",  Path(__file__).parent))

# 회의록 이미지 저장 루트 (앱 기동 전에 생성해야 StaticFiles 마운트 가능)
MEETINGS_DIR = _RUN_DIR / "meetings"
MEETINGS_DIR.mkdir(exist_ok=True)

# 팀 기능 그룹 D #24: 체크·일정 등 신규 첨부 저장 루트 — `uploads/teams/{team_id}/{kind}/...`
# 계획서 §14. 문서(meetings) 첨부와 분리해 팀별 폴더로 격리한다.
TEAMS_UPLOAD_DIR = _RUN_DIR / "uploads" / "teams"
TEAMS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# M5-2: Media service IPC 분기
# WHATUDOIN_MEDIA_SERVICE_URL 미설정 시 기존 in-process 동작 100% 유지.
_MEDIA_SERVICE_URL: str = os.environ.get("WHATUDOIN_MEDIA_SERVICE_URL", "").strip()

# M5-1: staging 루트 (Media service와 공유 — WHATUDOIN_STAGING_ROOT env로 통일)
STAGING_ROOT = Path(
    os.environ.get("WHATUDOIN_STAGING_ROOT", str(_RUN_DIR / "staging"))
)
STAGING_ROOT.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB 파일 존재 시에만 백업 (첫 부팅은 빈 DB 백업 방지)
    if Path(db.DB_PATH).exists():
        try:
            await run_in_threadpool(backup.run_backup, db.DB_PATH, _RUN_DIR)
        except Exception as _e:
            import logging
            logging.getLogger("whatudoin").warning("서버 시작 백업 실패: %s", _e)
    db.init_db()
    db.cleanup_expired_sessions()  # 만료/레거시 NULL 세션 정리 (P1-6)
    # SSE broker에 현재 이벤트 루프 등록 (sync 핸들러에서 publish 시 필수)
    wu_broker.start_on_loop(asyncio.get_running_loop())
    saved_url = db.get_setting("ollama_url")
    if saved_url:
        llm_parser.set_ollama_base_url(saved_url)
    saved_timeout = db.get_setting("ollama_timeout")
    if saved_timeout:
        llm_parser.set_ollama_timeout(int(saved_timeout))
    saved_num_ctx = db.get_setting("ollama_num_ctx")
    if saved_num_ctx:
        llm_parser.set_ollama_num_ctx(int(saved_num_ctx))
    # M1c-U2: Ollama 동시성 — DB 설정 우선, 없으면 env 기반 초기값 유지
    saved_concurrency = db.get_setting("ollama_concurrency")
    if saved_concurrency:
        try:
            llm_parser.set_ollama_concurrency(int(saved_concurrency))
        except (TypeError, ValueError):
            import logging
            logging.getLogger("whatudoin").warning(
                "ollama_concurrency DB 설정 파싱 실패: %r (env/기본값 유지)", saved_concurrency,
            )
    # ── M3-1 startup maintenance 단일 owner 표 (§11) ──────────────────────────
    # 작업                           | owner
    # finalize_expired_done          | scheduler (cron + startup) 단독
    #   ※ 현재 lifespan(위) + APScheduler(아래 03:05) 동거 — M3-2에서 lifespan 호출 이관
    # cleanup_old_trash              | scheduler (cron) 단독
    # check_upcoming_event_alarms    | scheduler (interval) 단독
    # run_backup_startup_safetynet   | web_api_lifespan 단독 (시작 직전 안전판)
    # run_backup_nightly             | scheduler (cron 03:00) 단독
    # cleanup_old_backups            | scheduler (cron 03:10) 단독
    # cleanup_orphan_images          | scheduler (cron 03:30) 단독
    #
    # 본 표는 M3-2 APScheduler 분리 시 두 service가 같은 job을 동시에
    # 실행하지 않도록 사전에 박은 정책이다. 단일 owner 위반은 회귀로 본다.
    # 권위 있는 원본: maintenance_owners.MAINTENANCE_JOB_OWNERS
    # ──────────────────────────────────────────────────────────────────────────
    if _scheduler_service_enabled:
        # Scheduler service 프로세스가 APScheduler를 단독 소유.
        # Web API lifespan은 scheduler 시작/job 등록/finalize 즉시호출 모두 skip.
        pass
    else:
        # 단일 프로세스 fallback — 기존 동작 그대로 (M3 service 미사용 환경).
        db.finalize_expired_done()  # 서버 시작 시 만료된 done 일정 즉시 처리
        if not scheduler.running:
            # APScheduler: 1분마다 15분 후 일정 알람 체크
            scheduler.add_job(db.check_upcoming_event_alarms, "interval", minutes=1)
            # APScheduler: 매일 새벽 3시 DB 백업
            scheduler.add_job(
                lambda: backup.run_backup(db.DB_PATH, _RUN_DIR),
                "cron", hour=3, minute=0,
                id="daily-db-backup", replace_existing=True,
            )
            # APScheduler: 매일 새벽 3시 5분 done 7일 경과 일정 자동 완료 처리
            scheduler.add_job(db.finalize_expired_done, "cron", hour=3, minute=5)
            # APScheduler: 매일 새벽 3시 10분 오래된 백업 파일 정리 (90일 보관)
            scheduler.add_job(
                lambda: backup.cleanup_old_backups(_RUN_DIR),
                "cron", hour=3, minute=10,
                id="daily-backup-cleanup", replace_existing=True,
            )
            # APScheduler: 매일 새벽 3시 20분 휴지통 90일 초과 항목 정리
            scheduler.add_job(db.cleanup_old_trash, "cron", hour=3, minute=20)
            # APScheduler: 매일 새벽 3시 30분 고아 이미지 파일 정리 (05:00 이후 중단, 다음날 이어서)
            scheduler.add_job(
                lambda: backup.cleanup_orphan_images(_RUN_DIR, db),
                "cron", hour=3, minute=30,
                id="daily-orphan-image-cleanup", replace_existing=True,
            )
            scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="WhatUDoin", lifespan=lifespan)

limiter = Limiter(key_func=auth.get_client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# M1c-U1/U4: Ollama 외부 호출 거부/장애를 사용자에게 503 + 통합 메시지로 변환.
# 슬롯 포화(busy)와 외부 장애(timeout/connect/5xx)는 사용자 화면에 동일한 "AI 사용 중/불가"
# 흐름으로 통합되며, 내부 로그에서만 reason을 구분한다(U4에서 timeout/connect/5xx 재mapping).
@app.exception_handler(llm_parser.OllamaUnavailableError)
async def _ollama_unavailable_handler(request: Request, exc: llm_parser.OllamaUnavailableError):
    import logging
    logger = logging.getLogger("whatudoin.ollama")
    if exc.reason == "busy" and exc.slots is not None:
        in_use, cap = exc.slots
        logger.warning("ollama busy: in_use=%d capacity=%d path=%s", in_use, cap, request.url.path)
    else:
        logger.warning("ollama unavailable: reason=%s path=%s", exc.reason, request.url.path)
    snap = llm_parser.get_ollama_concurrency_snapshot()
    return JSONResponse(
        status_code=503,
        content={
            "detail": exc.message,
            "reason": exc.reason,
            "slots": {"in_use": snap[0], "capacity": snap[1]},
        },
    )


def _upload_url_from_static_path(path: str) -> str:
    return "/uploads/meetings/" + path.replace("\\", "/").lstrip("/")


def _can_read_uploaded_file(url: str, user) -> bool:
    refs = db.find_upload_references(url)
    if not refs:
        return True
    for ref in refs:
        if ref.get("deleted"):
            if _can_read_deleted_upload_ref(ref, user):
                return True
            continue
        if ref["type"] == "document":
            doc = db.get_meeting(ref["id"])
            if doc and _can_read_doc(user, doc):
                return True
        elif ref["type"] == "checklist":
            checklist = db.get_checklist(ref["id"])
            if checklist and _can_read_checklist(user, checklist):
                return True
    return False


def _can_read_deleted_upload_ref(ref: dict, user) -> bool:
    if not user:
        return False
    item_type = "meeting" if ref["type"] == "document" else ref["type"]
    if item_type not in ("meeting", "checklist"):
        return False
    hidden_project = db.get_trash_item_hidden_project(item_type, ref["id"])
    if hidden_project:
        if user.get("role") == "admin":
            return True
        if hidden_project.get("deleted_at"):
            return hidden_project.get("owner_id") == user.get("id")
        return db.is_hidden_project_visible(hidden_project["id"], user)
    if user.get("role") == "admin":
        return True
    item_team = db.get_trash_item_team(item_type, ref["id"])
    return item_team is not None and item_team == user.get("team_id")


class _ProtectedMeetingStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        request = Request(scope)
        user = auth.get_current_user(request)
        if not _can_read_uploaded_file(_upload_url_from_static_path(path), user):
            return Response(status_code=404)
        return await super().get_response(path, scope)


def _upload_url_from_team_static_path(path: str) -> str:
    """팀 기능 #24: `/uploads/teams/{team_id}/...` URL 복원."""
    return "/uploads/teams/" + path.replace("\\", "/").lstrip("/")


class _ProtectedTeamStaticFiles(StaticFiles):
    """팀 기능 그룹 D #24: 체크·일정 등 신규 첨부 다운로드 권한 가드.

    raw StaticFiles 공개가 아니라 ``_can_read_uploaded_file`` 로 DB 본문 참조 권한 검증.
    ``find_upload_references`` 가 URL LIKE 매칭이므로 ``/uploads/teams/...`` URL 도 자동 매칭.
    """
    async def get_response(self, path: str, scope):
        request = Request(scope)
        user = auth.get_current_user(request)
        if not _can_read_uploaded_file(_upload_url_from_team_static_path(path), user):
            return Response(status_code=404)
        return await super().get_response(path, scope)


app.mount("/static",          StaticFiles(directory=str(_BASE_DIR / "static")),   name="static")
app.mount("/uploads/meetings", _ProtectedMeetingStaticFiles(directory=str(MEETINGS_DIR)), name="meetings_files")
app.mount("/uploads/teams",   _ProtectedTeamStaticFiles(directory=str(TEAMS_UPLOAD_DIR)), name="teams_files")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

def _asset_mtime(abs_path: str) -> str:
    try:
        return format(int(Path(abs_path).stat().st_mtime), 'x')
    except OSError:
        return '0'

def asset_v(rel_path: str) -> str:
    abs_path = str(_BASE_DIR / rel_path.lstrip('/'))
    return f'?v={_asset_mtime(abs_path)}'

templates.env.globals["asset_v"] = asset_v

mount_mcp(app)


class _MCPBearerAuthMiddleware:
    """순수 ASGI 미들웨어. BaseHTTPMiddleware는 SSE 스트리밍을 버퍼링해 깨진다."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode()
            user = verify_bearer_token(auth)
            if user is None:
                body = b'{"error":"unauthorized"}'
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
            token = _mcp_user.set(user)
            try:
                await self.app(scope, receive, send)
            finally:
                _mcp_user.reset(token)
        else:
            await self.app(scope, receive, send)

app.add_middleware(_MCPBearerAuthMiddleware)


class _StaticCacheMiddleware:
    """/static/ 응답에 Cache-Control 헤더를 추가한다.
    ?v= 파라미터 있으면 1년 immutable, 없으면 1시간.
    기존 cache-control 헤더가 있으면 replace한다 (중복 방지).
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        has_version = b"v=" in scope.get("query_string", b"")
        cache_value = b"public, max-age=31536000, immutable" if has_version else b"public, max-age=3600"

        async def send_with_cache(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", [])
                    if k.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", cache_value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache)


app.add_middleware(_StaticCacheMiddleware)


_SECURITY_HEADERS_BASE = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
]


def _content_security_policy(frame_src: str = "") -> bytes:
    parts = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "font-src 'self' data:",
    ]
    if frame_src:
        parts.append(f"frame-src 'self' {frame_src}")
    parts.append("frame-ancestors 'none'")
    return "; ".join(parts).encode("utf-8")


_SECURITY_HEADERS = _SECURITY_HEADERS_BASE + [
    (b"content-security-policy", _content_security_policy()),
]


def _avr_frame_origin() -> str:
    try:
        avr_url_enc = db.get_setting("avr_url_enc")
        if not avr_url_enc:
            return ""
        parsed = urlparse(crypto.decrypt(avr_url_enc).strip())
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if ";" in origin or any(ch.isspace() for ch in origin):
        return ""
    return origin


def _security_headers_for_path(path: str):
    if path == "/avr":
        avr_origin = _avr_frame_origin()
        if avr_origin:
            return _SECURITY_HEADERS_BASE + [
                (b"content-security-policy", _content_security_policy(avr_origin)),
            ]
    return _SECURITY_HEADERS


class _SecurityHeadersMiddleware:
    """모든 응답에 보안 헤더를 추가한다. SSE 스트리밍 보존을 위해 순수 ASGI 방식 사용."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security(message):
            if message["type"] == "http.response.start":
                existing_names = {k.lower() for k, _ in message.get("headers", [])}
                security_headers = _security_headers_for_path(scope.get("path", ""))
                extra = [(k, v) for k, v in security_headers if k not in existing_names]
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_with_security)


app.add_middleware(_SecurityHeadersMiddleware)


WEB_API_INTERNAL_ONLY_ENV = "WHATUDOIN_WEB_API_INTERNAL_ONLY"


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


class _FrontRouterAccessGuardMiddleware:
    """Future Web API internal mode: only local Front Router may call directly."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and _env_truthy(WEB_API_INTERNAL_ONLY_ENV):
            client = scope.get("client") or ("", 0)
            peer = str(client[0]) if client else ""
            if not _is_loopback_host(peer):
                body = json.dumps(
                    {"detail": "Web API internal listener requires Front Router."},
                    ensure_ascii=False,
                ).encode("utf-8")
                headers = [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                    *_security_headers_for_path(scope.get("path", "")),
                ]
                await send({"type": "http.response.start", "status": 403, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


# HTTP 8000 fallback unsafe write 차단(M2-4 권장안)은 사용자 의도와 충돌하여
# 미적용 — 사내 인트라넷 망 전제(§2) + HTTP/HTTPS 기능 동등 + HTTPS는 알람용
# 이라는 운영 모델 회복(plan §13 (대안) "HTTP write 유지" 정책 채택).
# 회선 신뢰는 §2 사내 LAN 운영 전제 + Front Router strip-then-set(M2-11) +
# TRUSTED_PROXY 외부 직접 접근 차단(M2-13)으로 보호한다.


def _extract_host(raw: bytes) -> str:
    """Host 헤더에서 호스트명·IP만 반환 (포트 제거). IPv6 리터럴([::1]) 처리 포함."""
    host = raw.decode(errors="replace").strip()
    if host.startswith("["):
        end = host.find("]")
        return host[:end + 1] if end != -1 else host
    return host.split(":")[0]


def _url_host(host: str) -> str:
    host = (host or "localhost").strip()
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _public_base_from_host(host: str, scheme: str = "https") -> str:
    port = 8443 if scheme == "https" else 8000
    return f"{scheme}://{_url_host(host)}:{port}"


def _configured_public_host() -> str:
    configured = (os.environ.get("WHATUDOIN_PUBLIC_BASE_URL") or "").strip()
    if not configured:
        return ""
    parsed = urlparse(configured if "://" in configured else f"https://{configured}")
    return parsed.hostname or ""


def _public_base_url(request: Request, scheme: str = "https") -> str:
    """External origin for user-visible links.

    M2 Front Router/Supervisor should set WHATUDOIN_PUBLIC_BASE_URL to the
    current PC LAN IP origin. During the existing single-process mode we fall
    back to the request host to preserve the old IP-based behavior.
    """
    host = _configured_public_host() or request.url.hostname or "localhost"
    return _public_base_from_host(host, scheme)


class _BrowserHTTPSRedirectMiddleware:
    """브라우저 GET 요청 시 JS probe로 인증서 신뢰 여부 감지 후 HTTPS/HTTP 분기.
    MCP·API·SSE·AJAX 제외. wd-cert-skip=1 쿠키 있으면 HTTP 그대로 통과."""

    _SKIP_PREFIXES = ("/mcp", "/api", "/static", "/uploads")
    _SKIP_EXACT = ("/favicon.ico", "/avr", "/remote", "/healthz")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("scheme") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        method = scope.get("method", "GET")

        if self._should_skip_path(path) or method not in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}

        # wd-cert-skip=1 쿠키가 있으면 HTTP 그대로 통과
        if self._has_cert_skip_cookie(headers):
            await self.app(scope, receive, send)
            return

        if not _https_available():
            await self.app(scope, receive, send)
            return

        # HEAD는 통과 (probe HTML 본문 불필요)
        if method != "GET":
            await self.app(scope, receive, send)
            return

        if not self._is_browser_navigate(headers):
            await self.app(scope, receive, send)
            return

        host = _extract_host(headers.get(b"host", b""))
        if not host:
            await self.app(scope, receive, send)
            return

        qs_raw = scope.get("query_string", b"").decode()
        html = self._probe_html(host, path, qs_raw)
        body = html.encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _should_skip_path(path: str) -> bool:
        return path in _BrowserHTTPSRedirectMiddleware._SKIP_EXACT or path.startswith(
            _BrowserHTTPSRedirectMiddleware._SKIP_PREFIXES
        )

    @staticmethod
    def _has_cert_skip_cookie(headers: dict) -> bool:
        raw = headers.get(b"cookie", b"").decode(errors="replace")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k.strip() == "wd-cert-skip" and v.strip() == "1":
                return True
        return False

    @staticmethod
    def _probe_html(host: str, path: str, qs: str) -> str:
        def _js(s: str) -> str:
            return json.dumps(s).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        https_base = _public_base_from_host(host, "https")
        http_base = _public_base_from_host(host, "http")
        js_https_base = _js(https_base)
        js_http_base = _js(http_base)
        js_path = _js(path)
        js_qs   = _js(("?" + qs) if qs else "")
        return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><title>알람용 인증서 확인 중...</title>
<style>
  body {{background:#fff;color:#111;display:flex;align-items:center;
        justify-content:center;height:100vh;margin:0;font-family:sans-serif;}}
  p {{font-size:1.1rem;}}
</style></head>
<body><p>알람용 인증서 확인 중...</p>
<script>
(function(){{
  var httpsBase={js_https_base}, httpBase={js_http_base}, path={js_path}, qs={js_qs};
  fetch(httpsBase + '/api/health',{{mode:'no-cors'}})
    .then(function(){{
      location.replace(httpsBase + path + qs);
    }})
    .catch(function(){{
      document.cookie='wd-cert-skip=1; Max-Age=3600; Path=/';
      location.replace(httpBase + path + qs);
    }});
}})();
</script>
</body></html>"""

    @staticmethod
    def _is_browser_navigate(headers: dict) -> bool:
        """현대 브라우저(Sec-Fetch-*) 또는 구형 브라우저(Mozilla/ + text/html) 감지."""
        mode = headers.get(b"sec-fetch-mode", b"")
        dest = headers.get(b"sec-fetch-dest", b"")
        if mode or dest:
            return mode == b"navigate" or dest == b"document"
        ua = headers.get(b"user-agent", b"").lower()
        accept = headers.get(b"accept", b"").lower()
        return b"mozilla/" in ua and b"text/html" in accept


app.add_middleware(_BrowserHTTPSRedirectMiddleware)
app.add_middleware(_FrontRouterAccessGuardMiddleware)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.ico")


@app.get("/api/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/healthz", include_in_schema=False)
def healthz():
    snap = _publisher_mod.get_failure_snapshot()
    return {
        "status": "ok",
        "service": "web-api",
        "sse_publish_failures": snap["count"],
        "sse_publish_last_event": snap["last_event"],
        "sse_publish_last_at": snap["last_at"],
    }


# ── 헬퍼 ────────────────────────────────────────────────

_HTTPS_CERT_PATH = _RUN_DIR / "whatudoin-cert.pem"
_HTTPS_KEY_PATH  = _RUN_DIR / "whatudoin-key.pem"


def _https_available() -> bool:
    return _HTTPS_CERT_PATH.is_file() and _HTTPS_KEY_PATH.is_file()


def _ctx(request: Request, **kwargs):
    user = auth.get_current_user(request)
    # 팀 기능 그룹 B #15: 현재 작업 팀 — 프로필 라벨 + JS payload 용
    work_team_id = None
    work_team_name = None
    if user is not None and not auth.is_unassigned(user):
        work_team_id = auth.resolve_work_team(request, user, None)
        if work_team_id is not None:
            t = db.get_team_active(work_team_id)
            work_team_name = t["name"] if t else None
    # P3-1 catchup: 글로벌 nav 에서 /admin/members, /admin/menus 노출 여부 게이팅.
    # 라우트 자체는 시스템 admin + 팀 admin 모두 허용 → 동일 기준으로 nav 표시.
    manageable_admin_teams_count = 0
    if user is not None:
        try:
            manageable_admin_teams_count = len(db.get_admin_teams_for(user) or [])
        except Exception:
            manageable_admin_teams_count = 0
    return {
        "request": request,
        "user": user,
        "is_unassigned": auth.is_unassigned(user),  # 팀 기능 그룹 B #12 — 알림 벨 게이팅 등에 사용
        "work_team_id": work_team_id,            # 팀 기능 그룹 B #15
        "work_team_name": work_team_name,        # 팀 기능 그룹 B #15
        "manageable_admin_teams_count": manageable_admin_teams_count,  # P3-1 catchup
        "https_available": _https_available(),
        "https_port": 8443,
        "http_port": 8000,
        "public_https_base": _public_base_url(request, "https"),
        "public_http_base": _public_base_url(request, "http"),
        # 그룹 D catchup (비로그인 진입 재설계):
        #   portal_team — `/팀이름` 또는 `/팀이름/메뉴` 라우트가 자기 호출 시 dict 로 set.
        #                 그 외(/, /admin 등)에서는 None — base.html nav 분기에 사용.
        #   portal_menu — 같은 팀의 get_team_menu_visibility() 결과 dict.
        # 이 두 키는 호출부(`_ctx(... portal_team=team, portal_menu=menu)`)에서만 set 한다.
        "portal_team": kwargs.pop("portal_team", None),
        "portal_menu": kwargs.pop("portal_menu", None),
        **kwargs,
    }


def _check_csrf(request: Request):
    """unsafe method일 때 Origin/Referer의 netloc이 Host와 정확히 일치해야 함."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        from urllib.parse import urlparse
        src = request.headers.get("Origin") or request.headers.get("Referer") or ""
        host = request.headers.get("Host") or ""
        if src and host and urlparse(src).netloc != host:
            raise HTTPException(status_code=403, detail="CSRF 검증 실패")


def _require_editor(request: Request):
    _check_csrf(request)
    user = auth.get_current_user(request)
    if not auth.is_editor(user):
        raise HTTPException(status_code=403, detail="로그인이 필요합니다.")
    return user


def _require_admin(request: Request):
    _check_csrf(request)
    user = auth.get_current_user(request)
    if not auth.is_admin(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


def _can_write_doc(user, doc: dict) -> bool:
    """문서 편집·삭제 권한 — auth.can_edit_meeting에 위임 (팀 기능 그룹 A #10)."""
    return auth.can_edit_meeting(user, doc)


def _notice_work_team(request: Request, user, explicit_id=None):
    """팀 기능 그룹 B #15-3: 공지 라우트가 사용할 현재 작업 팀 team_id (int | None).

    - admin: explicit_id 를 그대로 신뢰(호출부가 require_work_team_access 로 검증) →
             없으면 work_team_id 쿠키 → first_active_team_id (auth.resolve_work_team).
    - 비admin: 비소속 explicit_id 는 버리고(다른 팀 공지 임의 조회 차단) →
              쿠키 → 대표 팀 (auth.resolve_work_team).
    user 가 None 이거나 미배정이면 None.
    """
    if user is None or auth.is_unassigned(user):
        return None
    if not auth.is_admin(user):
        if explicit_id is not None and not auth.user_can_access_team(user, _safe_int(explicit_id)):
            explicit_id = None
    return auth.resolve_work_team(request, user, explicit_id=explicit_id)


# ── 페이지 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # 비로그인 사용자:        팀 목록 + 로그인/계정 가입 안내 (팀 기능 #11)
    # 팀 미배정 로그인 사용자: 팀 목록 + 팀 신청 버튼 + "내 자료" 영역 (팀 기능 #12)
    # 팀 배정·admin 사용자:    내부 업무 대시보드
    teams = db.get_visible_teams()
    user = auth.get_current_user(request)
    extra = {}
    if auth.is_unassigned(user):
        extra["team_status_map"] = db.get_my_team_statuses(user["id"])
        extra["my_docs"] = db.get_my_personal_meetings(user["id"])
    resp = templates.TemplateResponse(request, "home.html", _ctx(request, teams=teams, **extra))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
    user = auth.get_current_user(request)
    if user is None:
        return RedirectResponse("/", status_code=303)
    teams = db.get_all_teams()
    resp = templates.TemplateResponse(request, "calendar.html", _ctx(request, teams=teams))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", _ctx(request))


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = auth.get_current_user(request)
    if not auth.is_admin(user):
        return templates.TemplateResponse(request, "admin_login.html", _ctx(request))
    teams = db.get_all_teams()
    pending = db.get_pending_users()
    users = db.get_all_users()
    resp = templates.TemplateResponse(request, "admin.html", _ctx(
        request, teams=teams, pending=pending, members=users
    ))
    _ensure_work_team_cookie(request, resp, user)
    return resp


# 팀 기능 그룹 C #18: 멤버 관리 페이지 (시스템 admin + 팀 admin 접근).
@app.get("/admin/members", response_class=HTMLResponse)
def admin_members_page(request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    manageable = db.get_admin_teams_for(user)
    if not manageable:
        raise HTTPException(status_code=403, detail="멤버 관리 권한이 없습니다.")
    resp = templates.TemplateResponse(request, "members_admin.html", _ctx(
        request,
        manageable_teams=manageable,
        is_system_admin=auth.is_admin(user),
        current_user_id=user.get("id"),
    ))
    _ensure_work_team_cookie(request, resp, user)
    return resp


# 팀 기능 그룹 C #19: 메뉴 외부 노출 관리 페이지 (시스템 admin + 팀 admin 접근).
@app.get("/admin/menus", response_class=HTMLResponse)
def admin_menus_page(request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    manageable = db.get_admin_teams_for(user)
    if not manageable:
        raise HTTPException(status_code=403, detail="메뉴 관리 권한이 없습니다.")
    resp = templates.TemplateResponse(request, "menu_settings.html", _ctx(
        request,
        manageable_teams=manageable,
        is_system_admin=auth.is_admin(user),
    ))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/kanban", response_class=HTMLResponse)
def kanban_page(request: Request):
    teams = db.get_all_teams()
    resp = templates.TemplateResponse(request, "kanban.html", _ctx(request, teams=teams))
    _ensure_work_team_cookie(request, resp, auth.get_current_user(request))
    return resp


@app.get("/gantt", response_class=HTMLResponse)
def project_page(request: Request):
    teams = db.get_all_teams()
    resp = templates.TemplateResponse(request, "project.html", _ctx(request, teams=teams))
    _ensure_work_team_cookie(request, resp, auth.get_current_user(request))
    return resp


@app.get("/project-manage", response_class=HTMLResponse)
def project_manage_page(request: Request):
    user = _require_editor(request)
    resp = templates.TemplateResponse(request, "project_manage.html", _ctx(request))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/doc", response_class=HTMLResponse)
def docs_page(request: Request):
    user = auth.get_current_user(request)
    docs = db.get_all_meetings(viewer=user, work_team_ids=(_work_scope(request, user, None) if user else None))
    teams = db.get_all_teams()
    resp = templates.TemplateResponse(request, "doc_list.html", _ctx(
        request, docs=docs, teams=teams,
        default_model=llm_parser.DEFAULT_MODEL,
    ))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/doc/new", response_class=HTMLResponse)
def doc_new_page(request: Request):
    user = auth.get_current_user(request)
    if not auth.is_editor(user):
        return RedirectResponse("/")
    resp = templates.TemplateResponse(request, "doc_editor.html", _ctx(request, doc=None, doc_events=[], can_edit=True))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/doc/{meeting_id}", response_class=HTMLResponse)
def doc_detail_page(request: Request, meeting_id: int):
    doc = db.get_meeting(meeting_id)
    current_user = auth.get_current_user(request)
    if not _can_read_doc(current_user, doc):
        raise HTTPException(status_code=404)
    events = _filter_visible_events(db.get_events_by_meeting(meeting_id), current_user)
    can_edit = _can_write_doc(current_user, doc)
    lock = db.get_meeting_lock(meeting_id)
    locked_by = None
    lock_type = None
    if lock:
        if current_user and lock["user_name"] == current_user["name"]:
            _t = request.query_params.get("_t", "")
            if _t and _t == lock.get("tab_token", ""):
                pass  # 이 탭이 잠금 소유자임을 증명 → 편집 모드 허용
            else:
                lock_type = "self_tab"
                locked_by = lock["user_name"]
        else:
            lock_type = "other_user"
            locked_by = lock["user_name"]
    done_projects = db.get_done_project_names()
    resp = templates.TemplateResponse(request, "doc_editor.html", _ctx(
        request, doc=doc, doc_events=events,
        locked_by=locked_by, lock_type=lock_type, can_edit=can_edit, done_projects=done_projects,
    ))
    _ensure_work_team_cookie(request, resp, current_user)
    return resp


@app.get("/doc/{meeting_id}/history", response_class=HTMLResponse)
def doc_history_page(request: Request, meeting_id: int):
    doc = db.get_meeting(meeting_id)
    current_user = auth.get_current_user(request)
    if current_user is None or not _can_read_doc(current_user, doc):
        raise HTTPException(status_code=404)
    histories = db.get_meeting_histories(meeting_id)
    resp = templates.TemplateResponse(request, "doc_history.html", _ctx(
        request, doc=doc, histories=histories
    ))
    _ensure_work_team_cookie(request, resp, current_user)
    return resp


@app.get("/ai-import", response_class=HTMLResponse)
def ai_import_page(request: Request):
    return templates.TemplateResponse(request, "ai_import.html", _ctx(request))


# ── 변경 이력 페이지 ──────────────────────────────────────
@app.get("/changelog", response_class=HTMLResponse)
def changelog_page(request: Request):
    import json as _json
    from pathlib import Path as _Path
    _cl_path = _Path(__file__).parent / "changelog" / "changelog.json"
    cl_data = _json.loads(_cl_path.read_text(encoding="utf-8")) if _cl_path.exists() else {"groups": []}
    return templates.TemplateResponse(request, "changelog.html", {**_ctx(request), "cl_groups": cl_data["groups"]})


# ── 알람 설정 (인증서 다운로드) 페이지 ─────────────────────
@app.get("/alarm-setup", response_class=HTMLResponse)
def alarm_setup_page(request: Request):
    cert_path = _RUN_DIR / "whatudoin-rootCA.pem"
    return templates.TemplateResponse(
        request, "alarm_setup.html",
        _ctx(request, cert_ready=cert_path.is_file()),
    )


# ── MCP 설정 페이지 ──────────────────────────────────────
@app.get("/settings/mcp", response_class=HTMLResponse)
def settings_mcp_page(request: Request):
    _require_editor(request)
    scheme = "http" if request.url.scheme == "http" else "https"
    base = _public_base_url(request, scheme)
    mcp_base = base
    cline_config = json.dumps({
        "mcpServers": {
            "whatudoin": {
                "url": f"{mcp_base}/mcp/",
                "headers": {"Authorization": "Bearer <YOUR_TOKEN>"},
                "disabled": False,
            }
        }
    }, indent=2, ensure_ascii=False)
    codex_config = (
        '[mcp_servers.WhatUdoin]\n'
        'command = "npx"\n'
        'args = [\n'
        '  "-y",\n'
        '  "mcp-remote",\n'
        f'  "{mcp_base}/mcp/",\n'
        '  "--transport",\n'
        '  "sse-only",\n'
        '  "--header",\n'
        '  "Authorization: Bearer <YOUR_TOKEN>"\n'
        ']'
    )
    claude_desktop_config = json.dumps({
        "mcpServers": {
            "WhatUdoin": {
                "command": "mcp-remote",
                "args": [
                    f"{mcp_base}/mcp/",
                    "--transport", "sse-only",
                    "--header",
                    "Authorization: Bearer <YOUR_TOKEN>",
                ],
            }
        }
    }, indent=2, ensure_ascii=False)
    claude_code_cmd = (
        f'claude mcp add --transport http WhatUdoin {mcp_base}/mcp/'
        ' --header "Authorization: Bearer <YOUR_TOKEN>"'
    )
    return templates.TemplateResponse(
        request, "settings_mcp.html",
        _ctx(request,
             base_url=base,
             cline_config=cline_config,
             codex_config=codex_config,
             claude_desktop_config=claude_desktop_config,
             claude_code_cmd=claude_code_cmd),
    )


@app.get("/api/cert/rootCA.zip")
def download_rootca():
    cert_path = _RUN_DIR / "whatudoin-rootCA.pem"
    if not cert_path.is_file():
        raise HTTPException(status_code=404, detail="루트 인증서가 아직 준비되지 않았습니다.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(cert_path, arcname="WhatUdoin-rootCA.crt")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="WhatUdoin-rootCA.zip"'},
    )


# ── 팀 공지 페이지 ─────────────────────────────────────────

@app.get("/notice", response_class=HTMLResponse)
def notice_page(request: Request):
    user = auth.get_current_user(request)
    tid = _notice_work_team(request, user, None)  # 팀 기능 #15-3: 현재 작업 팀
    notice = db.get_notice_latest_for_team(tid) if tid is not None else None
    resp = templates.TemplateResponse(request, "notice.html", _ctx(request, notice=notice))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/notice/history", response_class=HTMLResponse)
def notice_history_page(request: Request):
    user = auth.get_current_user(request)
    tid = _notice_work_team(request, user, None)  # 팀 기능 #15-3: 현재 작업 팀
    histories = (db.get_notice_history(tid, include_null=auth.is_admin(user))
                 if tid is not None else [])
    resp = templates.TemplateResponse(request, "notice_history.html", _ctx(request, histories=histories))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/check", response_class=HTMLResponse)
def check_page(request: Request):
    user = auth.get_current_user(request)
    all_projs = db.get_all_projects_meta(viewer=user, work_team_ids=(_work_scope(request, user, None) if user else None))
    visible = [p for p in all_projs if user or not p.get("is_private", 0)]
    active_projs = [p for p in visible if p.get("is_active", 1)]
    done_projs   = [p for p in visible if not p.get("is_active", 1)]
    resp = templates.TemplateResponse(request, "check.html",
        _ctx(request, projects=active_projs, done_projects=done_projs))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/check/new/edit", response_class=HTMLResponse)
def check_new_page(request: Request, proj: str = ""):
    user = auth.get_current_user(request)
    if not user or user.get("role") not in ("editor", "admin"):
        return RedirectResponse("/check")
    all_projs = db.get_all_projects_with_events(viewer=user, work_team_ids=_work_scope(request, user, None))
    projects = [p for p in all_projs if p.get("is_active", 1) and p.get("name") != "미지정"]
    proj = "" if proj == "미지정" else proj
    return templates.TemplateResponse(
        request, "check_editor.html",
        _ctx(request, checklist={"id": None, "title": "", "project": proj, "content": ""},
             locked_by=None, projects=projects, is_new=True)
    )


@app.get("/check/{checklist_id}/edit", response_class=HTMLResponse)
def check_editor_page(request: Request, checklist_id: int):
    user = auth.get_current_user(request)
    if not user or user.get("role") not in ("editor", "admin"):
        return RedirectResponse("/check")
    item = db.get_checklist(checklist_id)
    if not item:
        return RedirectResponse("/check")
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="Access denied.")
    all_projs = db.get_all_projects_with_events(viewer=user, work_team_ids=_work_scope(request, user, None))
    projects = [p for p in all_projs if p.get("is_active", 1) and p.get("name") != "미지정"]
    lock = db.get_checklist_lock(checklist_id)
    locked_by = None
    lock_type = None
    if lock:
        if user and lock["user_name"] == user["name"]:
            _t = request.query_params.get("_t", "")
            if _t and _t == lock.get("tab_token", ""):
                pass  # 이 탭이 잠금 소유자임을 증명 → 편집 모드 허용
            else:
                lock_type = "self_tab"
                locked_by = lock["user_name"]
        else:
            lock_type = "other_user"
            locked_by = lock["user_name"]
    proj_name = item.get("project")
    proj_info = next((p for p in all_projs if p.get("name") == proj_name), None) if proj_name else None
    proj_is_private = bool(proj_info.get("is_private", 0)) if proj_info else False
    done_projects = db.get_done_project_names()
    return templates.TemplateResponse(
        request, "check_editor.html",
        _ctx(request, checklist=item, locked_by=locked_by, lock_type=lock_type, projects=projects, proj_is_private=proj_is_private, done_projects=done_projects)
    )


@app.get("/check/{checklist_id}/history", response_class=HTMLResponse)
def check_history_page(request: Request, checklist_id: int):
    user = auth.get_current_user(request)
    if not user or user.get("role") not in ("editor", "admin"):
        return RedirectResponse("/check")
    item = db.get_checklist(checklist_id)
    if not item:
        return RedirectResponse("/check")
    if not _can_read_checklist(user, item):
        raise HTTPException(status_code=404)
    histories = db.get_checklist_histories(checklist_id)
    return templates.TemplateResponse(
        request, "check_history.html",
        _ctx(request, checklist=item, histories=histories)
    )


# ── 체크리스트 API ────────────────────────────────────────────

@app.get("/api/checklists")
def list_checklists(request: Request, project: str = None, active: int = None, include_done: int = 0, team_id: int = None):
    viewer = auth.get_current_user(request)
    active_only = None if active is None else bool(active)
    # 팀 기능 그룹 A #10: 체크는 현재 작업 팀 기준 (resolve_work_team fallback).
    work_team_ids = _work_scope(request, viewer, team_id) if viewer else None
    return db.get_checklists(project=project, viewer=viewer, active_only=active_only,
                             include_done_projects=bool(include_done), work_team_ids=work_team_ids)


@app.post("/api/checklists")
async def create_checklist(request: Request):
    user = _require_editor(request)
    data = await request.json()
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    project = data.get("project", "").strip()
    _assert_can_assign_to_project(user, project or None)
    content = data.get("content", "").strip()
    is_public = 1 if data.get("is_public") else 0
    raw_att = data.get("attachments")
    if isinstance(raw_att, str):
        try:
            attachments = json.loads(raw_att)
        except Exception:
            attachments = None
    elif isinstance(raw_att, list):
        attachments = raw_att
    else:
        attachments = None
    # 팀 기능 그룹 C #16: 신규 체크리스트의 team_id 는 현재 작업 팀을 명시 보장.
    team_id = auth.require_admin_work_team(request, user)
    cid = db.create_checklist(project, title, content, user["name"], is_public=is_public, team_id=team_id, attachments=attachments)
    _sse_publish("checks.changed", {"id": cid, "action": "create"})
    return {"id": cid}


@app.get("/api/checklists/{checklist_id}")
def get_checklist(checklist_id: int, request: Request):
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    user = auth.get_current_user(request)
    if not _can_read_checklist(user, item):
        raise HTTPException(status_code=404)
    return item


@app.patch("/api/checklists/{checklist_id}/status")
async def toggle_checklist_status(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.set_checklist_active(checklist_id, is_active)
    _sse_publish("checks.changed", {"id": checklist_id, "action": "update"})
    return {"ok": True, "is_active": is_active}


@app.patch("/api/checklists/bulk-visibility")
async def bulk_checklist_visibility(request: Request):
    user = _require_editor(request)
    data = await request.json()
    project  = data.get("project")   # None 또는 "" → 미지정, 문자열 → 해당 프로젝트
    _assert_can_assign_to_project(user, project or None)
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    is_active_raw = data.get("is_active")
    is_active = None if is_active_raw is None else (1 if is_active_raw else 0)
    if is_public and project:
        _proj = db.get_project_by_name(project)
        if _proj and _proj.get("is_hidden"):
            raise HTTPException(status_code=403, detail="히든 프로젝트 항목은 외부 공개할 수 없습니다.")
    team_id_filter = user.get("team_id") if not project else None
    count = db.bulk_update_checklist_visibility(project, is_public, is_active, team_id=team_id_filter)
    return {"ok": True, "updated": count}


@app.patch("/api/events/bulk-visibility")
async def bulk_event_visibility(request: Request):
    user = _require_editor(request)
    data = await request.json()
    project  = data.get("project")   # None 또는 "" → 미지정, 문자열 → 해당 프로젝트
    _assert_can_assign_to_project(user, project or None)
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    is_active_raw = data.get("is_active")
    is_active = None if is_active_raw is None else (1 if is_active_raw else 0)
    if is_public and project:
        _proj = db.get_project_by_name(project)
        if _proj and _proj.get("is_hidden"):
            raise HTTPException(status_code=403, detail="히든 프로젝트 항목은 외부 공개할 수 없습니다.")
    team_id_filter = user.get("team_id") if not project else None
    count = db.bulk_update_event_visibility(project, is_public, is_active, team_id=team_id_filter)
    _sse_publish("events.changed", {"id": None, "action": "bulk_update", "team_id": team_id_filter})
    return {"ok": True, "updated": count}


@app.patch("/api/checklists/{checklist_id}/visibility")
async def rotate_checklist_visibility(checklist_id: int, request: Request):
    user = _require_editor(request)
    cl = db.get_checklist(checklist_id)
    if not cl:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, cl):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    body = await request.body()
    data = await request.json() if body else {}
    if "is_public" in data:
        raw = data["is_public"]
        new_pub = None if raw is None else (1 if raw else 0)
    else:
        # legacy toggle (check.html 2-state vis-seg 호환): None→1, 1→0, 0→None
        cur = cl.get("is_public")
        new_pub = 1 if cur is None else (0 if cur == 1 else None)
    # 히든 프로젝트 항목은 외부 공개 불가
    if new_pub:
        cl_proj = cl.get("project")
        if cl_proj:
            _proj = db.get_project_by_name(cl_proj)
            if _proj and _proj.get("is_hidden"):
                raise HTTPException(status_code=403, detail="히든 프로젝트 항목은 외부 공개할 수 없습니다.")
    db.update_checklist_visibility(checklist_id, new_pub)
    return {"ok": True, "is_public": new_pub}


@app.patch("/api/checklists/{checklist_id}")
async def update_checklist(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    title = data.get("title", item["title"]).strip()
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    old_proj = (item.get("project") or "").strip()
    project  = data.get("project", item["project"])
    project  = "" if project is None else str(project).strip()
    # 히든→일반(또는 무소속) 이동 시 confirm 요구 (project 필드가 실제로 변경되는 경우만)
    if "project" in data and old_proj and project != old_proj:
        _old_proj = db.get_project_by_name(old_proj)
        if _old_proj and _old_proj.get("is_hidden"):
            _new_is_hidden = False
            if project:
                _new_proj = db.get_project_by_name(project)
                _new_is_hidden = bool(_new_proj and _new_proj.get("is_hidden"))
            if not _new_is_hidden and not data.get("confirm"):
                return JSONResponse(
                    status_code=400,
                    content={"requires_confirm": True, "message": "히든 프로젝트 밖으로 이동합니다. 계속하시겠습니까?"},
                )
    # 새 프로젝트가 히든이면 멤버십 검사
    if "project" in data and project != old_proj:
        _assert_can_assign_to_project(user, project or None)
    raw_att = data.get("attachments")
    if isinstance(raw_att, str):
        try:
            attachments = json.loads(raw_att)
        except Exception:
            attachments = None
    elif isinstance(raw_att, list):
        attachments = raw_att
    else:
        attachments = None
    db.update_checklist(checklist_id, title, project, attachments=attachments)
    # 프로젝트에서 미지정으로 이동 → 항상 외부 비공개
    if old_proj and not project:
        db.update_checklist_visibility(checklist_id, 0)
    # 히든 프로젝트로 이동 시 is_public 강제 0
    elif project and project != old_proj:
        _proj = db.get_project_by_name(project)
        if _proj and _proj.get("is_hidden"):
            db.update_checklist_visibility(checklist_id, 0)
    _sse_publish("checks.changed", {"id": checklist_id, "action": "update"})
    return {"ok": True}


@app.patch("/api/checklists/{checklist_id}/content")
async def update_checklist_content(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    source = data.get("source", "editor")  # "editor" | "viewer_toggle"
    if source == "viewer_toggle":
        if item.get("is_locked"):
            raise HTTPException(status_code=423, detail="체크 잠금 상태입니다.")
        edit_lock = db.get_checklist_lock(checklist_id)
        if edit_lock:
            raise HTTPException(status_code=423, detail="편집 잠금 상태입니다.")
    content = data.get("content", "")
    save_history = data.get("save_history", True)
    db.update_checklist_content(checklist_id, content, user["name"], save_history=save_history)
    _sse_publish("checks.changed", {"id": checklist_id, "action": "content"})
    return {"ok": True}


@app.get("/api/checklists/{checklist_id}/histories")
def get_checklist_histories(checklist_id: int, request: Request):
    user = auth.get_current_user(request)
    checklist = db.get_checklist(checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="체크리스트를 찾을 수 없습니다.")
    if not _can_read_checklist(user, checklist):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    return db.get_checklist_histories(checklist_id)


@app.post("/api/checklists/{checklist_id}/histories/{history_id}/restore")
async def restore_checklist_history(checklist_id: int, history_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404, detail="체크리스트를 찾을 수 없습니다.")
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    ok = db.restore_checklist_from_history(checklist_id, history_id, user["name"])
    if not ok:
        raise HTTPException(status_code=404, detail="이력을 찾을 수 없습니다.")
    _sse_publish("checks.changed", {"id": checklist_id, "action": "content"})
    return db.get_checklist(checklist_id)


@app.delete("/api/checklists/{checklist_id}")
def delete_checklist(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    db.release_checklist_lock(checklist_id)  # 삭제 시 강제 해제
    db.delete_checklist(checklist_id, deleted_by=user["name"], team_id=user.get("team_id"))
    _sse_publish("checks.changed", {"id": checklist_id, "action": "delete"})
    return {"ok": True}


# ── 체크리스트 잠금 API ───────────────────────────────────────

@app.post("/api/checklists/{checklist_id}/lock")
def lock_checklist(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    tab_token = request.query_params.get("tab_token", "")
    ok = db.acquire_checklist_lock(checklist_id, user["name"], tab_token)
    if not ok:
        lock = db.get_checklist_lock(checklist_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    _sse_publish("checks.changed", {"id": checklist_id, "action": "lock"})
    return {"ok": True}


@app.put("/api/checklists/{checklist_id}/lock")
def heartbeat_checklist_lock(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="Access denied.")
    tab_token = request.query_params.get("tab_token", "")
    ok = db.heartbeat_checklist_lock(checklist_id, tab_token)
    if not ok:
        lock = db.get_checklist_lock(checklist_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    return {"ok": True}


@app.delete("/api/checklists/{checklist_id}/lock")
def unlock_checklist(checklist_id: int, request: Request):
    user = auth.get_current_user(request)
    if user:
        item = db.get_checklist(checklist_id)
        if not item:
            raise HTTPException(status_code=404)
        if not auth.can_edit_checklist(user, item):
            raise HTTPException(status_code=403, detail="Access denied.")
        tab_token = request.query_params.get("tab_token") or None
        if tab_token:  # tab_token 없으면 no-op — 다른 편집자 잠금 보호
            db.release_checklist_lock(checklist_id, tab_token)
            _sse_publish("checks.changed", {"id": checklist_id, "action": "unlock"})
    return {"ok": True}


@app.get("/api/checklists/{checklist_id}/lock")
def get_checklist_lock_status(checklist_id: int, request: Request):
    user = auth.get_current_user(request)
    item = db.get_checklist(checklist_id)
    if not item or not _can_read_checklist(user, item):
        raise HTTPException(status_code=404)
    lock = db.get_checklist_lock(checklist_id)
    if not lock:
        return {"locked_by": None, "lock_type": None}
    lock_type = "self_tab" if (user and lock["user_name"] == user["name"]) else "other_user"
    return {"locked_by": lock["user_name"], "lock_type": lock_type}


@app.patch("/api/checklists/{checklist_id}/is-locked")
async def set_checklist_is_locked(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    if not auth.can_edit_checklist(user, item):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    locked = 1 if data.get("locked") else 0
    db.set_checklist_is_locked(checklist_id, locked)
    return {"ok": True, "is_locked": locked}


@app.get("/api/notice")
def api_get_notice(request: Request, team_id: int = None):
    # 팀 기능 #15-3: 현재 작업 팀의 최신 공지 1건. 비로그인/미배정 → {}.
    user = auth.get_current_user(request)
    tid = _notice_work_team(request, user, team_id)
    if tid is None:
        return {}
    return db.get_notice_latest_for_team(tid) or {}


@app.post("/api/notice")
async def api_save_notice(request: Request):
    user = _require_editor(request)
    data = await request.json()
    content = data.get("content", "")
    # 팀 기능 #15-3 + C #16: work_team_id 기준 저장 — 작업 팀 명시 보장.
    team_id = auth.require_admin_work_team(request, user, explicit_id=data.get("team_id"))
    notice_id = db.save_notice(content, team_id, user["name"])
    return {"id": notice_id}


@app.post("/api/notice/notify")
async def api_notify_notice(request: Request):
    user = _require_editor(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    # 팀 기능 #15-3 + C #16: 현재 작업 팀 명시 보장 — admin이 임의 팀에 알림 발송 차단.
    team_id = auth.require_admin_work_team(request, user, explicit_id=(data or {}).get("team_id"))
    notice = db.get_notice_latest_for_team(team_id)
    if not notice:
        return {"ok": False, "reason": "no_notice"}
    content = notice["content"]
    preview = content.replace("#", "").strip()[:40]
    msg = f"📢 팀 공지 업데이트: {preview}…" if len(content.replace("#", "").strip()) > 40 else f"📢 팀 공지 업데이트: {preview}"
    db.create_notification_for_team(team_id, "notice", msg, exclude_user=user["name"])
    return {"ok": True}


# ── 알림 API ─────────────────────────────────────────────

@app.get("/api/notifications/count")
def get_notification_count(request: Request):
    user = auth.get_current_user(request)
    if not user or auth.is_unassigned(user):  # 팀 기능 #12: 미배정 사용자에겐 알림 비노출
        return {"count": 0}
    return {"count": db.get_notification_count(user["name"], viewer=user)}


@app.get("/api/notifications/pending")
def get_pending_notifications(request: Request):
    user = auth.get_current_user(request)
    if not user or auth.is_unassigned(user):  # 팀 기능 #12: 미배정 사용자에겐 알림 비노출
        return []
    return db.get_pending_notifications(user["name"], viewer=user)


@app.post("/api/notifications/read-all")
def mark_all_notifications_read(request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    db.mark_all_notifications_read(user["name"])
    return {"ok": True}


# ── 인증 API ────────────────────────────────────────────

@app.post("/api/login")
@limiter.limit("10/minute")
async def login(request: Request, response: Response):
    """일반 로그인 (#7): 이름 + 비밀번호. admin 제외, name_norm 매칭, hash 검증.

    응답 메시지는 모든 실패 케이스에서 동일 (admin 존재 노출 금지).
    """
    data = await request.json()
    name = data.get("name", "").strip()
    password = data.get("password", "").strip()
    if not name or not password:
        raise HTTPException(status_code=400, detail="이름과 비밀번호를 입력하세요.")
    if not passwords.is_valid_user_name(name):
        # 정규식 위반(공백·특수문자) — 별도 메시지: 입력 형식 자체가 잘못됨.
        raise HTTPException(status_code=400, detail="이름은 영문·숫자·한글만 사용할 수 있습니다.")
    user = db.get_user_by_login(name, password)
    if not user:
        # admin 시도 / 없는 사용자 / 잘못된 비밀번호 모두 동일 메시지.
        # get_user_by_login가 DUMMY_HASH로 timing 균등화까지 처리.
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    session_id = db.create_session(user["id"], role=user["role"])
    db.record_ip(user["id"], auth.get_client_ip(request))
    response.set_cookie(
        auth.SESSION_COOKIE, session_id,
        httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=86400 * 30,
    )
    return {"name": user["name"], "role": user["role"], "team_id": user["team_id"]}


@app.post("/api/me/change-password")
async def change_my_password(request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    data = await request.json()
    current_pw = data.get("current_password", "").strip()
    new_pw = data.get("new_password", "").strip()
    if not current_pw or not new_pw:
        raise HTTPException(status_code=400, detail="모두 입력하세요.")
    if not db.verify_user_password(user["id"], current_pw):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다.")
    if not passwords.is_valid_password_policy(new_pw):
        raise HTTPException(status_code=400, detail="새 비밀번호는 영문과 숫자를 모두 포함해야 합니다.")
    db.reset_user_password(user["id"], new_pw)
    return {"ok": True}


# ── IP 자동 로그인 (본인 — 설정 화면) ─────────────────────────────
_IP_WHITELIST_CONFLICT_MSG = (
    "이 PC는 다른 사용자의 자동 로그인 대상으로 등록되어 있습니다. 시스템 관리자에게 문의하세요."
)


def _require_login(request: Request):
    """로그인 필요 (CSRF 검사 없음 — GET 등 안전 메서드용)."""
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


@app.get("/api/me/ip-whitelist")
def get_my_ip_whitelist(request: Request):
    """현재 접속 PC IP의 본인 whitelist 상태. 설정 패널 초기 토글용.

    admin은 자동 로그인 대상이 아니므로 항상 enabled=false + admin 플래그.
    """
    user = _require_login(request)
    ip = auth.get_client_ip(request)
    if auth.is_admin(user):
        return {"enabled": False, "conflict": False, "conflict_user": None, "ip": ip, "admin": True}
    status = db.get_whitelist_status_for_ip(user["id"], ip)
    status["admin"] = False
    return status


@app.post("/api/me/ip-whitelist")
def add_my_ip_whitelist(request: Request):
    """현재 접속 PC IP를 본인 whitelist로 등록. admin은 403."""
    user = _require_editor(request)
    if auth.is_admin(user):
        raise HTTPException(status_code=403, detail="관리자 계정은 IP 자동 로그인 대상이 아닙니다.")
    ip = auth.get_client_ip(request)
    try:
        db.set_user_whitelist_ip(user["id"], ip)
    except db.IPWhitelistConflict:
        raise HTTPException(status_code=409, detail=_IP_WHITELIST_CONFLICT_MSG)
    return {"ok": True, "ip": ip}


@app.delete("/api/me/ip-whitelist")
def remove_my_ip_whitelist(request: Request):
    """현재 접속 PC IP의 본인 whitelist 해제 (history 강등). 확인 모달 없음."""
    user = _require_editor(request)
    ip = auth.get_client_ip(request)
    db.remove_user_whitelist_ip(user["id"], ip)
    return {"ok": True, "ip": ip}


# ── 현재 작업 팀 (work_team_id 쿠키) — 팀 기능 그룹 B #15 ──────────────

def _set_work_team_cookie(response: Response, team_id: int) -> None:
    response.set_cookie(
        auth.WORK_TEAM_COOKIE, str(team_id),
        max_age=86400 * 365, samesite="lax", httponly=False, path="/",
    )


def _ensure_work_team_cookie(request: Request, response: Response, user) -> None:
    """SSR 페이지 응답에 work_team_id 쿠키가 현재 작업 팀과 일치하도록 보정.

    - 비로그인 / 팀 미배정: 작업 팀 개념 없음 → 아무것도 안 함 (기존 쿠키도 건드리지 않음).
    - 그 외: resolve_work_team(검증 포함) 결과와 현재 쿠키가 다르면(없거나 무효라 fallback 됐거나
      오래된 값) Set-Cookie 로 갱신. resolve 결과가 None(admin인데 비삭제 팀 0개 등)이면 안 함.
    """
    if user is None or auth.is_unassigned(user):
        return
    intended = auth.resolve_work_team(request, user, None)
    if intended is None:
        return
    cookie_raw = request.cookies.get(auth.WORK_TEAM_COOKIE)
    if cookie_raw != str(intended):
        _set_work_team_cookie(response, intended)


@app.get("/api/me/work-team")
def get_my_work_team(request: Request):
    """현재 작업 팀 + 선택 가능한 팀 목록. 프로필 "팀 변경" 드롭다운용.

    - 비admin: 본인 approved + 비삭제 소속 팀 (joined_at 순).
    - admin:   전체 비삭제 팀 (이름 순).
    """
    user = _require_login(request)
    current = auth.resolve_work_team(request, user, None)
    if auth.is_admin(user):
        teams = [{"id": t["id"], "name": t["name"]} for t in db.get_visible_teams()]
    else:
        teams = db.user_work_teams(user["id"])
    return {"current": current, "teams": teams, "is_admin": auth.is_admin(user)}


@app.post("/api/me/work-team")
async def set_my_work_team(request: Request, response: Response):
    """현재 작업 팀 변경 — 검증 후 work_team_id 쿠키 갱신.

    - 비admin: 그 팀의 approved 멤버여야 함 (require_work_team_access). 비삭제 팀이어야 함.
    - admin:   비삭제 팀이면 허용.
    잘못된 팀이면 4xx.
    """
    _check_csrf(request)
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    data = await request.json()
    raw = data.get("team_id")
    try:
        tid = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="team_id가 올바르지 않습니다.")
    team = db.get_team_active(tid)
    if not team:
        raise HTTPException(status_code=404, detail="존재하지 않거나 삭제 예정인 팀입니다.")
    auth.require_work_team_access(user, tid)  # admin은 통과, 비admin은 소속 검증 (403)
    _set_work_team_cookie(response, tid)
    return {"ok": True, "team_id": tid, "team_name": team["name"]}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    session_id = request.cookies.get(auth.SESSION_COOKIE)
    if session_id:
        db.delete_session(session_id)
    response.delete_cookie(
        auth.SESSION_COOKIE,
        httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"),
    )
    return {"ok": True}


# 팀 기능 그룹 A #8: 예약 사용자명 (대소문자 무관) — 가입 차단.
RESERVED_USERNAMES = {"admin", "system", "root", "guest", "anonymous"}


@app.post("/api/register")
@limiter.limit("5/minute")
async def register(request: Request, response: Response):
    """계정 가입 (#8): 이름·비밀번호만으로 즉시 활성 사용자 생성 + 자동 로그인.

    팀 신청은 별도(`POST /api/me/team-applications`). pending_users 는 더 이상 쓰지 않는다.
    """
    data = await request.json()
    name = data.get("name", "").strip()
    password = data.get("password", "").strip()
    if not name or not password:
        raise HTTPException(status_code=400, detail="이름과 비밀번호를 입력하세요.")
    if "password_confirm" in data and data.get("password_confirm", "").strip() != password:
        raise HTTPException(status_code=400, detail="비밀번호와 비밀번호 확인이 일치하지 않습니다.")
    if not passwords.is_valid_user_name(name):
        raise HTTPException(status_code=400, detail="이름은 영문·숫자·한글만 사용할 수 있습니다.")
    if name.casefold() in RESERVED_USERNAMES:
        raise HTTPException(status_code=400, detail="사용할 수 없는 이름입니다.")
    if not passwords.is_valid_password_policy(password):
        raise HTTPException(status_code=400, detail="비밀번호는 영문과 숫자를 모두 포함해야 합니다.")
    user = db.create_user_account(name, password)
    if not user:
        raise HTTPException(status_code=409, detail="이미 사용 중인 이름입니다.")
    session_id = db.create_session(user["id"], role=user["role"])
    db.record_ip(user["id"], auth.get_client_ip(request))
    response.set_cookie(
        auth.SESSION_COOKIE, session_id,
        httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=86400 * 30,
    )
    return {"ok": True, "name": user["name"], "role": user["role"], "team_id": user["team_id"]}


# ── 관리자 인증 API ──────────────────────────────────────

@app.post("/api/admin/login")
@limiter.limit("5/minute")
async def admin_login(request: Request, response: Response):
    data = await request.json()
    name = data.get("name", "").strip()
    password = data.get("password", "").strip()
    user = db.get_user_by_credentials(name, password)
    if not user:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    session_id = db.create_session(user["id"], role=user["role"])
    db.record_ip(user["id"], auth.get_client_ip(request))
    response.set_cookie(
        auth.SESSION_COOKIE, session_id,
        httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=300,
    )
    return {"ok": True}


# ── 관리자 API ──────────────────────────────────────────

@app.get("/api/admin/pending")
def admin_pending(request: Request):
    _require_admin(request)
    return db.get_pending_users()


@app.post("/api/admin/pending/{pending_id}/approve")
async def admin_approve(pending_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    team_id = data.get("team_id")
    if not team_id:
        raise HTTPException(status_code=400, detail="팀을 지정하세요.")
    user_id = db.approve_pending_user(pending_id, int(team_id))
    if not user_id:
        raise HTTPException(status_code=404)
    return {"ok": True, "user_id": user_id}


@app.post("/api/admin/pending/{pending_id}/reject")
def admin_reject(pending_id: int, request: Request):
    _require_admin(request)
    db.reject_pending_user(pending_id)
    return {"ok": True}


# ── 팀 신청 API (팀 기능 그룹 A #8) ──────────────────────

@app.post("/api/me/team-applications")
@limiter.limit("10/minute")
async def apply_team(request: Request):
    """본인이 특정 팀에 가입 신청. user_teams 에 pending row 생성/갱신."""
    _check_csrf(request)
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if auth.is_admin(user):
        raise HTTPException(status_code=400, detail="관리자는 팀 신청 대상이 아닙니다.")
    data = await request.json()
    team_id = data.get("team_id")
    if team_id is None:
        raise HTTPException(status_code=400, detail="팀을 선택하세요.")
    try:
        team_id = int(team_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="팀을 선택하세요.")
    if not db.get_team_active(team_id):
        raise HTTPException(status_code=404, detail="존재하지 않는 팀입니다.")
    result, detail = db.apply_to_team(user["id"], team_id)
    if result == "blocked":
        # 그룹 D #23: team_deleted/team_not_found 는 DB 헬퍼의 방어선이며 정상적으로는
        # 라우트의 get_team_active 1차 가드(line 1783)에서 404 로 차단되어 이 분기에
        # 닿지 않는다. 직접 헬퍼 호출 또는 race 조건 대비를 위해 매핑은 명시한다.
        msg = {
            "pending_here": "이미 가입 신청 중입니다.",
            "pending_other": "다른 팀 신청이 처리 대기 중입니다.",
            "already_member": "이미 해당 팀의 멤버입니다.",
            "team_deleted": "이 팀은 삭제 예정 상태로 신청할 수 없습니다.",
            "team_not_found": "존재하지 않는 팀입니다.",
        }.get(detail, "신청할 수 없습니다.")
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "status": "pending"}


def _require_team_admin(request: Request, team_id: int):
    """admin 또는 해당 팀 관리자만 통과. 실패 시 HTTPException."""
    _check_csrf(request)
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if not auth.is_team_admin(user, team_id):
        raise HTTPException(status_code=403, detail="해당 팀에 대한 관리 권한이 없습니다.")
    return user


@app.get("/api/teams/{team_id}/applications")
def list_team_apps(team_id: int, request: Request):
    """해당 팀의 pending 신청 목록 (admin·팀 관리자)."""
    _require_team_admin(request, team_id)
    return db.list_team_applications(team_id)


@app.post("/api/teams/{team_id}/applications/{user_id}/decide")
async def decide_team_app(team_id: int, user_id: int, request: Request):
    """팀 신청 수락/거절 (admin·팀 관리자). body: {decision: 'approve'|'reject'}."""
    _require_team_admin(request, team_id)
    data = await request.json()
    raw = str(data.get("decision") or "").strip().lower()
    decision = {"approve": "approved", "approved": "approved",
                "reject": "rejected", "rejected": "rejected"}.get(raw)
    if not decision:
        raise HTTPException(status_code=400, detail="decision 은 approve 또는 reject 여야 합니다.")
    ok = db.decide_team_application(user_id, team_id, decision)
    if not ok:
        raise HTTPException(status_code=404, detail="처리할 신청이 없습니다.")
    return {"ok": True}


# ── 팀 기능 그룹 C #18: 멤버 관리 페이지용 라우트 (admin + 팀 admin) ──

@app.get("/api/team-manage/{team_id}/members")
def team_manage_members(team_id: int, request: Request):
    """팀의 멤버십 전체(approved/pending/rejected) 반환. admin + 팀 admin 접근."""
    _require_team_admin(request, team_id)
    return db.list_team_memberships(team_id)


@app.put("/api/team-manage/{team_id}/members/{user_id}/role")
async def team_manage_set_role(team_id: int, user_id: int, request: Request):
    """팀 멤버 role 토글 (admin + 팀 admin).

    자기 자신 강등 차단: 호출자 본인 user_id 와 일치하면 400.
    마지막 admin 보호: db.set_team_member_role 이 ValueError("last_admin_protected") raise.
    """
    caller = _require_team_admin(request, team_id)
    if caller and caller.get("id") == user_id:
        raise HTTPException(status_code=400, detail="자기 자신의 권한은 변경할 수 없습니다.")
    data = await request.json()
    role = (data.get("role") or "").strip()
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role 은 'admin' 또는 'member' 여야 합니다.")
    try:
        db.set_team_member_role(team_id, user_id, role)
    except ValueError as exc:
        code = str(exc)
        if code == "not_member":
            raise HTTPException(status_code=404, detail="해당 사용자는 이 팀의 승인 멤버가 아닙니다.")
        if code == "last_admin_protected":
            raise HTTPException(status_code=400, detail="이 팀의 마지막 팀 관리자입니다. 먼저 다른 멤버를 팀 관리자로 지정하세요.")
        raise HTTPException(status_code=400, detail="role 값이 올바르지 않습니다.")
    return {"ok": True}


@app.post("/api/team-manage/{team_id}/members/{user_id}/evict")
async def team_manage_evict(team_id: int, user_id: int, request: Request):
    """팀 멤버 추방 — user_teams.status='rejected' 로 변경, row 보존.

    자기 자신 추방 차단. 마지막 admin 보호 (deleted_at IS NULL 일 때).
    """
    caller = _require_team_admin(request, team_id)
    if caller and caller.get("id") == user_id:
        raise HTTPException(status_code=400, detail="자기 자신을 추방할 수 없습니다.")
    try:
        db.evict_team_member(team_id, user_id)
    except ValueError as exc:
        code = str(exc)
        if code == "not_member":
            raise HTTPException(status_code=404, detail="해당 사용자는 이 팀의 멤버가 아닙니다.")
        if code == "last_admin_protected":
            raise HTTPException(status_code=400, detail="이 팀의 마지막 팀 관리자입니다. 먼저 다른 멤버를 팀 관리자로 지정하세요.")
        raise HTTPException(status_code=400, detail="추방 처리 중 오류가 발생했습니다.")
    return {"ok": True}


# ── 팀 기능 그룹 C #19: 메뉴 외부 노출 토글 API (admin + 팀 admin) ──

@app.get("/api/team-menu/{team_id}")
def team_menu_get(team_id: int, request: Request):
    """팀별 메뉴 노출 현황. defaults 합성 dict."""
    _require_team_admin(request, team_id)
    return db.get_team_menu_visibility(team_id)


@app.put("/api/team-menu/{team_id}/{menu_key}")
async def team_menu_set(team_id: int, menu_key: str, request: Request):
    """단일 메뉴 토글. body: {enabled: bool}."""
    _require_team_admin(request, team_id)
    data = await request.json()
    if "enabled" not in data:
        raise HTTPException(status_code=400, detail="enabled 필드가 필요합니다.")
    enabled = bool(data.get("enabled"))
    try:
        db.set_team_menu_visibility(team_id, menu_key, enabled)
    except ValueError as exc:
        if str(exc) == "invalid_menu_key":
            raise HTTPException(status_code=400, detail="허용된 menu_key 가 아닙니다.")
        raise HTTPException(status_code=400, detail="요청이 올바르지 않습니다.")
    return {"ok": True}


@app.get("/api/admin/users")
def admin_users(request: Request):
    _require_admin(request)
    return db.get_all_users()


@app.put("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    target_user = db.get_user(user_id)
    # 관리자 계정 비활성화 보호
    if not data.get("is_active"):
        if target_user and target_user.get("role") == "admin":
            raise HTTPException(status_code=400, detail="관리자 계정은 비활성화할 수 없습니다.")
    # 팀 제외 여부 판단: team_id가 없어지거나 is_active=0이 되는 경우
    old_team_id = target_user.get("team_id") if target_user else None
    new_team_id = data.get("team_id")
    new_is_active = data.get("is_active", 1)
    is_removing = old_team_id is not None and (
        new_team_id != old_team_id or not new_is_active
    )
    if is_removing:
        force = data.get("force", False)
        hidden_owned = db.get_user_owned_hidden_projects(user_id)
        if hidden_owned and not force:
            return {
                "warning": True,
                "hidden_projects": [r["name"] for r in hidden_owned],
                "message": "해당 사용자는 히든 프로젝트 관리자입니다. 계속 진행하면 관리 권한이 이양됩니다.",
            }
        if force and hidden_owned:
            db.transfer_hidden_projects_on_removal(user_id, hidden_owned)
    db.update_user(user_id, data)
    return {"ok": True}


@app.put("/api/admin/users/{user_id}/name")
async def admin_rename_user(user_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    new_name = data.get("name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="이름을 입력하세요.")
    db.update_user_name(user_id, new_name)
    return {"ok": True}


@app.get("/api/admin/pending/count")
def admin_pending_count(request: Request):
    # 로그인된 모든 사용자가 접근 가능 (뱃지 표시용)
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return {"count": len(db.get_pending_users())}


@app.get("/api/admin/settings/llm")
def admin_get_llm_settings(request: Request):
    _require_admin(request)
    return {
        "ollama_url": db.get_setting("ollama_url") or llm_parser.OLLAMA_BASE_URL,
        "ollama_timeout": int(db.get_setting("ollama_timeout") or 300),
        "ollama_num_ctx": int(db.get_setting("ollama_num_ctx") or 4096),
        "ollama_concurrency": int(db.get_setting("ollama_concurrency") or llm_parser.get_ollama_concurrency_snapshot()[1]),
    }


@app.put("/api/admin/settings/llm")
async def admin_set_llm_settings(request: Request):
    _require_admin(request)
    data = await request.json()
    url = data.get("ollama_url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL을 입력하세요.")
    db.set_setting("ollama_url", url)
    llm_parser.set_ollama_base_url(url)

    timeout_raw = data.get("ollama_timeout")
    if timeout_raw is not None:
        t = max(30, int(timeout_raw))
        db.set_setting("ollama_timeout", str(t))
        llm_parser.set_ollama_timeout(t)

    num_ctx_raw = data.get("ollama_num_ctx")
    if num_ctx_raw is not None:
        n = max(512, int(num_ctx_raw))
        db.set_setting("ollama_num_ctx", str(n))
        llm_parser.set_ollama_num_ctx(n)

    concurrency_raw = data.get("ollama_concurrency")
    if concurrency_raw is not None:
        c = max(1, min(5, int(concurrency_raw)))
        db.set_setting("ollama_concurrency", str(c))
        llm_parser.set_ollama_concurrency(c)

    return {"ok": True}


@app.post("/api/admin/users/{user_id}/reset-password")
async def admin_reset_password(user_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    new_pw = data.get("password", "").strip()
    if not new_pw:
        raise HTTPException(status_code=400, detail="새 비밀번호를 입력하세요.")
    db.reset_user_password(user_id, new_pw)
    return {"ok": True}


@app.get("/api/teams/members")
def team_members(request: Request, team_id: int = None):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    all_users = db.get_all_users()
    members = [u for u in all_users if u.get("is_active", 1) and u.get("role") != "admin"]
    if team_id is not None:
        members = [u for u in members if u.get("team_id") == team_id]
    return [{"name": u["name"], "team_id": u.get("team_id"), "team_name": u.get("team_name")} for u in members]


# 팀 기능 그룹 C #17 — 팀 생성/관리 라우트 정비.
# RESERVED_TEAM_NAMES: 하드코딩 예약어 목록 (계획서 §4).
# 실제 등록된 route first-segment 와 함께 검사한다 (`_team_name_collides_with_route`).
RESERVED_TEAM_NAMES = {
    "api", "admin", "doc", "check", "kanban", "gantt", "calendar",
    "mcp", "mcp-codex", "uploads", "static", "settings", "changelog",
    "register", "project-manage", "ai-import", "alarm-setup", "notice",
    "trash", "remote", "avr", "favicon.ico",
    "docs", "redoc", "openapi.json",
}


def _registered_route_first_segments() -> set:
    """app.routes 의 path 들에서 first segment 를 NFC casefold 로 모은다.

    런타임 평가 — import 시점에 routes 가 다 등록되지 않았을 수 있어 매 호출 시 산출.
    동적으로 추가된 라우트나 마운트도 자동 반영된다.
    """
    segs = set()
    for r in app.routes:
        raw_path = getattr(r, "path", None) or ""
        if not raw_path.startswith("/"):
            continue
        # 첫 슬래시 이후 첫 세그먼트만. path parameter ({x}) 는 스킵.
        rest = raw_path[1:]
        if not rest:
            continue
        first = rest.split("/", 1)[0]
        if first.startswith("{"):
            continue
        if not first:
            continue
        segs.add(db.normalize_name(first))
    return segs


def _team_name_collides_with_route(name: str) -> bool:
    """팀 이름이 예약 경로 또는 실제 등록된 route 와 충돌하는지 검사."""
    norm = db.normalize_name(name)
    if norm in {db.normalize_name(r) for r in RESERVED_TEAM_NAMES}:
        return True
    return norm in _registered_route_first_segments()


@app.get("/api/admin/teams")
def admin_teams(request: Request):
    _require_admin(request)
    return db.get_all_teams()


@app.post("/api/admin/teams")
async def admin_create_team(request: Request):
    _require_admin(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="팀 이름을 입력하세요.")
    # 1) 정규식: 영문/숫자/언더바만.
    if not re.match(r"^[A-Za-z0-9_]+$", name):
        raise HTTPException(
            status_code=400,
            detail="팀 이름은 영문/숫자/언더바만 사용할 수 있습니다.",
        )
    # 2) 예약 경로 + 실제 등록 route 충돌 검사.
    if _team_name_collides_with_route(name):
        raise HTTPException(
            status_code=400,
            detail="이 이름은 예약된 경로입니다. 다른 이름을 사용하세요.",
        )
    # 3) DB 헬퍼가 정적 검증·name_norm 중복을 다시 검사.
    try:
        team_id = db.create_team(name)
    except ValueError as exc:
        code = str(exc)
        if code == "duplicate_name":
            raise HTTPException(
                status_code=400,
                detail="같은 이름의 팀이 이미 존재합니다 (대소문자 무관).",
            )
        # invalid_name 등.
        raise HTTPException(status_code=400, detail="팀 이름이 유효하지 않습니다.")
    return {"id": team_id, "name": name}


# 팀 기능 그룹 C #17: PUT /api/admin/teams/{team_id} 제거.
# 팀 이름은 생성 후 변경 불가 (계획서 §4). 프론트의 팀 이름 수정 UI 도 함께 제거됐다.


@app.delete("/api/admin/teams/{team_id}")
def admin_delete_team(team_id: int, request: Request):
    """팀 삭제 (그룹 D #23): soft delete + 90일 유예 + 자동 완전 삭제.

    실제 hard delete 는 ``scheduler_service.py`` 가 03:40 cron 으로 호출하는
    ``db.purge_expired_teams()`` 가 처리한다. 본 라우트는 ``teams.deleted_at`` 만 기록한다.
    """
    user = _require_admin(request)
    actor_id = user.get("id") if isinstance(user, dict) else None
    try:
        info = db.soft_delete_team(team_id, actor_id=actor_id)
    except ValueError as exc:
        code = str(exc)
        if code == "already_deleted":
            raise HTTPException(status_code=400, detail="이미 삭제 예정인 팀입니다.")
        if code == "not_found":
            raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다.")
        raise HTTPException(status_code=400, detail="팀 삭제에 실패했습니다.")
    return {"ok": True, **info}


@app.post("/api/admin/teams/{team_id}/restore")
def admin_restore_team(team_id: int, request: Request):
    """soft-deleted 팀 복원 (그룹 D #23). user_teams 보존되어 있어 멤버 자동 복구."""
    user = _require_admin(request)
    actor_id = user.get("id") if isinstance(user, dict) else None
    try:
        info = db.restore_team(team_id, actor_id=actor_id)
    except ValueError as exc:
        code = str(exc)
        if code == "not_deleted":
            raise HTTPException(status_code=400, detail="활성 팀입니다. 복원할 필요가 없습니다.")
        if code == "not_found":
            raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다. (이미 완전 삭제됐을 수 있습니다.)")
        raise HTTPException(status_code=400, detail="팀 복원에 실패했습니다.")
    return {"ok": True, **info}


@app.get("/api/admin/teams/deleted")
def admin_list_deleted_teams(request: Request):
    """삭제 예정 팀 목록 (그룹 D #23 admin UI 용)."""
    _require_admin(request)
    return db.list_deleted_teams()


# 팀 기능 그룹 C #17: 팀 멤버 관리 라우트 (admin 화면용).

@app.get("/api/admin/teams/{team_id}/members")
def admin_team_members(team_id: int, request: Request):
    _require_admin(request)
    return db.get_team_members(team_id)


@app.put("/api/admin/teams/{team_id}/members/{user_id}/role")
async def admin_set_team_member_role(team_id: int, user_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    role = (data.get("role") or "").strip()
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role 은 'admin' 또는 'member' 여야 합니다.")
    try:
        db.set_team_member_role(team_id, user_id, role)
    except ValueError as exc:
        code = str(exc)
        if code == "not_member":
            raise HTTPException(status_code=404, detail="해당 사용자는 이 팀의 승인 멤버가 아닙니다.")
        raise HTTPException(status_code=400, detail="role 값이 올바르지 않습니다.")
    return {"ok": True}


@app.get("/api/admin/users/{user_id}/ips")
def admin_user_ips(user_id: int, request: Request):
    _require_admin(request)
    return db.get_user_ips(user_id)


@app.put("/api/admin/ips/{ip_id}/whitelist")
async def admin_toggle_whitelist(ip_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    enable = data.get("enable", False)
    try:
        db.toggle_ip_whitelist(ip_id, enable)
    except db.IPWhitelistConflict:
        raise HTTPException(status_code=409, detail="이 IP는 이미 다른 사용자의 자동 로그인 대상으로 등록되어 있습니다.")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/ips")
async def admin_register_ip(user_id: int, request: Request):
    """admin — 임의 사용자에게 임의 IP를 whitelist로 직접 등록 (접속 이력 없는 IP도 가능)."""
    _require_admin(request)
    data = await request.json()
    ip = (data.get("ip_address") or "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="IP 주소를 입력하세요.")
    try:
        db.admin_set_whitelist_ip(user_id, ip)
    except db.IPWhitelistConflict:
        raise HTTPException(status_code=409, detail="이 IP는 이미 다른 사용자의 자동 로그인 대상으로 등록되어 있습니다.")
    return {"ok": True, "ip": ip}


@app.delete("/api/admin/ips/{ip_id}")
def admin_delete_ip(ip_id: int, request: Request):
    """admin — user_ips row 삭제."""
    _require_admin(request)
    db.delete_ip_row(ip_id)
    return {"ok": True}


# ── 프로젝트 자동 색상 (간트와 동일 팔레트·해시) ─────────────
_PROJECT_COLOR_PALETTE = [
    '#0984e3', '#00b894', '#00a8a8', '#6c5ce7', '#00875a',
    '#1565c0', '#00838f', '#7c4dff', '#9b59b6', '#5c35a0',
    '#546e7a', '#8d6e63', '#7b5e00', '#d48806', '#e67e22',
    '#c0392b', '#d81b60', '#ad1457', '#ef6c00', '#f9a825',
    '#558b2f', '#2e7d32', '#00695c', '#0277bd', '#283593',
    '#4527a0', '#6d4c41', '#455a64', '#b71c1c', '#880e4f',
]
_UNASSIGNED_PROJECT_NAME = '미지정'

def _project_color(name: str, used_colors=None) -> str:
    h = 0
    for ch in (name or ''):
        h = (h * 31 + ord(ch)) & 0xffff
    n = len(_PROJECT_COLOR_PALETTE)
    if not used_colors:
        return _PROJECT_COLOR_PALETTE[h % n]
    for i in range(n):
        color = _PROJECT_COLOR_PALETTE[(h + i) % n]
        if color not in used_colors:
            return color
    return _PROJECT_COLOR_PALETTE[h % n]


def resolve_project_color(proj_name: str, proj_colors: dict) -> str:
    """프로젝트 색상 최종 결정: DB지정 > 해시팔레트(겹침회피)."""
    normalized = (proj_name or '').strip() or _UNASSIGNED_PROJECT_NAME
    db_color = None if normalized == _UNASSIGNED_PROJECT_NAME else proj_colors.get(normalized)
    if db_color:
        return db_color
    return _project_color(normalized, set(proj_colors.values()))


# ── 이벤트 API ───────────────────────────────────────────

def _filter_events_by_visibility(events: list, user, scope_team_ids=None) -> list:
    """일정 가시성 필터 — 팀 기능 그룹 A #10.

    scope_team_ids 의미:
      - admin 사용자(user.role=='admin'): 인자 무관하게 전체 통과 (전 팀 슈퍼유저).
      - scope_team_ids=None & 비admin: 호출부가 작업 팀을 명시하지 않음 →
        사용자 소속 팀 전체(user_team_ids)로 fallback. (#15 쿠키 도입 전 안전망)
      - scope_team_ids=set(): 그 안의 team_id 만 통과. 비어 있으면 팀 자료는 통과 안 함.

    규칙(우선순위):
      1. 히든 프로젝트 차단 (기존 동작)
      2. team_id ∈ scope → 통과 (is_public 값 무관, 같은 팀이므로 비공개도 봄)
      3. team_id IS NULL → 작성자 본인(events.created_by == user.name)만 통과
      4. is_public == 1 → 통과 (공개 일정 — 비로그인 캘린더도 봄, 기존 동작)
      5. 그 외 skip
    """
    if user and user.get("role") == "admin":
        return events
    if scope_team_ids is None:
        scope_team_ids = auth.user_team_ids(user) if user else set()
    # events/checklists.created_by 는 신규 쓰기는 str(user.id), legacy 는 사용자 이름 — 둘 다 인정.
    author_ids = set()
    if user:
        author_ids.add(str(user.get("id")))
        if user.get("name"):
            author_ids.add(user.get("name"))
    blocked_hidden = db.get_blocked_hidden_project_names(user)
    result = []
    for e in events:
        proj = e.get("project") or ""
        if proj and proj in blocked_hidden:
            continue
        team = e.get("team_id")
        if team is not None and team in scope_team_ids:
            result.append(e)
            continue
        if team is None:
            if author_ids and e.get("created_by") in author_ids:
                result.append(e)
            continue
        if e.get("is_public") == 1:
            result.append(e)
    return result


def _work_scope(request: Request, user, explicit_id=None):
    """현재 작업 팀 기준 라우트의 가시성 scope 집합 — 팀 기능 그룹 A #10.

    admin: None 반환 (전 팀 슈퍼유저 — 필터가 무필터 처리).
    비admin: resolve_work_team(explicit → 쿠키 → 대표 팀 → legacy) 결과 1개 set.
             단 명시 team_id 가 사용자 소속이 아니면 무시하고 대표 팀으로 fallback
             (다른 팀 자료 임의 조회 차단 — "서버는 매 요청마다 소속 검증" 원칙).
             결정 불가(팀 미배정)이면 빈 set → 팀 자료 미노출 (작성자 본인 NULL row 만 보임).
    #15 에서 쿠키 발급/검증/UI 가 붙으면 자연스럽게 쿠키 기반으로 합쳐진다.
    """
    if auth.is_admin(user):
        return None
    # 명시 team_id 가 비소속이면 버린다 — resolve_work_team 은 explicit 을 무조건 신뢰하므로 여기서 차단.
    if explicit_id is not None and not auth.user_can_access_team(user, _safe_int(explicit_id)):
        explicit_id = None
    tid = auth.resolve_work_team(request, user, explicit_id=explicit_id)
    if tid is None:
        return set()
    # 쿠키 등 다른 경로로 들어온 team_id 도 소속 검증 (#15 쿠키 검증 합류 전 안전망)
    if not auth.user_can_access_team(user, tid):
        return set()
    return {tid}


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@app.get("/api/events")
def list_events(request: Request, team_id: int = None):
    user = auth.get_current_user(request)
    events = db.get_all_events()
    events = _filter_events_by_visibility(events, user, _work_scope(request, user, team_id))
    proj_colors = db.get_project_colors()
    # 바인딩된 체크리스트의 title 일괄 조회 (삭제된 체크는 None으로 폴백)
    bound_ids = {e.get("bound_checklist_id") for e in events if e.get("bound_checklist_id")}
    bound_titles = {}
    if bound_ids:
        with db.get_conn() as _conn:
            placeholders = ",".join("?" for _ in bound_ids)
            rows = _conn.execute(
                f"SELECT id, title FROM checklists WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                tuple(bound_ids)
            ).fetchall()
            bound_titles = {r["id"]: r["title"] for r in rows}
    result = []
    for e in events:
        proj_name = e.get("project")

        # 24시간 초과 이벤트는 allDay로 처리
        is_all_day = bool(e["all_day"])
        if not is_all_day and e.get("end_datetime") and e.get("start_datetime"):
            try:
                _start = datetime.fromisoformat(e["start_datetime"])
                _end   = datetime.fromisoformat(e["end_datetime"])
                if (_end - _start).total_seconds() >= 86400:
                    is_all_day = True
            except Exception:
                pass

        # FullCalendar의 all-day end는 exclusive → DB 실제 끝날짜 + 1일
        ev_end = e["end_datetime"] or e["start_datetime"]
        if is_all_day and ev_end:
            try:
                _end_dt = datetime.fromisoformat(ev_end) + timedelta(days=1)
                ev_end = _end_dt.strftime("%Y-%m-%dT00:00")
            except Exception:
                pass

        evt_type = e.get("event_type", "schedule")
        ev = {
            "id": e["id"],
            "title": e["title"],
            "start": e["start_datetime"],
            "end": ev_end,
            "allDay": is_all_day,
            "classNames": (["ev-meeting"] if evt_type == "meeting"
                           else ["ev-journal"] if evt_type == "journal"
                           else ["ev-subtask"] if evt_type == "subtask"
                           else ["ev-schedule"]),
            "extendedProps": {
                "project":               proj_name,
                "description":           e["description"],
                "location":              e["location"],
                "assignee":              e["assignee"],
                "all_day":               is_all_day,
                "source":                e["source"],
                "team_id":               e["team_id"],
                "meeting_id":            e["meeting_id"],
                "kanban_status":         e.get("kanban_status"),
                "priority":              e.get("priority", "normal"),
                "event_type":            evt_type,
                "recurrence_rule":       e.get("recurrence_rule"),
                "recurrence_parent_id":  e.get("recurrence_parent_id"),
                "parent_event_id":       e.get("parent_event_id"),
                "bound_checklist_id":    e.get("bound_checklist_id"),
                "bound_checklist_title": bound_titles.get(e.get("bound_checklist_id")) if e.get("bound_checklist_id") else None,
            },
        }
        color = resolve_project_color(proj_name, proj_colors)
        ev["backgroundColor"] = color
        ev["borderColor"]     = color
        result.append(ev)
    return result


@app.get("/api/events/by-project-range")
def events_by_project_range(request: Request, project: str, start: str, end: str, include_subtasks: int = 0, team_id: int = None):
    user = auth.get_current_user(request)
    events = db.get_events_by_project_range(project, start, end, include_subtasks=bool(include_subtasks))
    return _filter_events_by_visibility(events, user, _work_scope(request, user, team_id))


@app.get("/api/events/search-parent")
def search_parent_events(request: Request, project: str = "", q: str = "", exclude_id: str = None, team_id: int = None):
    """하위 업무 모달의 '상위 업무' 오토컴플릿 전용 검색"""
    user = auth.get_current_user(request)
    with db.get_conn() as conn:
        params = []
        where = ["e.event_type = 'schedule'", "e.recurrence_rule IS NULL", "e.deleted_at IS NULL", "e.parent_event_id IS NULL", "(e.kanban_status IS NULL OR e.kanban_status != 'done')"]
        if project:
            where.append("e.project = ?")
            params.append(project)
        if q:
            where.append("e.title LIKE ?")
            params.append(f"%{q}%")
        exclude_id_int = int(exclude_id) if exclude_id and exclude_id.isdigit() else None
        if exclude_id_int:
            where.append("e.id != ?")
            params.append(exclude_id_int)
        rows = conn.execute(
            f"SELECT id, title, project, start_datetime, end_datetime, team_id, is_public, created_by FROM events e WHERE {' AND '.join(where)} ORDER BY e.start_datetime LIMIT 30",
            params
        ).fetchall()
    events = [dict(r) for r in rows]
    events = _filter_events_by_visibility(events, user, _work_scope(request, user, team_id))
    # 클라이언트에 team_id/is_public/created_by 노출 불필요 — 제거
    for e in events:
        e.pop("team_id", None)
        e.pop("is_public", None)
        e.pop("created_by", None)
    return events


@app.get("/api/events/{event_id}/subtasks")
def get_event_subtasks(event_id: int, request: Request, team_id: int = None):
    """특정 이벤트의 하위 업무 목록"""
    user = auth.get_current_user(request)
    subtasks = db.get_subtasks(event_id)
    return _filter_events_by_visibility(subtasks, user, _work_scope(request, user, team_id))


@app.get("/api/events/{event_id}")
def get_event(event_id: int, request: Request, team_id: int = None):
    user = auth.get_current_user(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not _filter_events_by_visibility([event], user, _work_scope(request, user, team_id)):
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _validate_event_payload(payload: dict) -> list:
    """수동·AI 양쪽 경로에서 공통으로 쓰는 필수 필드 검증."""
    errors = []
    if not (payload.get("title") or "").strip():
        errors.append("제목을 입력해주세요.")
    if not (payload.get("assignee") or "").strip():
        errors.append("담당자를 입력해주세요.")
    if payload.get("event_type") not in (None, "schedule", "meeting", "journal", "subtask"):
        payload["event_type"] = "schedule"
    return errors


@app.post("/api/events")
async def create_event(request: Request):
    user = _require_editor(request)
    data = await request.json()
    data.setdefault("project", None)
    data.setdefault("description", "")
    data.setdefault("location", "")
    data.setdefault("assignee", None)
    data.setdefault("all_day", 0)
    data.setdefault("end_datetime", None)
    data.setdefault("source", "manual")
    data.setdefault("meeting_id", None)
    data.setdefault("kanban_status", None)
    data.setdefault("priority", "normal")
    data.setdefault("event_type", "schedule")
    data.setdefault("recurrence_rule", None)
    data.setdefault("recurrence_end", None)
    data.setdefault("parent_event_id", None)
    data.setdefault("bound_checklist_id", None)
    errors = _validate_event_payload(data)
    if errors:
        raise HTTPException(status_code=422, detail=errors[0])
    # 하위 업무 검증
    if data.get("event_type") == "subtask":
        pid = data.get("parent_event_id")
        if not pid:
            raise HTTPException(status_code=400, detail="하위 업무는 상위 업무를 지정해야 합니다.")
        parent = db.get_event(int(pid))
        if not parent:
            raise HTTPException(status_code=400, detail="상위 업무를 찾을 수 없습니다.")
        if parent.get("event_type") != "schedule":
            raise HTTPException(status_code=400, detail="하위 업무의 상위는 업무 유형이어야 합니다.")
        if parent.get("recurrence_rule"):
            raise HTTPException(status_code=400, detail="반복 일정에는 하위 업무를 추가할 수 없습니다.")
        # 프로젝트 미설정 시 부모에서 상속
        if not data.get("project"):
            data["project"] = parent.get("project")
    proj_name = (data.get("project") or "").strip()
    if proj_name and not auth.is_admin(user):
        _proj = db.get_project(proj_name)
        if _proj and _proj.get("is_hidden") and not db.is_hidden_project_visible(_proj["id"], user):
            raise HTTPException(status_code=403, detail="히든 프로젝트에 접근 권한이 없습니다.")
    _assert_assignees_in_hidden_project(proj_name or None, data.get("assignee"))
    data["created_by"] = str(user["id"])
    # 팀 기능 그룹 C #16: 신규 일정의 team_id 는 현재 작업 팀을 명시 보장.
    # admin이 작업 팀 미선택이거나 비admin 미배정이면 400 (NULL team_id 신규 row 차단).
    data["team_id"] = auth.require_admin_work_team(request, user)
    event_id = db.create_event(data)
    # 일지·하위 업무는 담당자 알림 없음
    if data.get("event_type") not in ("journal", "subtask"):
        assignees = [a.strip() for a in (data.get("assignee") or "").split(",") if a.strip()]
        for name in assignees:
            if name != user["name"]:
                db.create_notification(name, "assigned", f"📌 담당자로 지정됨: {data.get('title','')}", event_id)
    _sse_publish("events.changed", {"id": event_id, "action": "create", "team_id": user.get("team_id")})
    return {"id": event_id}


@app.put("/api/events/{event_id}")
async def update_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="다른 팀의 일정은 수정할 수 없습니다.")
    data = await request.json()
    edit_mode = data.pop("edit_mode", "this")
    data.setdefault("description", event.get("description", ""))
    data.setdefault("location", event.get("location", ""))
    data.setdefault("all_day", event.get("all_day", 0))
    data.setdefault("kanban_status", event.get("kanban_status"))
    data.setdefault("priority", event.get("priority", "normal"))
    data.setdefault("parent_event_id", event.get("parent_event_id"))
    data.setdefault("bound_checklist_id", event.get("bound_checklist_id"))
    # 하위 업무를 가진 업무의 유형 변경 차단
    existing_type = event.get("event_type", "schedule")
    new_type = data.get("event_type")
    if new_type and new_type != existing_type and db.has_subtasks(event_id):
        raise HTTPException(status_code=400, detail="하위 일정이 있는 업무의 유형은 변경할 수 없습니다.")
    errors = _validate_event_payload(data)
    if errors:
        raise HTTPException(status_code=422, detail=errors[0])
    # 하위 업무 저장 시 parent 검증
    if data.get("event_type") == "subtask":
        pid = data.get("parent_event_id")
        if not pid:
            raise HTTPException(status_code=400, detail="하위 업무는 상위 업무를 지정해야 합니다.")
        pid = int(pid)
        if pid == event_id:
            raise HTTPException(status_code=400, detail="자기 자신을 상위로 지정할 수 없습니다.")
        parent = db.get_event(pid)
        if not parent or parent.get("event_type") != "schedule":
            raise HTTPException(status_code=400, detail="상위 업무는 '업무' 유형이어야 합니다.")
        if parent.get("recurrence_rule"):
            raise HTTPException(status_code=400, detail="반복 일정에는 하위 업무를 추가할 수 없습니다.")
        if db.has_subtasks(event_id):
            raise HTTPException(status_code=400, detail="하위 업무를 가진 업무는 하위 업무가 될 수 없습니다.")

    # 새 project가 히든이면 멤버십 검사
    if "project" in data:
        _new_proj_name = (data.get("project") or "").strip() or None
        if _new_proj_name != ((event.get("project") or "").strip() or None):
            _assert_can_assign_to_project(user, _new_proj_name)
    # 히든 프로젝트 담당자 검증
    _effective_proj = (data.get("project") if "project" in data else event.get("project")) or None
    _assert_assignees_in_hidden_project((_effective_proj or "").strip() or None, data.get("assignee"))

    # 수정 전 담당자 목록
    prev_assignees = set(a.strip() for a in (event.get("assignee") or "").split(",") if a.strip())

    is_recurring = bool(event.get("recurrence_rule") or event.get("recurrence_parent_id"))
    if is_recurring:
        if edit_mode == "all":
            db.update_event_recurring_all(event_id, data)
        elif edit_mode == "from_here":
            db.update_event_recurring_from_here(event_id, data)
        else:
            db.update_event_recurring_this(event_id, data)
    else:
        db.update_event(event_id, data)
        if existing_type == "schedule":
            going_inactive = data.get("is_active") == 0 and event.get("is_active") != 0
            going_done = data.get("kanban_status") == "done" and event.get("kanban_status") != "done"
            if going_inactive or going_done:
                db.complete_subtasks(event_id)

    # 새로 추가된 담당자에게만 알림 (등록자 본인 제외, 일지 제외)
    if data.get("event_type") != "journal":
        new_assignees = set(a.strip() for a in (data.get("assignee") or "").split(",") if a.strip())
        for name in new_assignees - prev_assignees:
            if name != user["name"]:
                db.create_notification(name, "assigned", f"📌 담당자로 지정됨: {data.get('title', event.get('title',''))}", event_id)

    # 반복 3분기(this/from_here/all)·단일 모두 공통 return이므로 여기서 1회 publish
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


@app.patch("/api/events/{event_id}/unlink")
def unlink_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    with db.get_conn() as conn:
        conn.execute("UPDATE events SET meeting_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (event_id,))
    # sync 핸들러 → broker 내부의 call_soon_threadsafe가 루프 스레드로 안전 전달
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, request: Request, delete_mode: str = "this"):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="다른 팀의 일정은 삭제할 수 없습니다.")
    db.delete_event(event_id, delete_mode, deleted_by=user["name"], team_id=user.get("team_id"))
    # sync 핸들러 → broker 내부의 call_soon_threadsafe가 루프 스레드로 안전 전달
    _sse_publish("events.changed", {"id": event_id, "action": "delete", "team_id": user.get("team_id")})
    return {"ok": True}


# ── SSE 실시간 스트림 ────────────────────────────────────────
@app.get("/api/stream")
async def sse_stream(request: Request):
    """캘린더·칸반·간트 실시간 동기화용 SSE 엔드포인트.

    - WHATUDOIN_SSE_SERVICE_URL 설정 시: SSE service 분리 모드 → 503 반환
      (Front Router가 이미 /api/stream을 SSE service로 라우팅하므로
       이 핸들러에 도달하는 경우는 loopback 직접 접근뿐 — defense-in-depth)
    - 미설정 시(단일 프로세스 fallback): in-process wu_broker 사용
    - 비로그인 게스트 포함 — 페이로드는 id/action 메타 한정
    - 25초마다 ping 주석으로 프록시·브라우저 타임아웃 방지
    - 클라이언트 연결 종료 시 subscribe한 큐를 자동 해제
    """
    # SSE service 분리 모드 가드
    if os.environ.get("WHATUDOIN_SSE_SERVICE_URL"):
        return JSONResponse(
            {"detail": "SSE endpoint moved to SSE service"},
            status_code=503,
        )

    async def gen():
        import time
        queue = await wu_broker.subscribe()
        _last_ping = time.monotonic()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    ev, data = await asyncio.wait_for(queue.get(), timeout=3.0)
                    yield f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 3초마다 disconnect 감지 — 좀비 연결 빠른 정리
                    if await request.is_disconnected():
                        break
                    # 25초마다 ping (프록시·브라우저 타임아웃 방지)
                    if time.monotonic() - _last_ping >= 25.0:
                        yield ": ping\n\n"
                        _last_ping = time.monotonic()
        finally:
            wu_broker.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/kanban")
def get_kanban_events(request: Request, team_id: int = None):
    viewer = auth.get_current_user(request)
    # 팀 기능 그룹 A #10: 칸반은 현재 작업 팀 기준. team_id 미지정 시 resolve_work_team.
    # 비admin 사용자가 작업 팀을 결정할 수 없거나(팀 미배정) 비소속 팀이면 빈 목록 — 다른 팀 일정 누출 방지.
    if not auth.is_admin(viewer):
        scope = _work_scope(request, viewer, team_id)
        if not scope:
            return []
        team_id = next(iter(scope))
    return db.get_kanban_events(team_id, viewer=viewer)


@app.get("/api/my-meetings")
def get_my_meetings(request: Request):
    """내 담당 회의 일정 (오늘 이후, 최대 7개)"""
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="로그인이 필요합니다.")
    return db.get_upcoming_meetings(assignee_name=user["name"], limit=5)


@app.get("/api/project-milestones/calendar")
def get_milestone_calendar_events(request: Request):
    """캘린더용 milestone 이벤트 소스 (로그인 사용자의 프로젝트만, 비로그인 시 빈 배열)"""
    user = auth.get_current_user(request)
    if not user:
        return []
    proj_colors = db.get_project_colors()
    milestones = db.get_calendar_milestones(user["name"], viewer=user)
    for ev in milestones:
        proj_name = ev.get("extendedProps", {}).get("project")
        color = resolve_project_color(proj_name, proj_colors)
        ev["backgroundColor"] = color
        ev["borderColor"] = color
    return milestones


@app.get("/api/my-milestones")
def get_my_milestones(request: Request):
    """내 스케줄 — 다가오는 프로젝트 중간 일정 (오늘 이후, 최대 5개)"""
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="로그인이 필요합니다.")
    return db.get_upcoming_milestones(user["name"], limit=5, viewer=user)


@app.patch("/api/events/{event_id}/datetime")
async def update_event_datetime(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="다른 팀의 일정은 수정할 수 없습니다.")
    data = await request.json()
    db.update_event_datetime(
        event_id,
        start_datetime=data["start_datetime"],
        end_datetime=data.get("end_datetime"),
        all_day=data.get("all_day", event.get("all_day", 0)),
    )
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


@app.patch("/api/events/{event_id}/project")
async def update_event_project(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="다른 팀의 일정은 수정할 수 없습니다.")
    data = await request.json()
    new_proj_name = data.get("project")
    # 히든→일반(또는 무소속) 이동 시 confirm 요구
    old_proj_name = (event.get("project") or "").strip()
    if old_proj_name:
        _old_proj = db.get_project_by_name(old_proj_name)
        if _old_proj and _old_proj.get("is_hidden"):
            _new_is_hidden = False
            if new_proj_name:
                _new_proj = db.get_project_by_name(new_proj_name)
                _new_is_hidden = bool(_new_proj and _new_proj.get("is_hidden"))
            if not _new_is_hidden and not data.get("confirm"):
                return JSONResponse(
                    status_code=400,
                    content={"requires_confirm": True, "message": "히든 프로젝트 밖으로 이동합니다. 계속하시겠습니까?"},
                )
    # 새 프로젝트가 히든이면 멤버십 확인
    _new_proj_for_check = db.get_project_by_name(new_proj_name) if new_proj_name else None
    if _new_proj_for_check and _new_proj_for_check.get("is_hidden") and not auth.is_admin(user):
        if not db.is_hidden_project_visible(_new_proj_for_check["id"], user):
            raise HTTPException(status_code=403, detail="히든 프로젝트에 접근 권한이 없습니다.")
    db.update_event_project(event_id, new_proj_name)
    # 히든 프로젝트로 이동 시 is_public 강제 0
    hidden_forced = False
    if _new_proj_for_check and _new_proj_for_check.get("is_hidden"):
        db.update_event_visibility(event_id, 0)
        hidden_forced = True
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True, "hidden_forced": hidden_forced}


_CONFLICT_EXACT = 70
_CONFLICT_SIMILAR = 50


def _db_row_to_prompt_shape(row: dict) -> dict:
    """DB 행의 start_datetime/end_datetime을 date/start_time/end_time으로 분해."""
    sd = row.get("start_datetime") or ""
    ed = row.get("end_datetime") or ""
    return {
        **row,
        "date":       sd[:10] if sd else "",
        "start_time": sd[11:16] if len(sd) > 10 else None,
        "end_time":   ed[11:16] if len(ed) > 10 else None,
    }


@app.post("/api/events/check-conflicts")
async def check_event_conflicts(request: Request):
    """AI 파싱 결과와 기존 일정의 중복 여부를 검사합니다.

    필드별 가중치 점수화 방식 사용.
    - total >= 70: exact (자동 중복)
    - 50 <= total < 70: similar (LLM funnel 대상, ai_funnel_candidates 첨부)
    - total < 50: pass
    intra-batch(같은 실행 내 후보끼리)도 동일 함수로 비교 후 _conflict_batch 플래그.
    """
    user = _require_editor(request)
    data = await request.json()
    candidates = data.get("events", [])
    team_id = auth.resolve_work_team(request, user)  # 팀 기능 그룹 A #10
    existing = _filter_visible_events(db.get_events_for_conflict_check(team_id), user)

    results = []
    for i, cand in enumerate(candidates):
        # ── DB existing 비교 ──
        scored = []
        for ex in existing:
            s = llm_parser.score_conflict(cand, ex)
            scored.append((s, ex))
        scored.sort(key=lambda x: x[0]["total"], reverse=True)

        conflict = None
        if scored:
            best_score, best_ex = scored[0]
            total = best_score["total"]
            if total >= _CONFLICT_EXACT:
                conflict = {
                    "type":           "exact",
                    "existing_id":    best_ex["id"],
                    "existing_title": best_ex["title"],
                    "similarity":     total,
                    "fields_matched": best_score["fields_matched"],
                }
            elif total >= _CONFLICT_SIMILAR:
                top3 = [
                    {
                        "id":    ex["id"],
                        "title": ex["title"],
                        "date":  (ex.get("start_datetime") or "")[:10],
                        "assignee": ex.get("assignee"),
                        "project":  ex.get("project"),
                        "start_time": (ex.get("start_datetime") or "")[11:16] or None,
                        "end_time":   (ex.get("end_datetime") or "")[11:16] or None,
                        "all_day":    bool(ex.get("all_day")),
                    }
                    for _, ex in scored[:3]
                ]
                conflict = {
                    "type":                "similar",
                    "existing_id":         best_ex["id"],
                    "existing_title":      best_ex["title"],
                    "similarity":          total,
                    "fields_matched":      best_score["fields_matched"],
                    "ai_funnel_candidates": top3,
                }

        # ── intra-batch 비교 (이전 후보들과 비교) ──
        conflict_batch = None
        for j in range(i):
            prev = candidates[j]
            s = llm_parser.score_conflict(cand, {
                **prev,
                "start_datetime": f"{prev.get('date','')}" + (f"T{prev.get('start_time')}" if prev.get("start_time") else ""),
                "end_datetime": f"{prev.get('date','')}" + (f"T{prev.get('end_time')}" if prev.get("end_time") else ""),
            })
            if s["total"] >= _CONFLICT_SIMILAR:
                conflict_batch = {
                    "batch_index":  j,
                    "batch_title":  prev.get("title", ""),
                    "similarity":   s["total"],
                }
                break

        results.append({"conflict": conflict, "conflict_batch": conflict_batch})

    return {"results": results}


@app.post("/api/events/ai-conflict-review")
async def ai_conflict_review(request: Request):
    """similar 구간 후보만 AI가 최종 검토합니다. server-owned top-K 방식.

    클라이언트는 후보 전체와 check-conflicts 결과(conflict 포함)를 보낸다.
    서버가 similar 구간을 골라 각 후보에 대한 top-3 existing만 프롬프트에 주입.
    exact 판정은 AI가 뒤집지 못한다.
    """
    user = _require_editor(request)
    data = await request.json()
    all_events = data.get("events", [])
    model      = data.get("model", llm_parser.DEFAULT_MODEL)
    team_id    = auth.resolve_work_team(request, user)  # 팀 기능 그룹 A #10

    if not all_events:
        return {"results": []}

    # 서버에서 직접 DB 재조회 후 similar 구간 재계산 (클라이언트 check_results 무시)
    existing = _filter_visible_events(db.get_events_for_conflict_check(team_id), user)
    similar_indices: list[int] = []
    ai_funnel_map: dict[int, list] = {}  # ai_candidates 인덱스 → top3 existing

    for i, cand in enumerate(all_events):
        scored = [(llm_parser.score_conflict(cand, ex), ex) for ex in existing]
        scored.sort(key=lambda x: x[0]["total"], reverse=True)
        if scored:
            best_total = scored[0][0]["total"]
            if _CONFLICT_SIMILAR <= best_total < _CONFLICT_EXACT:
                ai_funnel_map[len(similar_indices)] = [_db_row_to_prompt_shape(ex) for _, ex in scored[:3]]
                similar_indices.append(i)

    if not similar_indices:
        return {"results": [{"is_duplicate": False, "reason": "", "existing_title": None} for _ in all_events]}

    ai_candidates = [all_events[i] for i in similar_indices]

    # AI 검토 호출 (후보마다 top-3만 넘김)
    ai_raw_results = await run_in_threadpool(
        llm_parser.review_all_conflicts_with_funnel, ai_candidates, ai_funnel_map, model
    )

    # 결과를 전체 이벤트 인덱스로 매핑
    out = [{"is_duplicate": False, "reason": "", "existing_title": None} for _ in all_events]
    for ai_idx, orig_idx in enumerate(similar_indices):
        if ai_idx < len(ai_raw_results):
            out[orig_idx] = ai_raw_results[ai_idx]

    return {"results": out}


@app.patch("/api/events/{event_id}/kanban")
async def update_event_kanban(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    data = await request.json()
    kwargs = {}
    if "kanban_status" in data:
        kwargs["kanban_status"] = data["kanban_status"]
    if "priority" in data:
        kwargs["priority"] = data["priority"]
    db.update_kanban_status(event_id, **kwargs)
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


@app.get("/api/conflicts")
def check_conflicts(request: Request, start: str, end: str = None, team_id: int = None, exclude_id: int = None):
    user = auth.get_current_user(request)
    if not user:
        return {"conflicts": []}
    conflicts = db.check_conflicts(start, end or start, team_id, exclude_id)
    return {"conflicts": _filter_visible_events(conflicts, user)}


# ── 프로젝트 ─────────────────────────────────────────────

@app.get("/api/projects")
def list_projects(request: Request, team_id: int = None):
    user = auth.get_current_user(request)
    projects = db.get_unified_project_list(viewer=user, work_team_ids=(_work_scope(request, user, team_id) if user else None))
    if not user:
        projects = [p for p in projects if not p.get("is_private")]
    return [p["name"] for p in projects]


@app.get("/api/projects-meta")
def list_projects_meta(request: Request, team_id: int = None):
    """이벤트 모달 담당자 필터용 — 프로젝트별 is_hidden 플래그 반환."""
    user = auth.get_current_user(request)
    projects = db.get_unified_project_list(viewer=user, work_team_ids=(_work_scope(request, user, team_id) if user else None))
    if not user:
        projects = [p for p in projects if not p.get("is_private") and not p.get("is_hidden")]
    return [{"name": p["name"], "is_hidden": bool(p.get("is_hidden"))} for p in projects]


@app.get("/api/hidden-project-assignees")
def hidden_project_assignees(request: Request, project: str):
    """히든 프로젝트 일정 등록 시 담당자 자동완성용 — 멤버면 누구나 호출 가능."""
    user = _require_editor(request)
    proj = _get_hidden_proj_or_404(project, user)
    members = db.get_hidden_project_members(proj["id"])
    return [m["name"] for m in members]


@app.get("/api/project-timeline")
def project_timeline(request: Request, team_id: int = None):
    viewer = auth.get_current_user(request)
    # 팀 기능 그룹 A #10: 간트는 현재 작업 팀 기준 (/api/kanban 과 동일 골격).
    # 비admin(비로그인 포함)이 작업 팀을 결정할 수 없거나 비소속이면 빈 목록 — 다른 팀 자료 누출 방지.
    # admin 은 무필터(team_id 그대로, 보통 None) — 전 팀 슈퍼유저, 의도된 동작.
    if not auth.is_admin(viewer):
        scope = _work_scope(request, viewer, team_id)
        if not scope:
            return []
        team_id = next(iter(scope))
    proj_colors = db.get_project_colors()
    teams = db.get_project_timeline(team_id, viewer=viewer)
    for team in teams:
        for project in team.get("projects", []):
            project["color"] = resolve_project_color(project.get("name"), proj_colors)
    return teams


# ── 통합 프로젝트 목록 API ──────────────────────────────────

@app.get("/api/project-list")
def api_project_list(request: Request, team_id: int = None):
    """모든 페이지에서 공통으로 사용하는 통합 프로젝트 목록.
    projects 테이블 + events.project + checklists.project 합산, [{name, color, is_active, id}]
    """
    user = _require_editor(request)
    return db.get_unified_project_list(viewer=user, work_team_ids=_work_scope(request, user, team_id))


def _publish_project_changed(name: str | None, action: str, project: dict | None = None):
    """Public SSE payload must not disclose hidden project names."""
    publish_name = name
    if name:
        proj = project if project is not None else db.get_project_by_name(name)
        if proj and proj.get("is_hidden"):
            publish_name = None
    _sse_publish("projects.changed", {"name": publish_name, "action": action})


# ── 프로젝트 관리 API ────────────────────────────────────

@app.get("/api/manage/projects")
def manage_list_projects(request: Request, team_id: int = None):
    user = _require_editor(request)
    return db.get_all_projects_with_events(viewer=user, work_team_ids=_work_scope(request, user, team_id))


@app.post("/api/manage/projects")
async def manage_create_project(request: Request):
    user = _require_editor(request)
    data = await request.json()
    name = data.get("name", "").strip()
    color = data.get("color") or None
    memo  = (data.get("memo") or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="프로젝트 이름을 입력하세요.")
    # 팀 기능 그룹 C #16: 작업 팀 명시 보장 (admin은 묵시 first_active fallback 금지).
    team_id = auth.require_admin_work_team(request, user, explicit_id=data.get("team_id"))
    # 같은 팀 안에서 같은 이름 사전 차단.
    norm = db.normalize_name(name)
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM projects "
            " WHERE team_id = ? AND name_norm = ? AND deleted_at IS NULL",
            (team_id, norm),
        ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="같은 이름의 프로젝트가 이미 있습니다.")
    import sqlite3
    try:
        proj_id = db.create_project(name, color, memo, team_id=team_id)
    except sqlite3.IntegrityError:
        # race로 인한 중복 — 사전 검사 통과 후 INSERT 직전 다른 호출이 선점한 경우.
        raise HTTPException(status_code=409, detail="같은 이름의 프로젝트가 이미 있습니다.")
    _publish_project_changed(name, "create")
    return {"id": proj_id, "name": name, "team_id": team_id}


@app.put("/api/manage/projects/{name:path}")
async def manage_rename_project(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    new_name = data.get("name", "").strip()
    force    = data.get("force", False)
    if not new_name:
        raise HTTPException(status_code=400, detail="새 이름을 입력하세요.")
    target_proj = db.get_project(new_name) if new_name != name else None
    if target_proj and target_proj.get("is_hidden") and not auth.can_edit_project(user, target_proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    # 같은 팀 안에서만 충돌 검사 (다른 팀의 동일 이름은 허용).
    if new_name != name and not force:
        proj_team_id = proj.get("team_id")
        if proj_team_id is not None:
            new_norm = db.normalize_name(new_name)
            with db.get_conn() as conn:
                conflict = conn.execute(
                    "SELECT 1 FROM projects "
                    " WHERE team_id = ? AND name_norm = ? AND deleted_at IS NULL "
                    "   AND id != ?",
                    (proj_team_id, new_norm, proj["id"]),
                ).fetchone()
            if conflict:
                raise HTTPException(
                    status_code=409,
                    detail=f'"{new_name}" 프로젝트가 이미 존재합니다. 병합하시겠습니까?',
                )
    import sqlite3
    try:
        db.rename_project(name, new_name, merge=bool(force))
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f'"{new_name}" 프로젝트가 이미 존재합니다. 병합하시겠습니까?',
        )
    _publish_project_changed(new_name, "update")
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/status")
async def manage_project_status(name: str, request: Request):
    user = _require_editor(request)
    # projects 테이블에 없어도 events.project에만 존재하는 경우를 허용
    proj = db.get_project(name) or {}
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.update_project_status(name, is_active)
    _publish_project_changed(name, "update", project=proj)
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/privacy")
async def manage_project_privacy(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    is_private = 1 if data.get("is_private") else 0
    db.update_project_privacy(name, is_private)
    _publish_project_changed(name, "update", project=proj)
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/memo")
async def manage_project_memo(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    db.update_project_memo(name, data.get("memo"))
    _publish_project_changed(name, "update", project=proj)
    return {"ok": True}


@app.get("/api/project-colors")
def project_colors_api(request: Request):
    """프로젝트명 → 최종 결정 색상 딕셔너리 반환 (DB지정 > 해시팔레트, 모든 프로젝트 포함)"""
    user = auth.get_current_user(request)
    raw_colors = db.get_project_colors()
    all_projects = db.get_unified_project_list(active_only=False, viewer=user)
    if not user:
        private_names = {p["name"] for p in all_projects if p.get("is_private")}
        all_projects = [p for p in all_projects if p["name"] not in private_names]
    result = {}
    for proj in all_projects:
        result[proj["name"]] = resolve_project_color(proj["name"], raw_colors)
    result[_UNASSIGNED_PROJECT_NAME] = resolve_project_color(_UNASSIGNED_PROJECT_NAME, raw_colors)
    return result


# ── 비로그인 공개 포털 v2 (`/팀이름/{메뉴}` 정식 화면용) ────────────────
# 비로그인 진입 재설계 v2 (사용자 요청: 로그인 화면과 동일 레이아웃 + 공개 항목만).
# 정식 데이터 API (`/api/kanban` 등) 는 비로그인 + work_team 미해결 시 빈 목록을 반환하므로,
# 팀 한정 + 공개 필터링 전용으로 별도 엔드포인트를 분리한다 (advisor 권고 옵션 B).
# 가드는 _public_team_menu_gate 1곳으로 집중 — 미래의 API 추가 시 동일 패턴 강제.

def _public_team_menu_gate(team_id: int, menu_key: str) -> dict:
    """공개 포털 데이터 API 공통 가드.

    팀 존재 + 미삭제 + 해당 메뉴 외부공개 ON 인 경우만 통과. 어느 조건 실패라도 404
    (존재 oracle 차단 — pending 팀명 확인 불가하게 detail 동일).
    """
    team = db.get_team_active(team_id)  # deleted_at IS NULL 자동 적용 — 삭제 예정 팀은 None.
    if not team:
        raise HTTPException(status_code=404, detail="Not Found")
    menu_vis = db.get_team_menu_visibility(team_id)
    if not menu_vis.get(menu_key, False):
        raise HTTPException(status_code=404, detail="Not Found")
    return team


@app.get("/api/public/teams/{team_id}/kanban")
def public_kanban_events(team_id: int):
    _public_team_menu_gate(team_id, "kanban")
    return db.get_kanban_events(team_id, viewer=None)


@app.get("/api/public/teams/{team_id}/project-timeline")
def public_project_timeline(team_id: int):
    _public_team_menu_gate(team_id, "gantt")
    proj_colors = db.get_project_colors()
    teams = db.get_project_timeline(team_id, viewer=None)
    for team in teams:
        for project in team.get("projects", []):
            project["color"] = resolve_project_color(project.get("name"), proj_colors)
    return teams


@app.get("/api/public/teams/{team_id}/checklists")
def public_checklists(team_id: int):
    _public_team_menu_gate(team_id, "check")
    # get_checklists 는 viewer=None 일 때 is_public=1 + 외부 가시 프로젝트로 자동 필터.
    # team_id 인자가 없으므로 결과를 팀으로 필터링 (get_public_portal_data 와 동일 패턴).
    return [c for c in db.get_checklists(viewer=None, include_done_projects=True)
            if c.get("team_id") == team_id]


@app.patch("/api/manage/projects/{name:path}/color")
async def manage_project_color(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    color = data.get("color", "").strip() or None
    db.update_project_color(name, color)
    _publish_project_changed(name, "update", project=proj)
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/dates")
async def manage_project_dates(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    db.update_project_dates(name, data.get("start_date"), data.get("end_date"))
    _publish_project_changed(name, "update", project=proj)
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/milestones")
async def manage_project_milestones(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = await request.json()
    items = data.get("milestones") or []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="milestones는 배열이어야 합니다.")
    if len(items) > 10:
        raise HTTPException(status_code=400, detail="중간 일정은 최대 10개까지입니다.")
    cleaned = []
    seen_dates = set()
    for m in items:
        title = (m.get("title") or "").strip()
        date  = (m.get("date") or "").strip()
        if not title or not date:
            raise HTTPException(status_code=400, detail="제목과 일자를 모두 입력하세요.")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(status_code=400, detail="일자 형식이 올바르지 않습니다.")
        if date in seen_dates:
            raise HTTPException(status_code=400, detail=f"동일한 일자가 중복됩니다: {date}")
        seen_dates.add(date)
        cleaned.append({"title": title[:100], "date": date})
    # 일정 기간 범위 검사
    with db.get_conn() as conn:
        proj_row = conn.execute(
            "SELECT start_date, end_date FROM projects WHERE name = ?", (name,)
        ).fetchone()
    if proj_row:
        s, e = proj_row["start_date"], proj_row["end_date"]
        if s and e:
            for m in cleaned:
                if m["date"] < s or m["date"] > e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"일정 기간({s} ~ {e})을 벗어납니다: {m['date']}"
                    )
    try:
        db.set_project_milestones(name, cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _publish_project_changed(name, "update", project=proj)
    return {"ok": True, "milestones": cleaned}


# ── 히든 프로젝트 API ──────────────────────────────────────

@app.post("/api/manage/hidden-projects")
async def create_hidden_project_route(request: Request):
    user = _require_editor(request)
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="프로젝트 이름을 입력하세요.")
    # 팀 기능 그룹 C #16: 히든 프로젝트는 항상 팀 컨텍스트 안에서만 생성 — 명시 보장.
    team_id = auth.require_admin_work_team(request, user, explicit_id=data.get("team_id"))
    result = db.create_hidden_project(
        name=name,
        color=data.get("color", ""),
        memo=data.get("memo", ""),
        owner_id=user["id"],
        team_id=team_id,
    )
    if result is None:
        raise HTTPException(status_code=422, detail="생성할 수 없습니다. 다른 이름을 넣어주세요.")
    _sse_publish("projects.changed", {"name": None, "action": "create"})
    return result


@app.get("/api/manage/hidden-projects/{name}/can-manage")
async def can_manage_hidden_project(name: str, request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    proj = db.get_project_by_name(name)
    if not proj or not proj.get("is_hidden"):
        raise HTTPException(status_code=404)
    if not db.is_hidden_project_visible(proj["id"], user):
        raise HTTPException(status_code=404)
    is_admin = user.get("role") == "admin"
    is_owner = proj.get("owner_id") == user.get("id")
    return {"can_manage": is_admin or is_owner, "is_owner": is_owner, "is_admin": is_admin}


def _assert_can_assign_to_project(user, project_name: str | None):
    """쓰기 경로 공통: 새 project가 히든이면 멤버십 강제. admin은 통과."""
    if not project_name or auth.is_admin(user):
        return
    proj = db.get_project_by_name(project_name)
    if proj and proj.get("is_hidden"):
        if not db.is_hidden_project_visible(proj["id"], user):
            raise HTTPException(status_code=403, detail="히든 프로젝트에 접근 권한이 없습니다.")


def _assert_assignees_in_hidden_project(project_name: str | None, assignee_csv: str | None):
    """히든 프로젝트 일정/업무의 assignee가 모두 해당 프로젝트 멤버인지 검증. 한 명이라도 아니면 422."""
    if not project_name or not assignee_csv:
        return
    proj = db.get_project_by_name(project_name)
    if not proj or not proj.get("is_hidden"):
        return
    member_names = {m["name"] for m in db.get_hidden_project_members(proj["id"])}
    invalid = [n.strip() for n in assignee_csv.split(",")
               if n.strip() and n.strip() not in member_names]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"히든 프로젝트 멤버가 아닌 담당자: {', '.join(invalid)}"
        )


def _filter_visible_events(events: list, user) -> list:
    """비멤버에게 히든 프로젝트 일정을 필터링. admin은 전체 반환, 익명은 히든 전부 제거."""
    if user and auth.is_admin(user):
        return events
    cache: dict[str, bool] = {}
    out = []
    for e in events:
        proj_name = e.get("project")
        if not proj_name:
            out.append(e)
            continue
        if proj_name not in cache:
            proj = db.get_project_by_name(proj_name)
            if proj and proj.get("is_hidden"):
                cache[proj_name] = bool(user) and bool(db.is_hidden_project_visible(proj["id"], user))
            else:
                cache[proj_name] = True
        if cache[proj_name]:
            out.append(e)
    return out


def _get_hidden_proj_or_404(name: str, user: dict = None):
    """히든 프로젝트를 이름으로 조회. 없거나 히든 아니면 404.
    user가 주어지면 visibility 체크 추가 — 비멤버에게 프로젝트 존재 누설 방지.
    """
    proj = db.get_project_by_name(name)
    if not proj or not proj.get("is_hidden"):
        raise HTTPException(status_code=404)
    if user and not db.is_hidden_project_visible(proj["id"], user):
        raise HTTPException(status_code=404)
    return proj


def _require_hidden_can_manage(user: dict, proj: dict):
    """can_manage 권한(owner 또는 admin) 검증. 없으면 403."""
    is_admin = user.get("role") == "admin"
    is_owner = proj.get("owner_id") == user.get("id")
    if not is_admin and not is_owner:
        raise HTTPException(status_code=403, detail="관리 권한이 없습니다.")
    return is_admin, is_owner


@app.get("/api/manage/hidden-projects/{name}/members")
async def get_hidden_project_members_route(name: str, request: Request):
    user = _require_editor(request)
    proj = _get_hidden_proj_or_404(name, user)
    _require_hidden_can_manage(user, proj)
    members = db.get_hidden_project_members(proj["id"])
    return {"members": members}


@app.get("/api/manage/hidden-projects/{name}/addable-members")
async def get_hidden_project_addable_members_route(name: str, request: Request):
    user = _require_editor(request)
    proj = _get_hidden_proj_or_404(name, user)
    _require_hidden_can_manage(user, proj)
    addable = db.get_hidden_project_addable_members(proj["id"])
    return {"addable_members": addable}


@app.post("/api/manage/hidden-projects/{name}/members")
async def add_hidden_project_member_route(name: str, request: Request):
    user = _require_editor(request)
    proj = _get_hidden_proj_or_404(name, user)
    _require_hidden_can_manage(user, proj)
    data = await request.json()
    target_user_id = data.get("user_id")
    if not target_user_id:
        raise HTTPException(status_code=400, detail="user_id를 입력하세요.")
    result = db.add_hidden_project_member(proj["id"], target_user_id)
    if result is False:
        raise HTTPException(status_code=403, detail="해당 팀의 승인된 멤버만 멤버로 추가할 수 있습니다.")
    if result is None:
        raise HTTPException(status_code=409, detail="이미 멤버입니다.")
    _sse_publish("projects.changed", {"name": None, "action": "member_add"})
    return {"ok": True}


@app.delete("/api/manage/hidden-projects/{name}/members/{user_id}")
async def remove_hidden_project_member_route(name: str, user_id: int, request: Request):
    user = _require_editor(request)
    proj = _get_hidden_proj_or_404(name, user)
    _require_hidden_can_manage(user, proj)
    result = db.remove_hidden_project_member(proj["id"], user_id)
    if result is False:
        raise HTTPException(status_code=403, detail="관리 권한을 먼저 이양하세요.")
    _sse_publish("projects.changed", {"name": None, "action": "member_remove"})
    return {"ok": True}


@app.post("/api/manage/hidden-projects/{name}/transfer-owner")
async def transfer_hidden_project_owner_route(name: str, request: Request):
    user = _require_editor(request)
    proj = _get_hidden_proj_or_404(name, user)
    # owner만 가능 (admin 불가)
    if proj.get("owner_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="관리 권한이 없습니다.")
    data = await request.json()
    new_owner_id = data.get("user_id")
    if not new_owner_id:
        raise HTTPException(status_code=400, detail="user_id를 입력하세요.")
    result = db.transfer_hidden_project_owner(proj["id"], new_owner_id, user["id"])
    if not result:
        raise HTTPException(status_code=400, detail="해당 사용자는 현재 프로젝트 멤버가 아닙니다.")
    _sse_publish("projects.changed", {"name": None, "action": "owner_transfer"})
    return {"ok": True}


@app.post("/api/manage/hidden-projects/{name}/change-owner")
async def admin_change_hidden_project_owner_route(name: str, request: Request):
    user = _require_admin(request)
    proj = _get_hidden_proj_or_404(name, user)
    data = await request.json()
    new_owner_id = data.get("user_id")
    if not new_owner_id:
        raise HTTPException(status_code=400, detail="user_id를 입력하세요.")
    result = db.admin_change_hidden_project_owner(proj["id"], new_owner_id)
    if not result:
        raise HTTPException(status_code=400, detail="해당 사용자는 현재 프로젝트 멤버가 아닙니다.")
    _sse_publish("projects.changed", {"name": None, "action": "owner_change"})
    return {"ok": True}


@app.delete("/api/manage/projects/{name:path}/items")
async def manage_delete_project_items(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    body = await request.json()
    event_ids = [int(x) for x in body.get('event_ids', [])]
    checklist_ids = [int(x) for x in body.get('checklist_ids', [])]
    if not event_ids and not checklist_ids:
        raise HTTPException(status_code=400, detail='선택된 항목 없음')
    ev_n, ck_n = db.bulk_soft_delete_project_items(name, event_ids, checklist_ids, user["name"], user.get("team_id"))
    _sse_publish("events.changed", {"id": None, "action": "bulk_update", "team_id": user.get("team_id")})
    return {"deleted_events": ev_n, "deleted_checklists": ck_n}


@app.delete("/api/manage/projects/{name:path}")
async def manage_delete_project(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    db.delete_project(name, deleted_by=user["name"], team_id=user.get("team_id"))
    # 프로젝트 삭제는 이벤트에도 영향 → 두 채널 모두 publish
    _publish_project_changed(name, "delete", project=proj)
    _sse_publish("events.changed", {"id": None, "action": "bulk_update", "team_id": user.get("team_id")})
    return {"ok": True}


# P1-1 catchup: meetings + teams/{id}/{subdir} 두 경로 모두 인식.
# non-capturing 그룹 사용 → findall/finditer 결과는 풀 매칭 문자열.
_IMG_URL_RE = re.compile(
    r'/uploads/(?:meetings/\d{4}/\d{2}|teams/\d+/[^/]+/\d{4}/\d{2})/[\w\-.]+'
)


def _img_url_to_disk(url: str) -> Path | None:
    """업로드 이미지 URL을 디스크 절대 경로로 변환.

    - ``/uploads/meetings/...`` → ``MEETINGS_DIR / rel``
    - ``/uploads/teams/{id}/{subdir}/...`` → ``_RUN_DIR / "uploads" / rel``
    - 그 외 → ``None``
    """
    if url.startswith("/uploads/meetings/"):
        rel = url[len("/uploads/meetings/"):]
        return MEETINGS_DIR / rel
    if url.startswith("/uploads/teams/"):
        rel = url[len("/uploads/"):]  # "teams/{id}/{sub}/.../file"
        return _RUN_DIR / "uploads" / rel
    return None


def _safe_filename(s: str, fallback: str = "untitled") -> str:
    s = re.sub(r'[\\/:*?"<>|#^\[\]]+', "_", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip(". ")
    return s or fallback


def _yaml_str(v) -> str:
    s = "" if v is None else str(v).replace("\r\n", "\n").strip()
    if not s:
        return '""'
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _esc(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\r\n", "\n").strip()


def _build_index_md(name: str, proj: dict | None,
                    events: list[dict], checklists: list[dict],
                    event_files: list[str], checklist_files: list[str], exported_at: str,
                    meetings: list[dict] | None = None, meeting_files: list[str] | None = None) -> str:
    lines = ["---", f"project: {_yaml_str(name)}", "type: project"]
    if proj:
        if proj.get("color"):      lines.append(f"color: {_yaml_str(proj.get('color'))}")
        if proj.get("start_date"): lines.append(f"start_date: {_esc(proj.get('start_date'))}")
        if proj.get("end_date"):   lines.append(f"end_date: {_esc(proj.get('end_date'))}")
        lines.append(f"is_active: {'true' if proj.get('is_active', 1) else 'false'}")
    lines += [f"exported_at: {exported_at}", "tags: [whatudoin, project-export]", "---", "",
              f"# {name}", ""]
    if proj and proj.get("memo"):
        lines.append("> " + _esc(proj["memo"]).replace("\n", "\n> "))
        lines.append("")
    lines.append(f"## 📅 일정 ({len(events)}개)")
    lines.append("")
    if event_files:
        for stem in event_files:
            lines.append(f"- [[일정/{stem}]]")
    else:
        lines.append("_연결된 일정 없음_")
    lines.append("")
    if meetings is not None:
        lines.append(f"## 🗓️ 회의 ({len(meetings)}개)")
        lines.append("")
        if meeting_files:
            for stem in meeting_files:
                lines.append(f"- [[회의/{stem}]]")
        else:
            lines.append("_연결된 회의 없음_")
        lines.append("")
    lines.append(f"## ✅ 체크리스트 ({len(checklists)}개)")
    lines.append("")
    if checklist_files:
        for stem in checklist_files:
            lines.append(f"- [[체크리스트/{stem}]]")
    else:
        lines.append("_연결된 체크리스트 없음_")
    lines.append("")
    return "\n".join(lines)


def _build_doc_md(doc: dict, exported_at: str,
                  images: list | None = None,
                  include_backlink: bool = False) -> str:
    title = _esc(doc.get("title")) or "(제목 없음)"
    lines = ["---", "type: doc", f"title: {_yaml_str(title)}"]
    if doc.get("meeting_date"):
        lines.append(f"meeting_date: {_esc(doc.get('meeting_date'))}")
    lines.append(f"is_team_doc: {'true' if doc.get('is_team_doc', 1) else 'false'}")
    lines.append(f"is_public: {'true' if doc.get('is_public', 0) else 'false'}")
    lines.append(f"team_share: {'true' if doc.get('team_share', 0) else 'false'}")
    if doc.get("author_name"):
        lines.append(f"author: {_yaml_str(doc.get('author_name'))}")
    if doc.get("team_name"):
        lines.append(f"team: {_yaml_str(doc.get('team_name'))}")
    updated = _esc(doc.get("updated_at") or doc.get("created_at") or "")
    if updated:
        lines.append(f"updated_at: {updated[:19].replace('T', ' ')}")
    lines += [f"exported_at: {exported_at}", "tags: [whatudoin, doc]", "---", "",
              f"# {title}", ""]
    if include_backlink:
        lines += [f"← [[index]]", ""]
    raw = (doc.get("content") or "").replace("\r\n", "\n").strip()
    if raw:
        rewritten, found = _rewrite_image_paths(_normalize_markdown_for_export(raw))
        if images is not None:
            images.extend(found)
        lines.append(rewritten)
    else:
        lines.append("_(내용 없음)_")
    # 첨부파일 목록 추가
    attachments = doc.get("attachments") or []
    if isinstance(attachments, str):
        import json as _json
        try:
            attachments = _json.loads(attachments)
        except Exception:
            attachments = []
    if attachments:
        lines += ["", "## 첨부파일", ""]
        for att in attachments:
            url  = att.get("url", "")
            name = att.get("name", url.rsplit("/", 1)[-1])
            basename = url.rsplit("/", 1)[-1]
            lines.append(f"- [{name}](attachments/{basename})")
            if images is not None and url:
                # P1-1 catchup: meetings + teams 양쪽 모두 지원.
                disk = _img_url_to_disk(url)
                if disk is not None:
                    images.append((disk, f"attachments/{basename}"))
    lines.append("")
    return "\n".join(lines)


def _build_event_md(project_name: str | None, ev: dict, exported_at: str,
                    include_backlink: bool = True) -> str:
    title = _esc(ev.get("title")) or "(제목 없음)"
    lines = ["---", "type: event"]
    if project_name is not None:
        lines.append(f"project: {_yaml_str(project_name)}")
    lines += [f"title: {_yaml_str(title)}"]
    if ev.get("start_datetime"): lines.append(f"start: {_esc(ev.get('start_datetime'))}")
    if ev.get("end_datetime"):   lines.append(f"end: {_esc(ev.get('end_datetime'))}")
    if ev.get("assignee"):       lines.append(f"assignee: {_yaml_str(ev.get('assignee'))}")
    if ev.get("priority"):       lines.append(f"priority: {_yaml_str(ev.get('priority'))}")
    if ev.get("location"):       lines.append(f"location: {_yaml_str(ev.get('location'))}")
    status = "완료" if ev.get("is_active") == 0 else "진행 중"
    lines.append(f"status: {_yaml_str(status)}")
    lines += [f"exported_at: {exported_at}", "tags: [whatudoin, event]", "---", "",
              f"# {title}", ""]
    if include_backlink:
        lines += [f"← [[index]]", ""]
    s = _esc(ev.get("start_datetime")); e = _esc(ev.get("end_datetime"))
    if s and e and e != s:
        lines.append(f"- **기간**: {s} ~ {e}")
    elif s:
        lines.append(f"- **일시**: {s}")
    if ev.get("assignee"): lines.append(f"- **담당**: {_esc(ev.get('assignee'))}")
    if ev.get("priority"): lines.append(f"- **우선순위**: {_esc(ev.get('priority'))}")
    if ev.get("location"): lines.append(f"- **장소**: {_esc(ev.get('location'))}")
    lines.append(f"- **상태**: {status}")
    desc = _esc(ev.get("description"))
    if desc:
        lines.append("")
        lines.append("## 설명")
        lines.append("")
        lines.append(desc)
    lines.append("")
    return "\n".join(lines)


_HTML_TABLE_RE = re.compile(r'<table[\s\S]*?</table>', re.IGNORECASE)


def _html_table_to_gfm(html: str) -> str | None:
    """단일 HTML <table>을 GFM 마크다운 테이블로 변환.
    colspan/rowspan > 1이면 None 반환 (HTML 유지)."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows: list[list[str]] = []
            self._row: list[str] | None = None
            self._cell: list[str] | None = None
            self._list_stack: list[dict] = []
            self._span_stack: list[str | None] = []
            self._link_stack: list[str | None] = []
            self._blockquote_depth = 0
            self._in_pre = False
            self.bad = False

        def _append_cell(self, text: str):
            if self._cell is not None and text:
                self._ensure_blockquote_prefix()
                self._cell.append(text)

        def _cell_has_content(self) -> bool:
            return bool(self._cell and ''.join(self._cell).strip())

        def _append_break(self):
            if self._cell_has_content() and self._cell and self._cell[-1] != '<br>':
                self._cell.append('<br>')

        def _ensure_blockquote_prefix(self):
            if self._blockquote_depth <= 0 or self._cell is None:
                return
            if not self._cell_has_content() or self._cell[-1] == '<br>':
                self._cell.append('>' * self._blockquote_depth + ' ')

        @staticmethod
        def _span_is_complex(value: str | None) -> bool:
            if value is None:
                return False
            try:
                return int(value) > 1
            except ValueError:
                return True

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            a = dict(attrs)
            if tag == 'tr':
                self._row = []
            elif tag in ('td', 'th'):
                if self._span_is_complex(a.get('colspan')) or self._span_is_complex(a.get('rowspan')):
                    self.bad = True
                self._cell = []
                self._list_stack = []
                self._span_stack = []
                self._link_stack = []
                self._blockquote_depth = 0
                self._in_pre = False
            elif tag == 'img' and self._cell is not None:
                src = a.get('src', '')
                alt = a.get('alt', '')
                style = a.get('style', '')
                w = re.search(r'width:\s*(\d+)px', style)
                # GFM 테이블 셀 안에서는 | 를 \| 로 이스케이프
                self._append_cell(f'![{alt}\\|{w.group(1)}]({src})' if w else f'![{alt}]({src})')
            elif tag == 'br' and self._cell is not None:
                self._append_break()
            elif tag in ('ul', 'ol') and self._cell is not None:
                data_type = (a.get('data-type') or '').lower()
                list_type = 'task' if data_type == 'tasklist' else tag
                start = 1
                if tag == 'ol':
                    try:
                        start = int(a.get('start', 1))
                    except ValueError:
                        start = 1
                self._list_stack.append({'type': list_type, 'counter': start - 1})
            elif tag == 'li' and self._cell is not None:
                self._append_break()
                current = self._list_stack[-1] if self._list_stack else {'type': 'ul', 'counter': 0}
                indent = '  ' * max(len(self._list_stack) - 1, 0)
                if current['type'] == 'ol':
                    current['counter'] += 1
                    marker = f"{current['counter']}. "
                elif current['type'] == 'task':
                    checked = (a.get('data-checked') or '').lower() in ('true', '1', 'checked')
                    marker = '- [x] ' if checked else '- [ ] '
                else:
                    marker = '- '
                self._append_cell(f'{indent}{marker}')
            elif tag in ('strong', 'b') and self._cell is not None:
                self._append_cell('**')
            elif tag in ('em', 'i') and self._cell is not None:
                self._append_cell('*')
            elif tag in ('s', 'strike', 'del') and self._cell is not None:
                self._append_cell('~~')
            elif tag == 'mark' and self._cell is not None:
                self._append_cell('==')
            elif tag == 'code' and self._cell is not None and not self._in_pre:
                self._append_cell('`')
            elif tag == 'pre' and self._cell is not None:
                self._append_break()
                self._append_cell('```<br>')
                self._in_pre = True
            elif tag == 'blockquote' and self._cell is not None:
                self._append_break()
                self._blockquote_depth += 1
            elif tag == 'hr' and self._cell is not None:
                self._append_break()
                self._append_cell('---')
                self._append_break()
            elif tag == 'a' and self._cell is not None:
                self._link_stack.append(a.get('href'))
                self._append_cell('[')
            elif tag == 'span' and self._cell is not None:
                data_type = (a.get('data-type') or '').lower()
                if data_type == 'inline-math':
                    self._append_cell('$' + (a.get('data-latex') or '') + '$')
                    self._span_stack.append(None)
                elif data_type == 'obsidian-comment':
                    self._append_cell('%%')
                    self._span_stack.append('obsidian-comment')
                else:
                    self._span_stack.append(None)
            elif tag == 'div' and self._cell is not None and (a.get('data-type') or '').lower() == 'block-math':
                self._append_break()
                self._append_cell('$$<br>' + (a.get('data-latex') or '') + '<br>$$')
                self._append_break()

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in ('td', 'th') and self._cell is not None and self._row is not None:
                raw = ''.join(self._cell).strip()
                raw = re.sub(r'^(?:<br>\s*)+', '', raw)
                raw = re.sub(r'(?:\s*<br>)+$', '', raw)
                # 이미 이스케이프된 \| 는 건드리지 않고 나머지 | 만 \| 로 변환
                escaped = re.sub(r'(?<!\\)\|', r'\\|', raw)
                self._row.append(escaped)
                self._cell = None
                self._list_stack = []
                self._span_stack = []
                self._link_stack = []
                self._blockquote_depth = 0
                self._in_pre = False
            elif tag == 'tr' and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None
            elif tag in ('ul', 'ol') and self._cell is not None:
                if self._list_stack:
                    self._list_stack.pop()
            elif tag in ('strong', 'b') and self._cell is not None:
                self._append_cell('**')
            elif tag in ('em', 'i') and self._cell is not None:
                self._append_cell('*')
            elif tag in ('s', 'strike', 'del') and self._cell is not None:
                self._append_cell('~~')
            elif tag == 'mark' and self._cell is not None:
                self._append_cell('==')
            elif tag == 'code' and self._cell is not None and not self._in_pre:
                self._append_cell('`')
            elif tag == 'pre' and self._cell is not None:
                self._append_cell('<br>```')
                self._in_pre = False
                self._append_break()
            elif tag == 'blockquote' and self._cell is not None:
                self._blockquote_depth = max(self._blockquote_depth - 1, 0)
                self._append_break()
            elif tag == 'a' and self._cell is not None:
                href = self._link_stack.pop() if self._link_stack else None
                self._append_cell(f']({href})' if href else ']')
            elif tag == 'span' and self._cell is not None:
                span_type = self._span_stack.pop() if self._span_stack else None
                if span_type == 'obsidian-comment':
                    self._append_cell('%%')
            elif tag in ('p', 'div') and self._cell is not None:
                self._append_break()

        def handle_data(self, data):
            if self._cell is not None:
                if not data.strip():
                    return
                t = data.replace('\r\n', '\n').replace('\r', '\n')
                t = re.sub(r'[ \t\f\v]+', ' ', t)
                t = re.sub(r' *\n+ *', '<br>', t)
                self._append_cell(t)

        def handle_entityref(self, name):
            if self._cell is not None:
                self._append_cell({'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"', 'nbsp': ' '}.get(name, ''))

        def handle_charref(self, name):
            if self._cell is not None:
                try:
                    self._append_cell(chr(int(name[1:], 16) if name.startswith('x') else int(name)))
                except (ValueError, OverflowError):
                    pass

    p = _P()
    p.feed(html)
    if p.bad or not p.rows:
        return None
    max_cols = max(len(r) for r in p.rows)
    if max_cols == 0:
        return None

    lines = []
    for i, row in enumerate(p.rows):
        padded = row + [''] * (max_cols - len(row))
        lines.append('| ' + ' | '.join(padded) + ' |')
        if i == 0:
            lines.append('| ' + ' | '.join(['---'] * max_cols) + ' |')
    return '\n'.join(lines)


def _fix_tiptap_lists_for_obsidian(html: str) -> str:
    """tiptap task list의 data-checked 속성을 Obsidian 호환 <input type="checkbox">로 변환."""
    def _replace_task_li(m: re.Match) -> str:
        checked = bool(re.search(r'data-checked=["\']true["\']', m.group(0), re.IGNORECASE))
        cb = '<input type="checkbox" checked disabled>' if checked else '<input type="checkbox" disabled>'
        return f'<li>{cb} '
    html = re.sub(r'<li[^>]*data-type=["\']taskItem["\'][^>]*>', _replace_task_li, html, flags=re.IGNORECASE)
    html = re.sub(r'<ul[^>]*data-type=["\']taskList["\'][^>]*>', '<ul>', html, flags=re.IGNORECASE)
    return html


def _convert_html_tables_to_gfm(md: str) -> str:
    """마크다운 텍스트 내 <table>…</table> 블록을 GFM 테이블로 변환.
    목록 포함·colspan/rowspan 등 변환 불가 시 tiptap 속성만 정리 후 HTML 유지."""
    def _repl(m: re.Match) -> str:
        html = m.group(0)
        result = _html_table_to_gfm(html)
        if result is not None:
            return result
        return _fix_tiptap_lists_for_obsidian(html)
    return _HTML_TABLE_RE.sub(_repl, md)


def _clean_callouts_for_export(md: str) -> str:
    """tiptap-markdown이 이스케이프한 콜아웃 구문을 옵시디언 표준으로 정리.
    > \\[!type\\] → > [!type]
    콜아웃 헤더 바로 뒤의 빈 '>' 줄도 제거한다."""
    # \[!type\] 이스케이프 제거 (> 로 시작하는 줄에서만)
    md = re.sub(r'^(> *)\\\[(![\w]+)\\\]', r'\1[\2]', md, flags=re.MULTILINE)
    # 콜아웃 헤더 줄 바로 다음 빈 > 줄 제거
    md = re.sub(r'(^> *\[![\w]+\][^\n]*\n)((?:^> *\n)+)', r'\1', md, flags=re.MULTILINE)
    return md


def _clean_footnotes_for_export(md: str) -> str:
    """tiptap-markdown이 이스케이프한 Obsidian 각주 표기를 되돌린다."""
    return re.sub(r'\\\[\^([^\]\s]+)\\\]', r'[^\1]', md)


def _clean_empty_paragraphs_for_export(md: str) -> str:
    """빈 단락 round-trip용 <p></p> HTML이 MD 내보내기에 노출되지 않게 제거한다."""
    md = re.sub(
        r'(?im)^[ \t]*<p(?:\s[^>]*)?>\s*(?:&nbsp;|\u00a0|<br\s*/?>)?\s*</p>[ \t]*$',
        '',
        md,
    )
    lines = md.split('\n')
    out: list[str] = []
    blank_count = 0
    in_code_fence = False
    in_math_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_fence = not in_code_fence
        elif stripped == '$$' and not in_code_fence:
            in_math_fence = not in_math_fence

        if not in_code_fence and not in_math_fence and stripped == '':
            blank_count += 1
            if blank_count > 1:
                continue
        else:
            blank_count = 0
        out.append(line)
    return '\n'.join(out)


def _normalize_markdown_for_export(md: str) -> str:
    md = _convert_html_tables_to_gfm(md)
    md = _clean_callouts_for_export(md)
    md = _clean_footnotes_for_export(md)
    md = _clean_empty_paragraphs_for_export(md)
    return md.strip()


def _rewrite_image_paths(content: str) -> tuple[str, list[tuple[Path, str]]]:
    """content 내 ``/uploads/meetings/…`` 또는 ``/uploads/teams/…`` URL을
    ``attachments/{basename}`` 로 치환.

    ZIP 내 .md 파일과 attachments/ 폴더는 같은 레벨에 위치하므로 ../ 불필요.
    Returns ``(rewritten_content, [(disk_path, zip_archive_path), ...])``

    P1-1 catchup: meetings 외에 teams/{id}/{subdir} 경로도 매칭한다.
    """
    collected: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def _repl(m: re.Match) -> str:
        url = m.group(0)
        basename = url.rsplit("/", 1)[-1]
        if basename not in seen:
            seen.add(basename)
            disk = _img_url_to_disk(url)
            if disk is not None:
                collected.append((disk, f"attachments/{basename}"))
        return f"attachments/{basename}"

    return _IMG_URL_RE.sub(_repl, content), collected


def _build_checklist_md(project_name: str | None, cl: dict, exported_at: str,
                        images: list | None = None, include_backlink: bool = True) -> str:
    title = _esc(cl.get("title")) or "(제목 없음)"
    updated = _esc(cl.get("updated_at"))
    lines = ["---", "type: checklist"]
    if project_name is not None:
        lines.append(f"project: {_yaml_str(project_name)}")
    lines.append(f"title: {_yaml_str(title)}")
    if updated:
        lines.append(f"updated_at: {updated[:19].replace('T', ' ')}")
    lines += [f"exported_at: {exported_at}", "tags: [whatudoin, checklist]", "---", "",
              f"# {title}", ""]
    if include_backlink:
        lines += [f"← [[index]]", ""]
    raw = (cl.get("content") or "").replace("\r\n", "\n").strip()
    if raw:
        rewritten, found = _rewrite_image_paths(_normalize_markdown_for_export(raw))
        if images is not None:
            images.extend(found)
        lines.append(rewritten)
    else:
        lines.append("_(내용 없음)_")
    # 첨부파일 목록 추가
    attachments = cl.get("attachments") or []
    if isinstance(attachments, str):
        import json as _json
        try:
            attachments = _json.loads(attachments)
        except Exception:
            attachments = []
    if attachments:
        lines += ["", "## 첨부파일", ""]
        for att in attachments:
            url  = att.get("url", "")
            name = att.get("name", url.rsplit("/", 1)[-1])
            basename = url.rsplit("/", 1)[-1]
            lines.append(f"- [{name}](attachments/{basename})")
            if images is not None and url:
                # P1-1 catchup: meetings + teams 양쪽 모두 지원.
                disk = _img_url_to_disk(url)
                if disk is not None:
                    images.append((disk, f"attachments/{basename}"))
    lines.append("")
    return "\n".join(lines)


def _uniq_filename(stem: str, used: set[str]) -> str:
    base = stem
    n = 2
    while stem in used:
        stem = f"{base}_{n}"
        n += 1
    used.add(stem)
    return stem


def _build_single_export(stem: str, md_text: str,
                         images: list[tuple[Path, str]]) -> tuple[bytes, str, str]:
    """이미지 없으면 (.md 바이트, 'text/markdown; charset=utf-8', f'{stem}.md'),
       이미지 있으면 ZIP, 내부: {stem}/{stem}.md + {stem}/attachments/{basename}"""
    if not images:
        return md_text.encode("utf-8"), "text/markdown; charset=utf-8", f"{stem}.md"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{stem}/{stem}.md", md_text)
        seen_archive: set[str] = set()
        for disk_path, archive_name in images:
            if archive_name in seen_archive:
                continue
            seen_archive.add(archive_name)
            if disk_path.exists():
                zf.write(disk_path, f"{stem}/{archive_name}")
    return buf.getvalue(), "application/zip", f"{stem}.zip"


def _attachment_response(body: bytes, media_type: str, filename: str) -> Response:
    disposition = (
        f"attachment; filename=\"{quote(filename)}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(content=body, media_type=media_type,
                    headers={"Content-Disposition": disposition})


def _build_project_zip(name: str) -> bytes:
    is_unset = (name == "미지정")
    if is_unset:
        proj = None
        all_events = db.get_unassigned_events()
        cl_metas = db.get_unassigned_checklists()
    else:
        proj = db.get_project(name)
        all_events = db.get_events_by_project(name)
        cl_metas = db.get_checklists(project=name)
    checklists = [db.get_checklist(c["id"]) for c in cl_metas]
    checklists = [c for c in checklists if c]

    schedules = [e for e in all_events if e.get("event_type") != "meeting"]
    meetings  = [e for e in all_events if e.get("event_type") == "meeting"]

    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    root = _safe_filename(name, "project")

    event_entries = []
    used_ev_stems: set[str] = set()
    for ev in schedules:
        stem = _uniq_filename(_safe_filename(ev.get("title") or "일정", "일정"), used_ev_stems)
        event_entries.append((stem, ev))

    meeting_entries = []
    used_mt_stems: set[str] = set()
    for ev in meetings:
        stem = _uniq_filename(_safe_filename(ev.get("title") or "회의", "회의"), used_mt_stems)
        meeting_entries.append((stem, ev))

    cl_entries = []
    used_cl_stems: set[str] = set()
    for cl in checklists:
        stem = _uniq_filename(_safe_filename(cl.get("title") or "체크리스트", "체크리스트"), used_cl_stems)
        cl_entries.append((stem, cl))

    proj_arg = None if is_unset else name
    images: list[tuple[Path, str]] = []
    cl_mds = [(stem, _build_checklist_md(proj_arg, cl, exported_at, images)) for stem, cl in cl_entries]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        index_md = _build_index_md(
            name, proj, schedules, checklists,
            [s for s, _ in event_entries],
            [s for s, _ in cl_entries],
            exported_at,
            meetings=meetings,
            meeting_files=[s for s, _ in meeting_entries],
        )
        zf.writestr(f"{root}/index.md", index_md)
        for stem, ev in event_entries:
            zf.writestr(f"{root}/일정/{stem}.md", _build_event_md(proj_arg, ev, exported_at))
        for stem, ev in meeting_entries:
            zf.writestr(f"{root}/회의/{stem}.md", _build_event_md(proj_arg, ev, exported_at))
        for stem, md_text in cl_mds:
            zf.writestr(f"{root}/체크리스트/{stem}.md", md_text)
        seen_archive: set[str] = set()
        for disk_path, archive_name in images:
            if archive_name in seen_archive:
                continue
            seen_archive.add(archive_name)
            if disk_path.exists():
                zf.write(disk_path, f"{root}/{archive_name}")
    return buf.getvalue()


@app.get("/api/manage/projects/{name:path}/export.zip")
async def manage_export_project_zip(name: str, request: Request):
    user = _require_editor(request)
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if not auth.can_edit_project(user, proj):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    data = _build_project_zip(name)
    safe = _safe_filename(name, "project")
    stamp = datetime.now().strftime("%Y%m%d")
    filename = f"{safe}_{stamp}.zip"
    return _attachment_response(data, "application/zip", filename)


@app.get("/api/doc/{meeting_id}/export")
def export_doc(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    doc = db.get_meeting(meeting_id)
    if not _can_read_doc(user, doc):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    images: list[tuple[Path, str]] = []
    md_text = _build_doc_md(doc, exported_at, images=images, include_backlink=False)
    stem = _safe_filename(doc.get("title") or "doc", "doc")
    stamp = datetime.now().strftime("%Y%m%d")
    body, media_type, base_name = _build_single_export(stem, md_text, images)
    name_root, _, ext = base_name.rpartition(".")
    filename = f"{name_root}_{stamp}.{ext}"
    return _attachment_response(body, media_type, filename)


@app.get("/api/checklists/{checklist_id}/export")
def export_checklist(checklist_id: int, request: Request):
    user = auth.get_current_user(request)
    cl = db.get_checklist(checklist_id)
    if not cl or not _can_read_checklist(user, cl):
        raise HTTPException(status_code=404, detail="체크리스트를 찾을 수 없습니다.")
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    project_name = cl.get("project") or None
    images: list[tuple[Path, str]] = []
    md_text = _build_checklist_md(project_name, cl, exported_at, images=images, include_backlink=False)
    stem = _safe_filename(cl.get("title") or "체크리스트", "체크리스트")
    stamp = datetime.now().strftime("%Y%m%d")
    body, media_type, base_name = _build_single_export(stem, md_text, images)
    name_root, _, ext = base_name.rpartition(".")
    filename = f"{name_root}_{stamp}.{ext}"
    return _attachment_response(body, media_type, filename)


@app.get("/api/events/{event_id}/export")
def export_event(event_id: int, request: Request):
    user = _require_editor(request)
    ev = db.get_event(event_id)
    if not ev or ev.get("deleted_at"):
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
    if not auth.can_edit_event(user, ev):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    project_name = ev.get("project") or None
    md_text = _build_event_md(project_name, ev, exported_at, include_backlink=False)
    stem = _safe_filename(ev.get("title") or "일정", "일정")
    stamp = datetime.now().strftime("%Y%m%d")
    filename = f"{stem}_{stamp}.md"
    return _attachment_response(md_text.encode("utf-8"), "text/markdown; charset=utf-8", filename)


@app.post("/api/manage/projects/{name:path}/events")
async def manage_add_event(name: str, request: Request):
    user = _require_editor(request)
    if name != "미지정":
        project = db.get_project(name)
        if not project:
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
        if project.get("is_private") and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="비공개 프로젝트에 접근 권한이 없습니다.")
        _assert_can_assign_to_project(user, name)   # 히든 프로젝트 멤버십 검사
    data = await request.json()
    _assert_assignees_in_hidden_project(name if name != "미지정" else None, data.get("assignee"))
    title = data.get("title", "").strip()
    start = data.get("start_datetime", "").strip()
    if not title or not start:
        raise HTTPException(status_code=400, detail="제목과 시작일을 입력하세요.")
    payload = {
        "title":          title,
        "project":        name if name != "미지정" else None,
        "start_datetime": start,
        "end_datetime":   data.get("end_datetime") or None,
        "assignee":       data.get("assignee") or None,
        "description":    "",
        "location":       "",
        "all_day":        1,
        "source":         "manual",
        "meeting_id":     None,
        "kanban_status":  None,
        "priority":       "normal",
        "created_by":     str(user["id"]),
        # 팀 기능 그룹 C #16: 신규 일정 team_id 명시 보장.
        "team_id":        auth.require_admin_work_team(request, user),
    }
    event_id = db.create_event(payload)
    _sse_publish("events.changed", {"id": event_id, "action": "create", "team_id": payload["team_id"]})
    return {"id": event_id}


@app.put("/api/manage/events/{event_id}")
async def manage_update_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    data = await request.json()
    # 수정 가능한 필드만 반영
    updated = {**event}
    for field in ("title", "project", "description", "location", "assignee",
                  "all_day", "start_datetime", "end_datetime", "kanban_status", "priority"):
        if field in data:
            updated[field] = data[field]
    # 프로젝트에서 미지정으로 이동 + is_public=NULL(연동) → 프로젝트 설정에 따라 확정
    old_proj = (event.get("project") or "").strip()
    new_proj = (updated.get("project") or "")
    new_proj = "" if new_proj is None else str(new_proj).strip()
    if old_proj and not new_proj and event.get("is_public") is None:
        proj = db.get_project(old_proj)
        updated["is_public"] = 0 if (proj and proj.get("is_private")) else 1
    # 새 프로젝트가 히든이면 멤버십 검사
    _mgr_new_proj = (updated.get("project") or "").strip() or None
    _mgr_old_proj = (event.get("project") or "").strip() or None
    if _mgr_new_proj != _mgr_old_proj:
        _assert_can_assign_to_project(user, _mgr_new_proj)
    _assert_assignees_in_hidden_project(_mgr_new_proj or _mgr_old_proj, updated.get("assignee"))
    db.update_event(event_id, updated)
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


@app.patch("/api/manage/events/{event_id}/status")
async def manage_event_status(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.update_event_active_status(event_id, is_active)
    if is_active == 0 and event.get("is_active") != 0 and event.get("event_type") == "schedule":
        db.complete_subtasks(event_id)
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}



@app.patch("/api/events/{event_id}/visibility")
async def update_event_visibility_api(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    body = await request.body()
    data = await request.json() if body else {}
    # is_public: null=프로젝트 연동, 0=비공개, 1=공개
    if "is_public" in data:
        raw = data["is_public"]
        is_public = None if raw is None else (1 if raw else 0)
    else:
        # 값 없으면 서버에서 cycling: None→1→0→None
        cur = event.get("is_public")
        is_public = 1 if cur is None else (0 if cur == 1 else None)
    # 히든 프로젝트 항목은 외부 공개 불가
    if is_public:
        ev_proj = event.get("project")
        if ev_proj:
            _proj = db.get_project_by_name(ev_proj)
            if _proj and _proj.get("is_hidden"):
                raise HTTPException(status_code=403, detail="히든 프로젝트 항목은 외부 공개할 수 없습니다.")
    db.update_event_visibility(event_id, is_public)
    _sse_publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True, "is_public": is_public}


@app.delete("/api/manage/events/{event_id}")
def manage_delete_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    db.delete_event(event_id, deleted_by=user["name"], team_id=user.get("team_id"))
    # sync 핸들러 → broker 내부의 call_soon_threadsafe가 루프 스레드로 안전 전달
    _sse_publish("events.changed", {"id": event_id, "action": "delete", "team_id": user.get("team_id")})
    return {"ok": True}


@app.get("/api/members")
def list_members():
    users = db.get_all_users()
    return [u["name"] for u in users if u.get("is_active") and u.get("role") != "admin"]


# ── 링크 API ─────────────────────────────────────────────

@app.get("/api/links")
def api_get_links(request: Request, team_id: int = None):
    # 팀 기능 그룹 B #15-2: scope='personal'은 작성자 본인 / scope='team'은 작업 팀 기준.
    user = auth.get_current_user(request)
    if not user:
        return []
    scope_team_ids = _work_scope(request, user, team_id)  # admin→None / 비admin→{tid} 또는 set()
    return db.get_links(user["name"], scope_team_ids)


@app.post("/api/links")
async def api_create_link(request: Request):
    user = _require_editor(request)
    data = await request.json()
    title = (data.get("title") or "").strip()
    url   = (data.get("url")   or "").strip()
    desc  = (data.get("description") or "").strip()
    scope = data.get("scope", "personal")
    if not title or not url:
        raise HTTPException(status_code=400, detail="title과 url은 필수입니다.")
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https", "mailto", ""):
        raise HTTPException(status_code=400, detail="허용되지 않는 URL 형식입니다.")
    if scope not in ("personal", "team"):
        scope = "personal"
    # 팀 기능 그룹 B #15-2 + C #16: scope='team' 이면 작업 팀 명시 보장, personal 은 NULL 유지.
    if scope == "team":
        team_id = auth.require_admin_work_team(request, user, explicit_id=data.get("team_id"))
    else:
        team_id = None
    link_id = db.create_link(title, url, desc, scope, team_id, user["name"])
    return {"id": link_id}


@app.put("/api/links/{link_id}")
async def api_update_link(link_id: int, request: Request):
    user = _require_editor(request)
    data = await request.json()
    title = (data.get("title") or "").strip()
    url   = (data.get("url")   or "").strip()
    desc  = (data.get("description") or "").strip()
    if not title or not url:
        raise HTTPException(status_code=400, detail="title과 url은 필수입니다.")
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https", "mailto", ""):
        raise HTTPException(status_code=400, detail="허용되지 않는 URL 형식입니다.")
    # 팀 기능 그룹 B #15-2: 작성자 본인 + admin 만 편집 (계획서 §8-1 — 일정·체크의 팀 공유 모델과 다른 예외).
    ok = db.update_link(link_id, title, url, desc, user["name"], user.get("role", "member"))
    if not ok:
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    return {"ok": True}


@app.delete("/api/links/{link_id}")
def api_delete_link(link_id: int, request: Request):
    user = _require_editor(request)
    # 팀 기능 그룹 B #15-2: 작성자 본인 + admin 만 삭제 (계획서 §8-1).
    ok = db.delete_link(link_id, user["name"], user.get("role", "member"))
    if not ok:
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
    return {"ok": True}


# ── 문서 API ─────────────────────────────────────────────

@app.get("/api/doc")
def list_docs(request: Request, team_id: int = None):
    user = auth.get_current_user(request)
    return db.get_all_meetings(viewer=user, work_team_ids=(_work_scope(request, user, team_id) if user else None))


@app.post("/api/doc")
async def create_doc(request: Request):
    user = _require_editor(request)
    data = await request.json()
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    meeting_date = data.get("meeting_date") or None
    is_team_doc  = 1 if data.get("is_team_doc", True) else 0
    is_public    = 1 if data.get("is_public", False) else 0
    team_share   = 1 if data.get("team_share", False) else 0
    # 팀 기능 #12: 미배정 사용자의 신규 문서는 항상 개인 문서·team_id NULL·team_share 무의미
    unassigned = auth.is_unassigned(user)
    if unassigned:
        is_team_doc, team_share = 0, 0
    raw_att = data.get("attachments")
    if isinstance(raw_att, str):
        try:
            attachments = json.loads(raw_att)
        except Exception:
            attachments = None
    elif isinstance(raw_att, list):
        attachments = raw_att
    else:
        attachments = None
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    # 팀 기능 그룹 C #16: 미배정 사용자의 개인 문서(is_team_doc=0)는 team_id NULL 허용 — #12 예외.
    # 그 외(팀 소속 + 팀/개인 문서, admin)는 작업 팀 명시 강제.
    if unassigned:
        doc_team_id = None
    else:
        doc_team_id = auth.require_admin_work_team(request, user)
    meeting_id = db.create_meeting(
        title, content, doc_team_id, user["id"],
        meeting_date, is_team_doc, is_public, team_share, attachments
    )
    _sse_publish("docs.changed", {"action": "create", "id": meeting_id})
    return {"id": meeting_id}


@app.put("/api/doc/{meeting_id}")
async def update_doc(meeting_id: int, request: Request):
    user = _require_editor(request)
    doc = db.get_meeting(meeting_id)
    if not doc:
        raise HTTPException(status_code=404)
    if not _can_write_doc(user, doc):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    data = await request.json()
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    meeting_date = data.get("meeting_date") or None
    is_team_doc  = 1 if data.get("is_team_doc", True) else 0
    is_public    = 1 if data.get("is_public", False) else 0
    team_share   = 1 if data.get("team_share", False) else 0
    # 팀 기능 #12: 미배정 사용자가 편집할 수 있는 건 본인 작성 개인 문서뿐 — 항상 개인 문서·team_share 무의미
    if auth.is_unassigned(user):
        is_team_doc, team_share = 0, 0
    raw_att = data.get("attachments")
    if isinstance(raw_att, str):
        try:
            attachments = json.loads(raw_att)
        except Exception:
            attachments = None
    elif isinstance(raw_att, list):
        attachments = raw_att
    else:
        attachments = None
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    old_title = (doc.get("title") or "").strip()
    db.update_meeting(meeting_id, title, content, user["id"], meeting_date, is_team_doc, is_public, team_share, attachments)
    if title != old_title:
        _sse_publish("docs.changed", {"action": "update", "id": meeting_id})
    # 저장 완료 시 잠금 해제 (tab_token 없으면 강제 해제)
    tab_token = data.get("tab_token") or None
    db.release_meeting_lock(meeting_id, tab_token)
    return {"ok": True}


@app.patch("/api/doc/{meeting_id}/visibility")
async def rotate_doc_visibility(meeting_id: int, request: Request):
    user = _require_editor(request)
    doc = db.get_meeting(meeting_id)
    if not doc:
        raise HTTPException(status_code=404)
    if not _can_write_doc(user, doc):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    is_team = int(doc.get("is_team_doc") or 0)
    is_pub  = int(doc.get("is_public")   or 0)
    t_share = int(doc.get("team_share")  or 0)
    if is_team:
        new_pub, new_share = (0 if is_pub else 1), 0
    elif auth.is_unassigned(user):
        # 팀 기능 #12: 미배정 사용자에겐 team_share 단계가 의미 없음 — is_public 만 토글
        new_pub, new_share = (0 if is_pub else 1), 0
    else:
        if   (is_pub, t_share) == (0, 0): new_pub, new_share = 0, 1
        elif (is_pub, t_share) == (0, 1): new_pub, new_share = 1, 0
        else:                              new_pub, new_share = 0, 0
    db.update_meeting_visibility(meeting_id, is_team, new_pub, new_share)
    _sse_publish("docs.changed", {"action": "update", "id": meeting_id})
    return {"ok": True, "is_team_doc": is_team, "is_public": new_pub, "team_share": new_share}


# ── 문서 편집 잠금 ────────────────────────────────────────
@app.post("/api/doc/{meeting_id}/lock")
def lock_doc(meeting_id: int, request: Request):
    user = _require_editor(request)
    doc = db.get_meeting(meeting_id)
    if not doc:
        raise HTTPException(status_code=404)
    if not _can_write_doc(user, doc):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    tab_token = request.query_params.get("tab_token", "")
    ok = db.acquire_meeting_lock(meeting_id, user["name"], tab_token)
    if not ok:
        lock = db.get_meeting_lock(meeting_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    return {"ok": True}


@app.put("/api/doc/{meeting_id}/lock")
def heartbeat_doc_lock(meeting_id: int, request: Request):
    user = _require_editor(request)
    doc = db.get_meeting(meeting_id)
    if not doc:
        raise HTTPException(status_code=404)
    if not _can_write_doc(user, doc):
        raise HTTPException(status_code=403, detail="Access denied.")
    tab_token = request.query_params.get("tab_token", "")
    ok = db.heartbeat_meeting_lock(meeting_id, tab_token)
    if not ok:
        lock = db.get_meeting_lock(meeting_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    return {"ok": True}


@app.delete("/api/doc/{meeting_id}/lock")
def unlock_doc(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    if user:
        doc = db.get_meeting(meeting_id)
        if not doc:
            raise HTTPException(status_code=404)
        if not _can_write_doc(user, doc):
            raise HTTPException(status_code=403, detail="Access denied.")
        tab_token = request.query_params.get("tab_token") or None
        if tab_token:  # tab_token 없으면 no-op — 다른 편집자 잠금 보호
            db.release_meeting_lock(meeting_id, tab_token)
    return {"ok": True}


@app.get("/api/doc/{meeting_id}/lock")
def get_doc_lock(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    doc = db.get_meeting(meeting_id)
    if not doc or not _can_read_doc(user, doc):
        raise HTTPException(status_code=404)
    lock = db.get_meeting_lock(meeting_id)
    if not lock:
        return {"locked_by": None, "lock_type": None}
    lock_type = "self_tab" if (user and lock["user_name"] == user["name"]) else "other_user"
    return {"locked_by": lock["user_name"], "lock_type": lock_type}


@app.get("/api/doc/calendar")
def docs_calendar(request: Request, team_id: int = None):
    user = auth.get_current_user(request)
    if user is None:
        return []
    docs = db.get_all_meetings(viewer=user, work_team_ids=_work_scope(request, user, team_id))
    result = []
    for m in docs:
        is_team_doc = bool(m.get("is_team_doc", 1))
        is_team_share = bool(m.get("team_share", 0))
        if not (is_team_doc or is_team_share):  # 개인(비공유) 문서는 캘린더 미노출
            continue
        date = m.get("meeting_date") or m["created_at"][:10]
        result.append({
            "id": f"meeting-{m['id']}",
            "title": f"📄 {m['title']}",
            "start": date,
            "allDay": True,
            "extendedProps": {"type": "meeting", "meeting_id": m["id"]},
        })
    return result


def _call_media_service(
    *,
    kind: str,
    staging_path: Path,
    original_name: str,
    max_bytes: int,
) -> dict:
    """Media service IPC 호출 헬퍼 (M5-1).

    POST _MEDIA_SERVICE_URL JSON {kind, staging_path, original_name, max_bytes}
    + Authorization Bearer 토큰.

    반환: Media service 응답 dict.
    - 2xx 성공 응답: JSON 파싱 후 반환.
    - 4xx 업무 오류 응답 (invalid_image, too_large 등): HTTPError.read()로 body 파싱 후 반환.
      urllib.urlopen은 4xx도 HTTPError를 raise하므로 별도 처리 필요.
    - 연결 실패/timeout/URLError → RuntimeError("unavailable") raise.
    stdlib urllib.request만 사용 — 외부 의존성 추가 0.
    """
    import urllib.error as _ue
    import urllib.request as _ur
    import json as _json

    token = os.environ.get("WHATUDOIN_INTERNAL_TOKEN", "").strip()
    payload = _json.dumps({
        "kind": kind,
        "staging_path": str(staging_path),
        "original_name": original_name,
        "max_bytes": max_bytes,
    }).encode("utf-8")
    req = _ur.Request(
        _MEDIA_SERVICE_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with _ur.urlopen(req, timeout=30) as resp:
            body = resp.read(4 * 1024 * 1024)
            return _json.loads(body)
    except _ue.HTTPError as exc:
        # 4xx 응답: media service가 업무 오류를 JSON으로 반환 (ok:false + reason).
        # urlopen은 4xx도 HTTPError로 raise하므로 body를 읽어서 반환.
        try:
            body = exc.read(4 * 1024 * 1024)
            return _json.loads(body)
        except Exception:
            # body 파싱 불가 시 (예: 401/403 인증 오류) → 연결 불가로 처리
            raise RuntimeError("unavailable") from exc
    except Exception as exc:
        raise RuntimeError("unavailable") from exc


def _resolve_upload_target(
    request: Request,
    user: dict,
    kind: str,
    subdir: str,
) -> tuple[Path, str]:
    """팀 기능 그룹 D #24: 업로드 대상 (디스크 폴더, URL prefix) 결정.

    kind == "meeting" (기본): 기존 ``meetings/{YYYY}/{MM}/...`` 경로.
    kind == "team": ``uploads/teams/{team_id}/{subdir}/{YYYY}/{MM}/...``.
                    team_id 는 ``resolve_work_team`` 으로 결정 — None 이면 400.
    """
    if kind == "meeting":
        now = datetime.now()
        folder = MEETINGS_DIR / str(now.year) / f"{now.month:02d}"
        url_prefix = f"/uploads/meetings/{now.year}/{now.month:02d}"
        return folder, url_prefix
    if kind != "team":
        raise HTTPException(
            status_code=400,
            detail="kind 는 'meeting' 또는 'team' 이어야 합니다.",
        )
    team_id = auth.resolve_work_team(request, user, None)
    if not team_id:
        raise HTTPException(
            status_code=400,
            detail="작업 팀이 필요합니다. 먼저 팀을 선택하세요.",
        )
    now = datetime.now()
    folder = TEAMS_UPLOAD_DIR / str(team_id) / subdir / str(now.year) / f"{now.month:02d}"
    url_prefix = f"/uploads/teams/{team_id}/{subdir}/{now.year}/{now.month:02d}"
    return folder, url_prefix


_UPLOAD_SUBDIRS = {"checks", "notices"}


@app.post("/api/upload/image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    kind: str = "meeting",
    subdir: str = "checks",
):
    """회의록·체크·공지 이미지 업로드.

    kind=meeting (기본): ``meetings/{YYYY}/{MM}/...``  (기존 동작 100% 보존)
    kind=team: ``uploads/teams/{team_id}/{subdir}/{YYYY}/{MM}/...``
        subdir: ``checks`` (기본) | ``notices``  (P1-2 catchup)
    """
    user = _require_editor(request)
    if subdir not in _UPLOAD_SUBDIRS:
        raise HTTPException(status_code=400, detail="허용되지 않은 subdir 입니다.")
    folder, url_prefix = _resolve_upload_target(request, user, kind, subdir=subdir)

    if not _MEDIA_SERVICE_URL:
        # ── in-process fallback (기존 동작 100% 보존) ──────────────────────
        from PIL import Image as _PilImage
        data = await file.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="파일 크기는 10MB 이하여야 합니다.")
        try:
            img = _PilImage.open(io.BytesIO(data))
            img.verify()
        except Exception:
            raise HTTPException(status_code=400, detail="유효하지 않은 이미지 파일입니다.")
        ext = Path(file.filename).suffix.lower() if file.filename else ".png"
        if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            ext = ".png"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        (folder / filename).write_bytes(data)
        return {"url": f"{url_prefix}/{filename}"}

    # ── Media service IPC 경로 ────────────────────────────────────────────────
    # ext 정규화: 알 수 없는 ext는 .png로 fallback (기존 in-process 정책 보존)
    raw_ext = Path(file.filename).suffix.lower() if file.filename else ".png"
    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    normalized_ext = raw_ext if raw_ext in _IMAGE_EXTS else ".png"
    # original_name ext를 정규화한 이름으로 Media service에 전달
    original_name = (Path(file.filename).stem + normalized_ext) if file.filename else f"image{normalized_ext}"

    staging_file: Path | None = STAGING_ROOT / f"{uuid.uuid4().hex}.tmp"
    try:
        # stream → staging 파일 저장 (큰 파일 메모리 피크 회피)
        written = 0
        max_bytes = 10 * 1024 * 1024
        with open(staging_file, "wb") as f:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=400, detail="파일 크기는 10MB 이하여야 합니다.")
                f.write(chunk)

        try:
            result = await run_in_threadpool(
                _call_media_service,
                kind="image",
                staging_path=staging_file,
                original_name=original_name,
                max_bytes=max_bytes,
            )
        except RuntimeError:
            raise HTTPException(status_code=500, detail="이미지 처리 서비스 일시 사용 불가")

        if not result.get("ok"):
            reason = result.get("reason", "")
            if reason == "too_large":
                raise HTTPException(status_code=413, detail="파일 크기는 10MB 이하여야 합니다.")
            elif reason == "invalid_image":
                raise HTTPException(status_code=400, detail="유효하지 않은 이미지 파일입니다.")
            elif reason == "forbidden_ext":
                raise HTTPException(status_code=415, detail="허용되지 않는 이미지 형식입니다.")
            else:
                raise HTTPException(status_code=400, detail="이미지 처리 실패")

        # Web API가 staging → 대상 폴더 이동 (owner). folder/url_prefix 는 라우트 진입 시 결정.
        ext = result.get("ext", normalized_ext) or normalized_ext
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        staging_file.rename(folder / filename)
        staging_file = None  # renamed — cleanup 불필요

        return {"url": f"{url_prefix}/{filename}"}
    finally:
        if staging_file is not None and staging_file.exists():
            staging_file.unlink(missing_ok=True)


@app.post("/api/upload/attachment")
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    kind: str = "meeting",
    subdir: str = "checks",
):
    """문서·체크·공지 첨부파일 업로드.

    kind=meeting (기본): ``meetings/{YYYY}/{MM}/...``  (기존 동작 100% 보존)
    kind=team: ``uploads/teams/{team_id}/{subdir}/{YYYY}/{MM}/...``
        subdir: ``checks`` (기본) | ``notices``  (P1-2 catchup)
    """
    user = _require_editor(request)
    if subdir not in _UPLOAD_SUBDIRS:
        raise HTTPException(status_code=400, detail="허용되지 않은 subdir 입니다.")
    _ALLOWED_EXTS = {".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".zip", ".7z"}
    folder, url_prefix = _resolve_upload_target(request, user, kind, subdir=subdir)

    if not _MEDIA_SERVICE_URL:
        # ── in-process fallback (기존 동작 100% 보존) ──────────────────────
        ext = Path(file.filename).suffix.lower() if file.filename else ""
        if ext not in _ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail="허용되지 않는 파일 형식입니다.")
        data = await file.read()
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="파일 크기는 20MB 이하여야 합니다.")
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        (folder / filename).write_bytes(data)
        original_name = file.filename or filename
        url = f"{url_prefix}/{filename}"
        uploaded_at = now_for_filename = datetime.now().strftime("%y%m%d_%H%M")
        return {"name": original_name, "url": url, "size": len(data), "uploaded_at": uploaded_at}

    # ── Media service IPC 경로 ────────────────────────────────────────────────
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="허용되지 않는 파일 형식입니다.")

    original_name = file.filename or f"attachment{ext}"

    staging_file: Path | None = STAGING_ROOT / f"{uuid.uuid4().hex}.tmp"
    try:
        # stream → staging 파일 저장 (큰 파일 메모리 피크 회피)
        written = 0
        max_bytes = 20 * 1024 * 1024
        with open(staging_file, "wb") as f:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=400, detail="파일 크기는 20MB 이하여야 합니다.")
                f.write(chunk)

        try:
            result = await run_in_threadpool(
                _call_media_service,
                kind="attachment",
                staging_path=staging_file,
                original_name=original_name,
                max_bytes=max_bytes,
            )
        except RuntimeError:
            raise HTTPException(status_code=500, detail="파일 처리 서비스 일시 사용 불가")

        if not result.get("ok"):
            reason = result.get("reason", "")
            if reason == "too_large":
                raise HTTPException(status_code=413, detail="파일 크기는 20MB 이하여야 합니다.")
            elif reason == "forbidden_ext":
                raise HTTPException(status_code=415, detail="허용되지 않는 파일 형식입니다.")
            else:
                raise HTTPException(status_code=400, detail="파일 처리 실패")

        # Web API가 staging → 대상 폴더 이동 (owner). folder/url_prefix 는 라우트 진입 시 결정.
        ext = result.get("ext", ext) or ext
        file_size = result.get("size", written)
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        staging_file.rename(folder / filename)
        staging_file = None  # renamed — cleanup 불필요

        url = f"{url_prefix}/{filename}"
        uploaded_at = datetime.now().strftime("%y%m%d_%H%M")
        return {"name": original_name, "url": url, "size": file_size, "uploaded_at": uploaded_at}
    finally:
        if staging_file is not None and staging_file.exists():
            staging_file.unlink(missing_ok=True)


def _delete_meeting_images(content: str):
    """마크다운 content에서 업로드 이미지 URL을 찾아 디스크 파일 삭제.

    P1-1 catchup: ``/uploads/meetings/…`` 외에 ``/uploads/teams/{id}/{sub}/…``
    경로도 인식 → 그룹 D #24 이후 작성된 팀 첨부도 정상 삭제된다.
    함수명은 호환성 위해 유지 (호출부 다수).
    """
    for url in _IMG_URL_RE.findall(content or ""):
        disk = _img_url_to_disk(url)
        if disk is None or not disk.exists():
            continue
        try:
            disk.unlink()
        except OSError:
            pass


@app.delete("/api/doc/{meeting_id}")
def delete_doc(meeting_id: int, request: Request):
    user = _require_editor(request)
    doc = db.get_meeting(meeting_id)
    if not doc:
        raise HTTPException(status_code=404)
    if not _can_write_doc(user, doc):
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
    db.delete_meeting(meeting_id, deleted_by=user["name"])
    _sse_publish("docs.changed", {"action": "delete", "id": meeting_id})
    return {"ok": True}


@app.get("/api/doc/{meeting_id}/histories")
def doc_histories(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    doc = db.get_meeting(meeting_id)
    if not _can_read_doc(user, doc):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return db.get_meeting_histories(meeting_id)


@app.post("/api/doc/{meeting_id}/histories/{history_id}/restore")
def restore_doc_history(meeting_id: int, history_id: int, request: Request):
    user = _require_editor(request)
    doc = db.get_meeting(meeting_id)
    if not _can_write_doc(user, doc):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    ok = db.restore_meeting_from_history(meeting_id, history_id, user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="이력을 찾을 수 없습니다.")
    return {"ok": True}


@app.get("/api/doc/{meeting_id}/events")
def doc_events_api(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    doc = db.get_meeting(meeting_id)
    if not _can_read_doc(user, doc):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return _filter_visible_events(db.get_events_by_meeting(meeting_id), user)


# ── AI 파싱 ──────────────────────────────────────────────

@app.post("/api/ai/parse")
async def ai_parse(request: Request):
    _require_editor(request)
    body = await request.json()
    text = body.get("text", "").strip()
    model = body.get("model", llm_parser.DEFAULT_MODEL)
    if not text:
        raise HTTPException(status_code=400, detail="텍스트를 입력하세요.")
    try:
        events = await run_in_threadpool(llm_parser.parse_schedule, text, model)
    except llm_parser.OllamaUnavailableError:
        raise  # exception_handler에서 503으로 변환
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")
    return {"events": events}


@app.post("/api/ai/confirm")
async def ai_confirm(request: Request):
    user = _require_editor(request)
    body = await request.json()
    events     = body.get("events", [])
    meeting_id = body.get("meeting_id")
    force      = bool(body.get("force", False))
    # 팀 기능 그룹 C #16: AI 일괄 확정의 신규 일정 team_id 명시 보장. 한 번만 결정해 모든 row에 적용.
    team_id    = auth.require_admin_work_team(request, user)

    # 저장 직전 재검사 — force=True가 아닌 경우만
    if not force:
        existing = _filter_visible_events(db.get_events_for_conflict_check(team_id), user)
        blocked = []
        for i, e in enumerate(events):
            scored_pairs = sorted(
                [(llm_parser.score_conflict(e, ex), ex) for ex in existing],
                key=lambda x: x[0]["total"], reverse=True,
            )
            if scored_pairs and scored_pairs[0][0]["total"] >= _CONFLICT_EXACT:
                best_score, best_ex = scored_pairs[0]
                blocked.append({
                    "index":          i,
                    "title":          e.get("title", ""),
                    "reason":         "exact",
                    "existing_id":    best_ex["id"],
                    "existing_title": best_ex["title"],
                    "similarity":     best_score["total"],
                })
        if blocked:
            return {"saved": 0, "blocked": blocked, "requires_force": True}

    saved = 0
    skipped = []
    created = []
    for i, e in enumerate(events):
        payload = llm_parser.to_event_payload(e)
        val_errors = _validate_event_payload(payload)
        if val_errors:
            skipped.append({"index": i, "title": e.get("title", ""), "reason": val_errors[0]})
            continue
        # 히든 프로젝트 권한 검사
        _ai_proj_name = (payload.get("project") or "").strip() or None
        _ai_proj = db.get_project_by_name(_ai_proj_name) if _ai_proj_name else None
        if _ai_proj and _ai_proj.get("is_hidden") and not auth.is_admin(user):
            if not db.is_hidden_project_visible(_ai_proj["id"], user):
                skipped.append({"index": i, "title": e.get("title", ""), "reason": "히든 프로젝트에 접근 권한이 없습니다."})
                continue
        # 히든 프로젝트 담당자 검증
        if _ai_proj and _ai_proj.get("is_hidden"):
            _ai_assignee = payload.get("assignee")
            if _ai_assignee:
                _ai_members = {m["name"] for m in db.get_hidden_project_members(_ai_proj["id"])}
                _ai_invalid = [n.strip() for n in _ai_assignee.split(",")
                               if n.strip() and n.strip() not in _ai_members]
                if _ai_invalid:
                    skipped.append({"index": i, "title": e.get("title", ""),
                                    "reason": f"히든 프로젝트 멤버가 아닌 담당자: {', '.join(_ai_invalid)}"})
                    continue
        if payload["start_datetime"]:
            payload["team_id"]    = team_id
            payload["created_by"] = str(user["id"])
            if meeting_id:
                payload["meeting_id"] = meeting_id
            event_id = db.create_event(payload)
            saved += 1
            created.append({"index": i, "id": event_id, "title": payload.get("title", "")})
        else:
            skipped.append({"index": i, "title": e.get("title", ""), "reason": "날짜를 입력해주세요."})
    # N건 일괄 저장 → publish 1회만 (루프 끝 단일 이벤트)
    if saved > 0:
        _sse_publish("events.changed", {"id": None, "action": "bulk_create", "team_id": team_id})
    return {"saved": saved, "created": created, "blocked": [], "skipped": skipped, "requires_force": False}


@app.post("/api/ai/refine")
async def ai_refine(request: Request):
    """2차 AI: 검토자 — 1차 추출 결과를 원본 텍스트와 함께 재검토."""
    _require_editor(request)
    body = await request.json()
    text   = body.get("text", "").strip()
    events = body.get("events", [])
    model  = body.get("model", llm_parser.DEFAULT_MODEL)
    if not text:
        raise HTTPException(status_code=400, detail="텍스트를 입력하세요.")
    try:
        refined = await run_in_threadpool(llm_parser.refine_schedule, text, events, model)
    except llm_parser.OllamaUnavailableError:
        raise  # exception_handler에서 503으로 변환
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")
    return {"events": refined}


@app.get("/api/ai/models")
def ai_models(request: Request):
    _require_editor(request)
    models, ok = llm_parser.get_available_models_with_status()
    if not ok:
        raise HTTPException(status_code=502, detail=f"Ollama 서버({llm_parser.OLLAMA_BASE_URL})에 연결할 수 없습니다.")
    return {"models": models}


@app.post("/api/ai/generate-event-checklist")
async def ai_generate_event_checklist(request: Request):
    user = _require_editor(request)
    body = await request.json()
    raw_event_ids = body.get("event_ids", [])
    try:
        event_ids = [int(eid) for eid in raw_event_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid event_ids.")
    model = body.get("model", llm_parser.DEFAULT_MODEL)
    project = body.get("project", "")

    events = [db.get_event(eid) for eid in event_ids]
    if any(e is None for e in events):
        raise HTTPException(status_code=404, detail="Event not found.")
    events = _filter_events_by_visibility(events, user)
    if len(events) != len(event_ids):
        raise HTTPException(status_code=403, detail="Access denied.")

    if not events:
        from datetime import date as _d
        return {"markdown": f"# {project} 일정 체크\n\n"}

    items = await run_in_threadpool(
        llm_parser.generate_event_checklist_items, events, model
    )

    from datetime import date as _d
    today = _d.today().strftime("%Y-%m-%d")
    lines = [f"# {project} 일정 체크 ({today})", ""]
    for item in items:
        lines.append(f"- [ ] {item['title']} [🔗](eid:{item['event_id']})")
        for sub in item["sub_items"]:
            lines.append(f"  - [ ] {sub}")
        if item["sub_items"]:
            lines.append("")

    return {"markdown": "\n".join(lines)}


@app.post("/api/ai/generate-checklist")
async def ai_generate_checklist(request: Request):
    _require_editor(request)
    body = await request.json()
    text  = (body.get("text") or "").strip()
    model = body.get("model", llm_parser.DEFAULT_MODEL)
    if not text:
        raise HTTPException(status_code=400, detail="요청 내용을 입력하세요.")
    try:
        md = await run_in_threadpool(llm_parser.generate_checklist, text, model)
    except llm_parser.OllamaUnavailableError:
        raise  # exception_handler에서 503으로 변환
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")
    return {"markdown": md}


@app.post("/api/ai/weekly-report")
async def ai_weekly_report(request: Request):
    user = _require_editor(request)
    from datetime import date as _date, datetime as _dt, timedelta as _td
    body = await request.json()
    base_date = (body.get("base_date") or _date.today().isoformat()).strip()
    model     = body.get("model", llm_parser.DEFAULT_MODEL)

    # 팀 기능 그룹 A #10: 주간 리포트는 현재 작업 팀 기준. team_id 미지정 시 resolve_work_team fallback.
    requested_team_id = body.get("team_id") or None
    if auth.is_admin(user):
        team_id = requested_team_id
        scope = None
    else:
        scope = _work_scope(request, user, requested_team_id)
        team_id = next(iter(scope)) if scope else None

    base_dt      = _dt.strptime(base_date, "%Y-%m-%d")
    past_start   = (base_dt - _td(days=7)).strftime("%Y-%m-%d")
    past_end     = base_date
    future_start = (base_dt + _td(days=1)).strftime("%Y-%m-%d")
    future_end   = (base_dt + _td(days=6)).strftime("%Y-%m-%d")

    # 겹침 쿼리로 변경됐으므로 7일 이전 시작 이벤트도 포함됨
    past_events   = _filter_events_by_visibility(
        db.get_events_by_date_range(past_start, past_end, team_id),
        user, scope,
    )
    future_events = _filter_events_by_visibility(
        db.get_events_by_date_range(future_start, future_end, team_id),
        user, scope,
    )

    def _is_today_active(e):
        start = (e.get("start_datetime") or "")[:10]
        end   = (e.get("end_datetime")   or "")[:10]
        return start <= base_date and (not end or end >= base_date)

    def _is_done(e):
        return e.get("is_active") == 0 or (e.get("kanban_status") or "") == "done"

    today_events  = [e for e in past_events if _is_today_active(e)]
    today_ids     = {e.get("id") for e in today_events}
    past_non_today = [e for e in past_events if e.get("id") not in today_ids]
    past_done     = [e for e in past_non_today if _is_done(e)]
    past_pending  = [e for e in past_non_today if not _is_done(e)]
    future_events = [e for e in future_events if e.get("id") not in today_ids]

    meetings   = db.get_meetings_by_date_range(past_start, past_end,
                                                team_id=team_id, created_by=user["id"])
    checklists = [
        cl for cl in db.get_checklists_by_date_range(past_start, past_end)
        if _can_read_checklist(user, cl)
    ]

    prev = db.get_previous_weekly_report(base_date, team_id, user["id"])
    if prev:
        gap = (_dt.strptime(base_date, "%Y-%m-%d") -
               _dt.strptime(prev["meeting_date"], "%Y-%m-%d")).days
        if gap > 14:
            prev = None

    try:
        report = await run_in_threadpool(
            llm_parser.generate_weekly_report,
            past_done, future_events, base_date, model,
            today_events=today_events,
            past_pending=past_pending,
            meetings=meetings,
            checklists=checklists,
            previous_report=prev,
        )
    except llm_parser.OllamaUnavailableError:
        raise  # exception_handler에서 503으로 변환
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")

    title = f"주간 업무 보고 ({base_date})"

    return {
        "title":              title,
        "content":            report,
        "past_count":         len(past_done),
        "past_pending_count": len(past_pending),
        "today_count":        len(today_events),
        "future_count":       len(future_events),
        "meetings_count":     len(meetings),
        "checklists_count":   len(checklists),
        "has_previous":       prev is not None,
        "base_date":          base_date,
    }


# ── 휴지통 ──────────────────────────────────────────────

@app.get("/trash", response_class=HTMLResponse)
def trash_page(request: Request):
    user = _require_editor(request)
    resp = templates.TemplateResponse(request, "trash.html", _ctx(request))
    _ensure_work_team_cookie(request, resp, user)
    return resp


@app.get("/api/trash")
def api_get_trash(request: Request):
    user = _require_editor(request)
    team_id = user.get("team_id")
    return db.get_trash_items(team_id, viewer=user)


def _can_restore_hidden_trash_item(user: dict, project: dict) -> bool:
    if user.get("role") == "admin":
        return True
    if project.get("deleted_at"):
        return project.get("owner_id") == user.get("id")
    return db.is_hidden_project_visible(project["id"], user)


@app.post("/api/trash/{item_type}/{item_id}/restore")
def api_restore_trash(item_type: str, item_id: int, request: Request):
    user = _require_editor(request)
    if item_type not in ("event", "meeting", "checklist", "project"):
        raise HTTPException(status_code=400, detail="잘못된 항목 타입입니다.")
    hidden_project = db.get_trash_item_hidden_project(item_type, item_id)
    if hidden_project:
        if not _can_restore_hidden_trash_item(user, hidden_project):
            raise HTTPException(status_code=403, detail="히든 프로젝트 항목 복원 권한이 없습니다.")
    elif user.get("role") != "admin":
        item_team = db.get_trash_item_team(item_type, item_id)
        if item_team is None or item_team != user.get("team_id"):
            raise HTTPException(status_code=403, detail="권한이 없습니다.")
    ok = db.restore_trash_item(item_type, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    # sync 핸들러 → broker 내부의 call_soon_threadsafe가 루프 스레드로 안전 전달
    # event/project 복원 시에만 관련 채널로 publish
    if item_type == "event":
        _sse_publish("events.changed", {"id": item_id, "action": "update", "team_id": user.get("team_id")})
    elif item_type == "project":
        _sse_publish("projects.changed", {"name": None, "action": "update"})
        _sse_publish("events.changed", {"id": None, "action": "bulk_update", "team_id": user.get("team_id")})
    return {"ok": True}


# ── AVR (WUDeskop 원격 데스크톱 연동) ────────────────────────────────────────


def _is_plain_http_url(url: str) -> bool:
    return urlparse(url).scheme == "http"


def _http_avr_url(request: Request) -> str:
    return f"{_public_base_url(request, 'http')}/avr"


@app.get("/remote")
def remote_redirect():
    return RedirectResponse(url="/avr", status_code=307)


@app.get("/avr", response_class=HTMLResponse)
def avr_page(request: Request):
    user = auth.get_current_user(request)
    if not user or not user.get("avr_enabled") or user.get("login_via") != "ip":
        raise HTTPException(status_code=403, detail="AVR 접근 권한이 없습니다.")
    avr_url_enc = db.get_setting("avr_url_enc")
    avr_secret_enc = db.get_setting("avr_secret_enc")
    if not avr_url_enc or not avr_secret_enc:
        return templates.TemplateResponse(
            request, "avr.html",
            _ctx(request, viewer_url=None, error="AVR 연동이 설정되지 않았습니다. 관리자에게 문의하세요."),
        )
    try:
        wudeskop_url = crypto.decrypt(avr_url_enc).strip().rstrip("/")
        if request.url.scheme == "https" and _is_plain_http_url(wudeskop_url):
            return RedirectResponse(url=_http_avr_url(request), status_code=307)
        wudeskop_secret = crypto.decrypt(avr_secret_enc)
        resp = _requests.post(
            f"{wudeskop_url}/api/issue-token",
            json={"secret": wudeskop_secret},
            timeout=3,
        )
        resp.raise_for_status()
        token = resp.json()["token"]
        viewer_url = f"{wudeskop_url}/viewer?token={quote(str(token), safe='')}"
    except Exception as e:
        return templates.TemplateResponse(
            request, "avr.html",
            _ctx(request, viewer_url=None, error=f"WUDeskop 연결 실패: {e}"),
        )
    parsed = urlparse(wudeskop_url)
    frame_origin = f"{parsed.scheme}://{parsed.netloc}"
    response = templates.TemplateResponse(
        request, "avr.html",
        _ctx(request, viewer_url=viewer_url, error=None),
    )
    # CSP의 frame-src를 WUDeskop 오리진으로 허용 (미들웨어가 기존 헤더는 덮어쓰지 않음)
    response.headers["content-security-policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "font-src 'self' data:; "
        f"frame-src {frame_origin}; "
        "frame-ancestors 'none'"
    )
    return response


@app.get("/api/admin/settings/avr")
def admin_get_avr_settings(request: Request):
    _require_admin(request)
    url_enc = db.get_setting("avr_url_enc")
    secret_enc = db.get_setting("avr_secret_enc")
    url_plain = ""
    if url_enc:
        try:
            url_plain = crypto.decrypt(url_enc)
        except Exception:
            url_plain = ""
    return {
        "url": url_plain,
        "secret_set": bool(secret_enc),
    }


@app.put("/api/admin/settings/avr")
async def admin_put_avr_settings(request: Request):
    _require_admin(request)
    body = await request.json()
    url = body.get("url", "").strip()
    secret = body.get("secret", "").strip()
    if url:
        db.set_setting("avr_url_enc", crypto.encrypt(url))
    else:
        db.delete_setting("avr_url_enc")
    if secret:
        db.set_setting("avr_secret_enc", crypto.encrypt(secret))
    elif "secret" in body:
        db.delete_setting("avr_secret_enc")
    return {"ok": True}


@app.get("/api/admin/users/avr")
def admin_get_avr_users(request: Request):
    _require_admin(request)
    return db.list_users_with_avr()


@app.put("/api/admin/users/{user_id}/avr")
async def admin_put_avr_user(user_id: int, request: Request):
    _require_admin(request)
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    db.set_user_avr_enabled(user_id, enabled)
    return {"ok": True}


# ── MCP 토큰 API ─────────────────────────────────────────

@app.get("/api/me/mcp-token")
def api_get_mcp_token(request: Request):
    """MCP 토큰 메타 조회. 평문 토큰은 절대 반환하지 않는다."""
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return db.get_mcp_token_meta(user["id"])


@app.post("/api/me/mcp-token/regenerate")
async def api_regenerate_mcp_token(request: Request):
    """MCP 토큰 발급/재발급. 평문 토큰을 1회만 반환한다 (이후 재조회 불가).

    팀 기능 그룹 C #22: 시스템 admin 은 MCP 토큰 발급 차단 (계획서 §16-1).
    """
    import sqlite3
    user = _require_editor(request)
    if auth.is_admin(user):
        raise HTTPException(
            status_code=403,
            detail="시스템 관리자는 MCP 토큰을 발급할 수 없습니다.",
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    for _ in range(2):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        try:
            db.set_mcp_token_hash(user["id"], token_hash, now)
            return {"token": token}
        except sqlite3.IntegrityError:
            continue
    raise HTTPException(status_code=500, detail="토큰 생성에 실패했습니다. 다시 시도해 주세요.")


@app.delete("/api/me/mcp-token")
def api_delete_mcp_token(request: Request):
    """MCP 토큰 삭제."""
    user = _require_editor(request)
    db.clear_mcp_token(user["id"])
    return {"ok": True}


# ── /팀이름 비로그인 공개 포털 (팀 기능 그룹 B #13) ──────────────────────
# 이 라우트는 단일 path 세그먼트 catch-all 이므로 반드시 모든 정적 페이지 라우트
# (/, /calendar, /admin, /kanban, ... /trash, /remote, /avr 등)보다 *뒤*에 등록해야 한다.
# FastAPI/Starlette 는 등록 순서대로 매칭하고 첫 매치가 승리한다 — 그래서 app.py 라우트
# 정의 영역 맨 끝(uvicorn 부트스트랩 직전)에 둔다. /docs /redoc /openapi.json 는
# app = FastAPI(...) 시점(맨 위)에 등록되므로 자연히 우선한다.

_TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 예약어 (팀 이름으로 사용 금지) — 계획서 섹션 4. 모두 casefold 비교.
# 하드코딩 목록 + 실제 등록된 라우트의 첫 경로 세그먼트 합집합 (누락 자동 방지).
_RESERVED_TEAM_PATHS_BASE = frozenset({
    "api", "admin", "doc", "check", "kanban", "gantt", "calendar", "mcp", "mcp-codex",
    "uploads", "static", "settings", "changelog", "register", "project-manage",
    "ai-import", "alarm-setup", "notice", "trash", "remote", "avr", "favicon.ico",
    "docs", "redoc", "openapi.json", "healthz",
})


def _build_reserved_team_paths() -> frozenset:
    extra = set()
    for r in app.routes:
        path = getattr(r, "path", "") or ""
        seg = path.strip("/").split("/", 1)[0] if path != "/" else ""
        if seg and "{" not in seg:
            extra.add(seg.casefold())
    return frozenset({s.casefold() for s in _RESERVED_TEAM_PATHS_BASE} | extra)


RESERVED_TEAM_PATHS = _build_reserved_team_paths()


# 그룹 D catchup (비로그인 진입 재설계): /{team_name}/{메뉴키} 4개 + /{team_name}.
# - URL path 세그먼트는 한글 사용 (사용자 결정: A안). _TEAM_NAME_RE 는 segment 1(team_name)만
#   ASCII 로 제약. segment 2(한글 메뉴 키)는 Starlette 가 UTF-8 디코드해 그대로 비교한다.
# - 4개 라우트는 /{team_name} 라우트 *위*에 등록 (FastAPI 첫 매치 승) — 둘 다 정적 라우트 뒤.
# - reserved-set 잠식 검증: _build_reserved_team_paths 는 path 의 segment 1 만 추출하는데
#   "/{team_name}/칸반" 의 segment 1 은 "{team_name}" (중괄호 포함) 이라 자동 skip — 칸반/
#   간트/문서/체크 어느 것도 RESERVED 에 들어가지 않는다. phase99 invariant 로 검증.

# kanban → gantt → doc → check 우선순위 (사용자 결정 — `/팀이름` 진입 시 첫 켜진 메뉴 선택).
_PORTAL_MENU_ORDER = ("kanban", "gantt", "doc", "check")


def _render_team_menu(request: Request, team_name: str, menu_key: str | None):
    """`/팀이름` 및 `/팀이름/{메뉴}` 공통 렌더링.

    v2 (비로그인 진입 재설계 — 사용자 요청 "로그인 화면과 동일 레이아웃"):
      - menu_key 가 4종(kanban/gantt/doc/check) 중 하나 → 각각 정식 템플릿
        (kanban.html / project.html / doc_list.html / check.html) 을 렌더하고
        공개 portal 컨텍스트로 데이터 제한 + 액션 UI 차단 플래그를 전달.
      - menu_key=None (랜딩) 또는 삭제 예정 팀 → v1 그대로 team_portal.html.

    권한 모델:
      - URL 은 권한 경계가 아니다 — 비로그인/admin 모두 동일 공개 포털 (v1 정책 계승).
      - is_public_portal=True 시 템플릿 본문은 user=None 마스킹으로 처리하여
        정식 페이지의 `{% if user %}` 가드를 일괄 활성화 → 액션 UI 자동 차단.
        base.html 헤더는 진짜 user 를 유지하므로 로그인 상태 표시는 그대로 노출.
    """
    if not _TEAM_NAME_RE.match(team_name) or team_name.casefold() in RESERVED_TEAM_PATHS:
        raise HTTPException(status_code=404, detail="Not Found")
    team = db.get_team_by_name_exact(team_name)
    if not team:
        raise HTTPException(status_code=404, detail="Not Found")
    if team.get("deleted_at"):
        # 삭제 예정 팀: menu_key 가 와도 안내만. 헤더 nav 도 비움.
        if menu_key is not None:
            raise HTTPException(status_code=404, detail="Not Found")
        return templates.TemplateResponse(request, "team_portal.html",
                _ctx(request, team=team, deleted=True, portal_team=None, portal_menu=None))
    menu_vis = db.get_team_menu_visibility(team["id"])
    # active_menu 결정
    if menu_key is not None:
        # 명시 메뉴 — 해당 메뉴가 외부공개로 켜져 있어야 200.
        if not menu_vis.get(menu_key, False):
            raise HTTPException(status_code=404, detail="Not Found")
        active_menu = menu_key
    else:
        # /{team_name} 기본 진입 — 우선순위 순서로 첫 켜진 메뉴 선택. 0개면 None.
        active_menu = next((k for k in _PORTAL_MENU_ORDER if menu_vis.get(k, False)), None)
    # #14: 우상단 버튼 분기 — 비로그인/소속/대기/admin/미소속 표 (그룹 B #14).
    user = auth.get_current_user(request)
    my_team_status = None
    if user and not auth.is_admin(user):
        if team["id"] in auth.user_team_ids(user):
            my_team_status = "approved"
        else:
            my_team_status = db.get_my_team_statuses(user["id"]).get(team["id"])

    # v2 분기: `/{team}/{메뉴}` (menu_key 명시) 만 정식 템플릿으로 렌더.
    # `/{team}` 랜딩 (menu_key=None) 은 active_menu 가 우선순위로 결정된 값이라도
    # v1 team_portal.html 유지 — 랜딩은 "이 팀이 무엇을 가졌는지" 정보적 미리보기 역할.
    # advisor 권고: spec 일치 + 기존 phase100 test [3a] 회귀 방지.
    if menu_key is not None and active_menu in ("kanban", "gantt", "doc", "check"):
        # 정식 페이지 공통 컨텍스트 base — `is_public_portal=True` + 본문 user 마스킹.
        # 본문 마스킹은 _ctx 의 user 키를 None 으로 덮어써 templates 안의 `{% if user %}`
        # 가드를 비활성화함. base.html 의 헤더는 _ctx 의 결과를 그대로 받아 별도 user 변수가
        # 노출되지만, Jinja2 의 자식 block 안에서 user 를 None 으로 다시 set 해도 base 가
        # 먼저 평가되므로 헤더는 영향 없음. 다만 명시적으로 안전한 분리를 위해 헤더용 정보는
        # _ctx 가 채운 다른 키(work_team_id 등) 로 노출되어 마스킹과 무관.
        ctx = _ctx(
            request,
            is_public_portal=True,
            portal_team=team,        # base.html nav 분기에서 사용 (기존 v1 컨텍스트 키 동일)
            portal_menu=menu_vis,    # base.html nav 분기 — 어떤 메뉴 링크를 그릴지
            my_team_status=my_team_status,
            teams=[team],            # doc_list.html 의 주간 보고 셀렉터 등 — 현재 팀만 노출
        )
        # 본문에서 user 를 None 으로 가려 액션 UI 일괄 차단 (admin 이 와도 동일).
        ctx["user"] = None
        # _ensure_work_team_cookie 는 호출하지 않음 — 공개 포털은 작업 팀 쿠키를 건드리지 않는다.
        if active_menu == "kanban":
            return templates.TemplateResponse(request, "kanban.html", ctx)
        if active_menu == "gantt":
            return templates.TemplateResponse(request, "project.html", ctx)
        if active_menu == "doc":
            # doc_list.html 은 docs / default_model 컨텍스트 필요 — 공개 portal docs 로 한정.
            portal = db.get_public_portal_data(team["id"])
            ctx["docs"] = portal.get("docs", [])
            ctx["default_model"] = ""  # 비로그인은 AI 액션 불가 — 빈 문자열로 충분.
            return templates.TemplateResponse(request, "doc_list.html", ctx)
        if active_menu == "check":
            # check.html 은 projects / done_projects 컨텍스트 필요 —
            # viewer=None + work_team_ids={team_id} 로 외부공개 프로젝트만 가져옴.
            all_projs = db.get_all_projects_meta(viewer=None, work_team_ids={team["id"]})
            active_projs = [p for p in all_projs if p.get("is_active", 1)]
            done_projs   = [p for p in all_projs if not p.get("is_active", 1)]
            ctx["projects"] = active_projs
            ctx["done_projects"] = done_projs
            return templates.TemplateResponse(request, "check.html", ctx)

    # v1 랜딩 (active_menu is None 또는 위 분기에 없는 값): team_portal.html.
    portal = db.get_public_portal_data(team["id"])
    return templates.TemplateResponse(request, "team_portal.html",
            _ctx(request, team=team, deleted=False, portal=portal,
                 active_menu=active_menu, my_team_status=my_team_status,
                 portal_team=team, portal_menu=menu_vis))


# 신규: /{team_name}/{한글메뉴} — 4개 개별 라우트. /{team_name} 직전 등록.
@app.get("/{team_name}/칸반", response_class=HTMLResponse)
def team_public_portal_kanban(request: Request, team_name: str):
    return _render_team_menu(request, team_name, "kanban")


@app.get("/{team_name}/간트", response_class=HTMLResponse)
def team_public_portal_gantt(request: Request, team_name: str):
    return _render_team_menu(request, team_name, "gantt")


@app.get("/{team_name}/문서", response_class=HTMLResponse)
def team_public_portal_doc(request: Request, team_name: str):
    return _render_team_menu(request, team_name, "doc")


@app.get("/{team_name}/체크", response_class=HTMLResponse)
def team_public_portal_check(request: Request, team_name: str):
    return _render_team_menu(request, team_name, "check")


@app.get("/{team_name}", response_class=HTMLResponse)
def team_public_portal(request: Request, team_name: str):
    # #13: /팀이름 공개 포털. URL 은 권한 경계가 아니다 — 항상 공개 portal context.
    #   로그인 사용자·admin 이 와도 동일하게 200 공개 포털을 주되 redirect 하지 않는다.
    # 그룹 D catchup: 별도 .portal-tabs 영역 제거. active_menu 1개에 대한 단일 패널만 렌더.
    return _render_team_menu(request, team_name, None)


if __name__ == "__main__":
    import uvicorn
    bind_host = (os.environ.get("WHATUDOIN_BIND_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    # supervisor가 Web API를 internal port(8769)로 spawn할 때 WHATUDOIN_WEB_API_INTERNAL_PORT=8769
    # 미설정 시 기존 8000 유지. reload는 internal-only(supervisor 관리) 시 비활성화.
    _direct_port = int(os.environ.get("WHATUDOIN_WEB_API_INTERNAL_PORT", "8000") or "8000")
    _internal_only = os.environ.get("WHATUDOIN_WEB_API_INTERNAL_ONLY", "").strip() == "1"
    uvicorn.run("app:app", host=bind_host, port=_direct_port, reload=not _internal_only)
