"""Media service — 파일 처리/검증/썸네일/메타데이터 전담 별도 프로세스.

M5-2: Web API의 파일 검증·처리를 분리. Web API는 POST /internal/process JSON IPC만 사용.
      DB write 0건. MEETINGS_DIR 이동 0건 — 검증 결과 metadata만 응답.

진입점: python media_service.py
환경변수:
  WHATUDOIN_MEDIA_PORT (기본 8768) — bind port
  WHATUDOIN_MEDIA_BIND_HOST (기본 127.0.0.1) — bind host
  WHATUDOIN_BASE_DIR / WHATUDOIN_RUN_DIR — 경로 해석 (app.py 패턴 동일)
  WHATUDOIN_INTERNAL_TOKEN — Bearer 토큰 검증용
  WHATUDOIN_STAGING_ROOT — staging 루트 디렉토리 (기본 _RUN_DIR/staging)

모듈 import 시 부작용 0: 인스턴스 생성/네트워크 호출 0.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def main() -> None:
    # ── 경로 해석 (app.py 패턴 동일) ─────────────────────────────────────────
    _BASE_DIR = Path(os.environ.get("WHATUDOIN_BASE_DIR", Path(__file__).parent))
    _RUN_DIR  = Path(os.environ.get("WHATUDOIN_RUN_DIR",  Path(__file__).parent))

    bind_host = os.environ.get("WHATUDOIN_MEDIA_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("WHATUDOIN_MEDIA_PORT", "8768"))

    staging_root = Path(
        os.environ.get("WHATUDOIN_STAGING_ROOT", str(_RUN_DIR / "staging"))
    )
    staging_root.mkdir(parents=True, exist_ok=True)

    # ── 로그 회전 설정 (M3-3/M4-1 패턴) ──────────────────────────────────────
    import logging
    import logging.handlers

    _log_dir = _RUN_DIR / "logs" / "services"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_path = _log_dir / "media.app.log"

    _rot_handler = logging.handlers.RotatingFileHandler(
        str(_log_path),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=14,
        encoding="utf-8",
        delay=True,  # Windows 파일 락 방지
    )
    _rot_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    ))
    _root_logger = logging.getLogger()
    _root_logger.addHandler(_rot_handler)
    _root_logger.setLevel(logging.INFO)

    logger = logging.getLogger("media_service")

    # ── 진입 시각 기록 (uptime 계산용) ───────────────────────────────────────
    from datetime import datetime, timezone
    _process_started_at = datetime.now(timezone.utc)

    # ── 처리 카운터 + 마지막 에러 ─────────────────────────────────────────────
    import threading
    _stats_lock = threading.Lock()
    _processed_count = 0
    _last_error = ""

    def _inc_processed():
        nonlocal _processed_count
        with _stats_lock:
            _processed_count += 1

    def _set_last_error(msg: str):
        nonlocal _last_error
        with _stats_lock:
            _last_error = msg

    def _get_stats():
        with _stats_lock:
            return _processed_count, _last_error

    # ── Starlette ASGI app ────────────────────────────────────────────────────
    import uvicorn
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    def _verify_token(request: Request) -> bool:
        """Bearer 토큰 검증. timing-safe 비교. raw 값 로그 0건."""
        import secrets as _secrets
        expected = os.environ.get("WHATUDOIN_INTERNAL_TOKEN", "").strip()
        if not expected:
            return False
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        provided = auth[len("Bearer "):]
        if not provided:
            return False
        return _secrets.compare_digest(expected, provided)

    def _loopback_guard(request: Request) -> bool:
        """loopback IP 이외 출처 차단."""
        client = request.client
        host = client.host if client else ""
        return host in _LOOPBACK_HOSTS

    def _safe_staging_path(staging_path_str: str) -> Path | None:
        """staging_path가 staging_root 하위인지 검증. 아니면 None 반환.

        symlink/.. 우회 차단: Path.resolve() 후 is_relative_to() 비교.
        """
        try:
            p = Path(staging_path_str).resolve()
            root = staging_root.resolve()
            if p.is_relative_to(root):
                return p
            return None
        except Exception:
            return None

    async def internal_process(request: Request) -> JSONResponse:
        """POST /internal/process — Web API가 파일 처리 위임하는 IPC 엔드포인트.

        Request body (JSON):
          kind: "image" | "attachment"
          staging_path: str — staging 루트 하위 절대 경로
          original_name: str — 원본 파일명 (ext 추출용)
          max_bytes: int — 크기 상한 (Web API 결정)

        Response 200:
          ok=True  → {ok, kind, original_name, size, sha256, ext, dimensions?:{w,h}}
          ok=False → {ok, reason: "too_large|invalid_image|forbidden_ext|missing|path_traversal|unauthorized|bad_request"}
        """
        # loopback 가드
        if not _loopback_guard(request):
            return JSONResponse({"ok": False, "reason": "forbidden"}, status_code=403)

        # Bearer 토큰 인증
        if not _verify_token(request):
            logger.warning(
                "unauthorized /internal/process attempt from %s",
                request.client.host if request.client else "unknown",
            )
            return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "reason": "bad_request"}, status_code=400)

        kind = body.get("kind", "")
        staging_path_str = body.get("staging_path", "")
        original_name = body.get("original_name", "")
        max_bytes = body.get("max_bytes", 0)

        if kind not in ("image", "attachment"):
            return JSONResponse({"ok": False, "reason": "bad_request"}, status_code=400)
        if not staging_path_str or not original_name or not max_bytes:
            return JSONResponse({"ok": False, "reason": "bad_request"}, status_code=400)

        # 1. staging_path 정규화 + staging root 하위 확인
        safe_path = _safe_staging_path(staging_path_str)
        if safe_path is None:
            logger.warning("path_traversal attempt: %s", staging_path_str)
            _set_last_error("path_traversal")
            return JSONResponse({"ok": False, "reason": "path_traversal"}, status_code=400)

        # 2. 파일 존재 확인
        if not safe_path.exists():
            _set_last_error("missing")
            return JSONResponse({"ok": False, "reason": "missing"}, status_code=400)

        # 3. 파일 크기 검사
        file_size = safe_path.stat().st_size
        if file_size > max_bytes:
            _set_last_error("too_large")
            return JSONResponse({"ok": False, "reason": "too_large"}, status_code=400)

        # 4. ext 추출
        ext = Path(original_name).suffix.lower() if original_name else ""

        # 5. kind별 검증
        dimensions = None

        if kind == "image":
            _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            if ext not in _IMAGE_EXTS:
                _set_last_error("forbidden_ext")
                return JSONResponse({"ok": False, "reason": "forbidden_ext"}, status_code=400)

            try:
                from PIL import Image as _PilImage
                # verify()는 스트림을 소진하므로 한 번 열고 닫은 뒤 다시 열어 size 읽기
                with open(safe_path, "rb") as f:
                    img = _PilImage.open(f)
                    img.verify()
                # verify 후 다시 열어서 dimensions 추출
                with open(safe_path, "rb") as f:
                    img2 = _PilImage.open(f)
                    w, h = img2.size
                dimensions = {"w": w, "h": h}
            except Exception as exc:
                _set_last_error("invalid_image")
                logger.info("invalid_image: %s — %s", original_name, exc)
                return JSONResponse({"ok": False, "reason": "invalid_image"}, status_code=400)

        elif kind == "attachment":
            _ATTACH_EXTS = {".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".zip", ".7z"}
            if ext not in _ATTACH_EXTS:
                _set_last_error("forbidden_ext")
                return JSONResponse({"ok": False, "reason": "forbidden_ext"}, status_code=400)

        # 6. SHA-256 해시 (16자)
        import hashlib
        sha_obj = hashlib.sha256()
        with open(safe_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha_obj.update(chunk)
        sha256 = sha_obj.hexdigest()[:16]

        _inc_processed()
        logger.info("processed kind=%s ext=%s size=%d sha256=%s", kind, ext, file_size, sha256)

        resp: dict = {
            "ok": True,
            "kind": kind,
            "original_name": original_name,
            "size": file_size,
            "sha256": sha256,
            "ext": ext,
        }
        if dimensions is not None:
            resp["dimensions"] = dimensions

        return JSONResponse(resp)

    async def healthz(request: Request) -> JSONResponse:
        """/healthz — 상태 확인 (M3-3/M4-1 패턴)."""
        try:
            now = datetime.now(timezone.utc)
            uptime_seconds = int((now - _process_started_at).total_seconds())
            processed, last_err = _get_stats()

            result: dict = {
                "status": "ok",
                "service": "media",
                "staging_root": str(staging_root),
                "processed_count": processed,
                "uptime_seconds": uptime_seconds,
            }
            if last_err:
                result["last_error"] = last_err
            return JSONResponse(result)
        except Exception as exc:
            return JSONResponse({
                "status": "degraded",
                "service": "media",
                "staging_root": str(staging_root),
                "processed_count": 0,
                "uptime_seconds": 0,
                "last_error": str(exc),
            })

    app = Starlette(
        routes=[
            Route("/internal/process", internal_process, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
        ],
    )

    # ── graceful shutdown (SIGTERM) ───────────────────────────────────────────
    _server: uvicorn.Server | None = None

    def _handle_sigterm(signum, frame):
        if _server is not None:
            _server.should_exit = True

    if os.name != "nt":
        signal.signal(signal.SIGTERM, _handle_sigterm)

    config = uvicorn.Config(
        app,
        host=bind_host,
        port=port,
        log_level="info",
    )
    _server = uvicorn.Server(config)
    _server.run()


if __name__ == "__main__":
    main()
