"""Ollama service — Ollama HTTP 호출을 전담하는 별도 프로세스.

M4-1: Main app의 Ollama 직접 호출을 분리. Main app은 POST /internal/llm JSON IPC만 사용.

진입점: python ollama_service.py
환경변수:
  WHATUDOIN_OLLAMA_PORT (기본 8767) — bind port
  WHATUDOIN_OLLAMA_BIND_HOST (기본 127.0.0.1) — bind host
  WHATUDOIN_BASE_DIR / WHATUDOIN_RUN_DIR — 경로 해석 (app.py 패턴 동일)
  WHATUDOIN_INTERNAL_TOKEN — Bearer 토큰 검증용

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

    bind_host = os.environ.get("WHATUDOIN_OLLAMA_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("WHATUDOIN_OLLAMA_PORT", "8767"))

    # ── 로그 회전 설정 (M3-3 패턴) ───────────────────────────────────────────
    import logging
    import logging.handlers

    _log_dir = _RUN_DIR / "logs" / "services"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_path = _log_dir / "ollama.app.log"

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

    # ── 진입 시각 기록 (uptime 계산용) ───────────────────────────────────────
    from datetime import datetime, timezone
    _process_started_at = datetime.now(timezone.utc)

    # ── llm_parser에서 limiter + Ollama 호출 로직 import ─────────────────────
    # 이 service가 _OllamaLimiter 인스턴스를 보유. main app 측은 사용 안 함.
    import llm_parser as _lp
    import requests as _requests

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

    async def internal_llm(request: Request) -> JSONResponse:
        """POST /internal/llm — Main app이 Ollama 호출 위임하는 IPC 엔드포인트.

        Request body:
          task: str — 작업 종류 (parse_schedule, refine_schedule, weekly_report,
                       review_conflicts, review_conflicts_funnel, checklist,
                       event_checklist_items, models)
          prompt: str — 전달할 프롬프트 텍스트 (tasks별 의미 다름)
          model: str? — 모델명 (미지정 시 llm_parser DEFAULT_MODEL)
          num_ctx: int? — 컨텍스트 토큰 수
          timeout: int? — 호출 타임아웃(초)
          user_id: str? — 로깅용 사용자 식별자 (로그에만 기록)

        Response:
          200 {ok: true, result: <generated text>}
          200 {ok: false, reason: "busy", slots: {used, max}}
          200 {ok: false, reason: "timeout"|"connect"|"5xx"}
          401 {error: "unauthorized"}
          403 {error: "forbidden"}
        """
        # loopback 가드
        if not _loopback_guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        # Bearer 토큰 인증
        if not _verify_token(request):
            logging.getLogger("ollama_service").warning(
                "unauthorized /internal/llm attempt from %s",
                request.client.host if request.client else "unknown",
            )
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        task = body.get("task", "")
        prompt = body.get("prompt", "")
        model = body.get("model") or _lp.DEFAULT_MODEL
        num_ctx = int(body.get("num_ctx") or _lp._NUM_CTX)
        timeout = int(body.get("timeout") or _lp._TIMEOUT)
        user_id = body.get("user_id") or ""

        if not task or not isinstance(prompt, str):
            return JSONResponse({"error": "task and prompt required"}, status_code=400)

        logger = logging.getLogger("ollama_service")
        if user_id:
            logger.info("llm task=%s model=%s user=%s", task, model, user_id)
        else:
            logger.info("llm task=%s model=%s", task, model)

        # limiter 획득 시도
        if not _lp._ollama_limiter.try_acquire():
            in_use, cap = _lp._ollama_limiter.snapshot()
            logger.warning("ollama_service: limiter busy in_use=%d cap=%d", in_use, cap)
            return JSONResponse({
                "ok": False,
                "reason": "busy",
                "slots": {"used": in_use, "max": cap},
            })

        try:
            result_text = _call_ollama(
                task=task,
                prompt=prompt,
                model=model,
                num_ctx=num_ctx,
                timeout=timeout,
            )
        except _lp.OllamaUnavailableError as exc:
            logger.warning("ollama_service: OllamaUnavailableError reason=%s", exc.reason)
            return JSONResponse({"ok": False, "reason": exc.reason})
        except Exception as exc:
            logger.warning("ollama_service: unexpected error: %s", exc)
            return JSONResponse({"ok": False, "reason": "5xx"})
        finally:
            _lp._ollama_limiter.release()

        return JSONResponse({"ok": True, "result": result_text})

    def _call_ollama(*, task: str, prompt: str, model: str, num_ctx: int, timeout: int) -> str:
        """실제 Ollama HTTP 호출. OllamaUnavailableError / RuntimeError raise 가능."""
        session = _lp._session
        url = _lp.OLLAMA_URL

        try:
            resp = session.post(
                url,
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_ctx": num_ctx},
                },
                timeout=timeout,
            )
            if resp.status_code >= 500:
                raise _lp.OllamaUnavailableError(reason="5xx")
            resp.raise_for_status()
            return resp.json().get("response", "")
        except _lp.OllamaUnavailableError:
            raise
        except _requests.Timeout:
            raise _lp.OllamaUnavailableError(reason="timeout")
        except _requests.ConnectionError:
            raise _lp.OllamaUnavailableError(reason="connect")
        except _requests.RequestException as exc:
            raise _lp.OllamaUnavailableError(reason="5xx") from exc

    async def healthz(request: Request) -> JSONResponse:
        """/healthz — 상태 확인 (starting/ok/degraded 패턴, M3-3)."""
        try:
            now = datetime.now(timezone.utc)
            uptime_seconds = int((now - _process_started_at).total_seconds())

            in_use, capacity = _lp._ollama_limiter.snapshot()

            # Ollama 서버 연결 확인
            ollama_health = "unreachable"
            try:
                resp = _lp._session.get(
                    _lp.OLLAMA_BASE_URL + "/api/tags",
                    timeout=3,
                )
                if resp.status_code < 500:
                    ollama_health = "ok"
            except Exception:
                pass

            status = "ok" if ollama_health == "ok" else "degraded"

            return JSONResponse({
                "status": status,
                "service": "ollama",
                "limiter": {"in_use": in_use, "capacity": capacity},
                "ollama_health": ollama_health,
                "uptime_seconds": uptime_seconds,
            })
        except Exception as exc:
            return JSONResponse({
                "status": "degraded",
                "service": "ollama",
                "limiter": {"in_use": 0, "capacity": 0},
                "ollama_health": "unreachable",
                "uptime_seconds": 0,
                "error": str(exc),
            })

    app = Starlette(
        routes=[
            Route("/internal/llm", internal_llm, methods=["POST"]),
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
