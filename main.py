"""
WhatUdoin 실행 진입점
- 개발: python main.py
- 배포: PyInstaller onedir 빌드 후 WhatUdoin.exe 실행
"""
import sys
import os
import asyncio
import shutil
import multiprocessing
import threading
import ctypes
from pathlib import Path

# 콘솔 HWND 공유 참조 (트레이·minimize-watcher·close-handler 공유)
_HWND_REF: list = [0]
# AllocConsole 실패 시 로그 파일 fallback 핸들
_LOG_FILE = None


def _base_dir() -> str:
    """정적 자원(templates, static) 위치.
    번들 실행 시 _MEIPASS, 개발 시 소스 파일 디렉토리."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def _run_dir() -> str:
    """DB·업로드 파일 저장 위치 (쓰기 가능해야 함).
    번들: 실행 파일(.exe) 옆 디렉토리, 개발: 소스 파일 디렉토리."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _ensure_credentials(run_dir: str) -> None:
    """credentials.json 준비.
    1) exe 옆에 이미 있으면 그대로 사용
    2) 번들 내(_MEIPASS)에 포함된 파일이 있으면 exe 옆으로 복사
    3) 둘 다 없으면 새 키를 생성"""
    import json
    from cryptography.fernet import Fernet

    creds_path = os.path.join(run_dir, "credentials.json")
    if os.path.exists(creds_path):
        return

    # 번들 내 포함된 credentials.json 복사 (PyInstaller frozen 환경)
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "credentials.json")  # type: ignore[attr-defined]
        if os.path.exists(bundled):
            shutil.copy2(bundled, creds_path)
            print(f"[WhatUdoin] credentials.json 복사됨: {creds_path}")
            return

    key = Fernet.generate_key().decode()
    with open(creds_path, "w", encoding="utf-8") as f:
        json.dump({"crypto_key": key}, f, indent=2, ensure_ascii=False)
    print(f"[WhatUdoin] credentials.json 생성됨: {creds_path}")


def _ensure_admin_guide(run_dir: str) -> None:
    """번들에 포함된 관리자 가이드를 exe 옆 docs/로 1회 복사."""
    src_dir = os.path.join(_base_dir(), "docs")
    dst_dir = os.path.join(run_dir, "docs")
    if not os.path.isdir(src_dir):
        return
    os.makedirs(dst_dir, exist_ok=True)
    for fn in os.listdir(src_dir):
        dst = os.path.join(dst_dir, fn)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(src_dir, fn), dst)


# ── 트레이 아이콘 / 콘솔창 제어 ───────────────────────────────────


def _hide_from_taskbar(hwnd: int) -> None:
    """WS_EX_TOOLWINDOW 적용으로 작업 표시줄에서 창을 제거."""
    GWL_EXSTYLE    = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW  = 0x00040000
    SW_HIDE = 0
    SW_SHOW = 5
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    user32.ShowWindow(hwnd, SW_HIDE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    user32.ShowWindow(hwnd, SW_SHOW)


def _disable_console_close(hwnd: int) -> None:
    """시스템 메뉴에서 SC_CLOSE 제거 → X 버튼 비활성(회색)."""
    SC_CLOSE    = 0xF060
    MF_BYCOMMAND = 0x0
    user32 = ctypes.windll.user32
    hmenu = user32.GetSystemMenu(hwnd, 0)
    if hmenu:
        user32.DeleteMenu(hmenu, SC_CLOSE, MF_BYCOMMAND)


def _intercept_console_close(hwnd_ref: list) -> None:
    """AllocConsole로 만든 보조 콘솔의 닫기를 숨김으로 전환.
    보조 콘솔은 프로세스 primary console이 아니므로 닫아도
    OS가 프로세스 종료 신호를 보내지 않음 — ShowWindow(SW_HIDE)만 수행."""
    CTRL_CLOSE_EVENT = 2
    SW_HIDE = 0
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    def _handler(ctrl_type):
        if ctrl_type == CTRL_CLOSE_EVENT:
            if hwnd_ref[0]:
                user32.ShowWindow(hwnd_ref[0], SW_HIDE)
            return True
        return False

    _intercept_console_close._handler = _handler
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True)


def _watch_minimize(hwnd_ref: list, stop_event: threading.Event) -> None:
    """최소화 감지 시 창을 숨김으로 전환 (0.4초 폴링)."""
    import time
    user32 = ctypes.windll.user32
    SW_HIDE = 0
    while not stop_event.is_set():
        hwnd = hwnd_ref[0]
        if hwnd and user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_HIDE)
        time.sleep(0.4)


def _make_tray(servers: list, hwnd_ref: list, stop_event: threading.Event):
    """pystray 트레이 아이콘 오브젝트 생성."""
    import pystray
    from PIL import Image
    import webbrowser

    icon_path = os.path.join(_base_dir(), "static", "favicon.ico")
    image = Image.open(icon_path)
    user32 = ctypes.windll.user32
    SW_HIDE    = 0
    SW_RESTORE = 9

    def toggle_log(icon, item):
        hwnd = hwnd_ref[0]
        if not hwnd:
            return
        if user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, SW_HIDE)
        else:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)

    def open_browser(icon, item):
        webbrowser.open("http://localhost:8000")

    def on_quit(icon, item):
        for s in servers:
            s.should_exit = True
        stop_event.set()
        icon.stop()
        if _LOG_FILE:
            try:
                _LOG_FILE.close()
            except Exception:
                pass

    menu = pystray.Menu(
        pystray.MenuItem("브라우저에서 열기", open_browser, default=True),
        pystray.MenuItem("로그 창 표시/숨김", toggle_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", on_quit),
    )
    return pystray.Icon("WhatUdoin", image, "WhatUdoin 서버", menu)


# Windows ProactorEventLoop의 ConnectionResetError 10054 노이즈 억제
# socket.shutdown()이 이미 닫힌 소켓에 호출될 때 발생하는 Windows 고유 버그
if sys.platform == "win32":
    import asyncio.proactor_events as _pe
    _orig_ccl = _pe._ProactorBasePipeTransport._call_connection_lost

    def _patched_ccl(self, exc):
        try:
            _orig_ccl(self, exc)
        except ConnectionResetError:
            pass

    _pe._ProactorBasePipeTransport._call_connection_lost = _patched_ccl


if __name__ == "__main__":
    multiprocessing.freeze_support()

    # ── 콘솔/스트림 초기화 ──────────────────────────────────────────
    if sys.platform == "win32":
        if getattr(sys, "frozen", False):
            # console=False 빌드: AllocConsole로 보조 콘솔 생성
            # 보조 콘솔은 프로세스 primary console이 아니므로
            # 닫아도 OS가 프로세스 종료 신호를 보내지 않음
            if ctypes.windll.kernel32.AllocConsole():
                sys.stdout = open("CONOUT$", "w", encoding="utf-8",
                                  errors="replace", buffering=1)
                sys.stderr = open("CONOUT$", "w", encoding="utf-8",
                                  errors="replace", buffering=1)
                sys.stdin  = open("CONIN$",  "r", encoding="utf-8",
                                  errors="replace")
                os.system("chcp 65001 > nul 2>&1")
                _HWND_REF[0] = ctypes.windll.kernel32.GetConsoleWindow()
            else:
                # 콘솔 할당 실패 → 로그 파일 fallback
                _LOG_FILE = open(
                    os.path.join(_run_dir(), "whatudoin.log"),
                    "a", encoding="utf-8", buffering=1,
                )
                sys.stdout = sys.stderr = _LOG_FILE
        else:
            # 개발 모드: UTF-8 래핑
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer,
                                          encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer,
                                          encoding="utf-8", errors="replace")
            os.system("chcp 65001 > nul 2>&1")

    # app.py / database.py 가 import 되기 전에 경로 설정
    os.environ.setdefault("WHATUDOIN_BASE_DIR", _base_dir())
    os.environ.setdefault("WHATUDOIN_RUN_DIR",  _run_dir())

    _ensure_credentials(_run_dir())
    _ensure_admin_guide(_run_dir())

    # ── 단계적 sidecar 분리 (env 토글) ─────────────────────────
    # 토글별로 service를 별도 프로세스로 spawn한다. 어떤 토글도 미설정이면
    # 기존 fallback 단일 프로세스 동작 100% 유지(VSCode 디버그/일상 운영 영향 0).
    #   분리 1단계: WHATUDOIN_ENABLE_SCHEDULER_SIDECAR=1  → Scheduler service
    #   분리 2단계: WHATUDOIN_ENABLE_MEDIA_SIDECAR=1      → Media service
    #   분리 3단계: WHATUDOIN_ENABLE_OLLAMA_SIDECAR=1     → Ollama service
    #   분리 4단계: WHATUDOIN_ENABLE_FRONTEND_ROUTING=1   → Front Router + Web API(internal) + SSE
    _supervisor_instance = None
    _scheduler_sidecar_enabled = (
        os.environ.get("WHATUDOIN_ENABLE_SCHEDULER_SIDECAR", "").strip() == "1"
    )
    _media_sidecar_enabled = (
        os.environ.get("WHATUDOIN_ENABLE_MEDIA_SIDECAR", "").strip() == "1"
    )
    _ollama_sidecar_enabled = (
        os.environ.get("WHATUDOIN_ENABLE_OLLAMA_SIDECAR", "").strip() == "1"
    )
    _frontend_routing_enabled = (
        os.environ.get("WHATUDOIN_ENABLE_FRONTEND_ROUTING", "").strip() == "1"
    )

    if _scheduler_sidecar_enabled or _media_sidecar_enabled or _ollama_sidecar_enabled or _frontend_routing_enabled:
        from supervisor import WhatUdoinSupervisor
        _supervisor_instance = WhatUdoinSupervisor(run_dir=_run_dir())
        _supervisor_instance.ensure_internal_token()

    if _scheduler_sidecar_enabled:
        from supervisor import scheduler_service_spec, SCHEDULER_SERVICE_ENABLE_ENV
        _scheduler_spec = scheduler_service_spec(
            command=[sys.executable, str(Path(_base_dir()) / "scheduler_service.py")],
        )
        _scheduler_state = _supervisor_instance.start_service(_scheduler_spec)
        # Web API lifespan이 APScheduler를 시작하지 않도록 분기 신호 주입
        os.environ[SCHEDULER_SERVICE_ENABLE_ENV] = "1"
        print(f"  [sidecar] scheduler service: pid={_scheduler_state.pid} status={_scheduler_state.status}")

    if _media_sidecar_enabled:
        from supervisor import (
            media_service_spec, MEDIA_SERVICE_URL_ENV, MEDIA_SERVICE_DEFAULT_PORT,
        )
        _media_spec = media_service_spec(
            command=[sys.executable, str(Path(_base_dir()) / "media_service.py")],
        )
        _media_state = _supervisor_instance.start_service(_media_spec)
        # Web API 업로드 핸들러가 IPC 모드로 분기하도록 URL 자동 주입
        _media_url = f"http://127.0.0.1:{MEDIA_SERVICE_DEFAULT_PORT}/internal/process"
        os.environ[MEDIA_SERVICE_URL_ENV] = _media_url
        print(f"  [sidecar] media service: pid={_media_state.pid} status={_media_state.status} url={_media_url}")

    if _ollama_sidecar_enabled:
        from supervisor import (
            ollama_service_spec, OLLAMA_SERVICE_URL_ENV, OLLAMA_SERVICE_DEFAULT_PORT,
        )
        _ollama_spec = ollama_service_spec(
            command=[sys.executable, str(Path(_base_dir()) / "ollama_service.py")],
        )
        _ollama_state = _supervisor_instance.start_service(_ollama_spec)
        # llm_parser가 IPC 모드로 분기하도록 URL 자동 주입
        _ollama_url = f"http://127.0.0.1:{OLLAMA_SERVICE_DEFAULT_PORT}/internal/llm"
        os.environ[OLLAMA_SERVICE_URL_ENV] = _ollama_url
        print(f"  [sidecar] ollama service: pid={_ollama_state.pid} status={_ollama_state.status} url={_ollama_url}")

    if _frontend_routing_enabled:
        from supervisor import (
            sse_service_spec, SSE_SERVICE_DEFAULT_PORT,
            web_api_internal_runtime_spec, WEB_API_INTERNAL_DEFAULT_PORT, WEB_API_INTERNAL_PORT_ENV,
            front_router_service_spec, FRONT_ROUTER_SERVICE_NAME,
        )
        # SSE service: internal-only 127.0.0.1:8765
        _fr_sse_port = SSE_SERVICE_DEFAULT_PORT
        _fr_sse_spec = sse_service_spec(
            command=[sys.executable, str(Path(_base_dir()) / "sse_service.py")],
        )
        _fr_sse_state = _supervisor_instance.start_service(_fr_sse_spec)
        print(f"  [4B] sse service: pid={_fr_sse_state.pid} status={_fr_sse_state.status}")

        # Web API: internal-only 127.0.0.1:8769
        _fr_web_api_port = WEB_API_INTERNAL_DEFAULT_PORT
        _fr_web_api_spec = web_api_internal_runtime_spec(
            command=[sys.executable, str(Path(_base_dir()) / "app.py")],
            port=_fr_web_api_port,
        )
        _fr_web_api_state = _supervisor_instance.start_service(_fr_web_api_spec)
        print(f"  [4B] web-api service: pid={_fr_web_api_state.pid} status={_fr_web_api_state.status} port={_fr_web_api_port}")

        # Front Router: 외부 0.0.0.0:8000/8443, web-api → 8769, sse → 8765
        _fr_cert = os.path.join(_run_dir(), "whatudoin-cert.pem")
        _fr_key = os.path.join(_run_dir(), "whatudoin-key.pem")
        _fr_have_tls = os.path.isfile(_fr_cert) and os.path.isfile(_fr_key)
        _fr_spec = front_router_service_spec(
            command=[sys.executable, str(Path(_base_dir()) / "front_router.py")],
            bind_host="0.0.0.0",
            http_port=8000,
            https_port=8443,
            cert_path=_fr_cert if _fr_have_tls else None,
            key_path=_fr_key if _fr_have_tls else None,
            web_api_url=f"http://127.0.0.1:{_fr_web_api_port}",
            sse_url=f"http://127.0.0.1:{_fr_sse_port}",
        )
        _fr_state = _supervisor_instance.start_service(_fr_spec)
        print(f"  [4B] front-router service: pid={_fr_state.pid} status={_fr_state.status}")
        print("  [4B] 외부 listener는 front-router가 담당 — main.py 직접 listen 비활성")
        # main.py 자체는 외부 listener 미가동: servers = [] 로 처리
        servers: list = []
    else:
        import uvicorn
        from app import app as fastapi_app  # noqa: E402

        PORT_HTTP  = 8000
        PORT_HTTPS = 8443
        bind_host = (os.environ.get("WHATUDOIN_BIND_HOST") or "0.0.0.0").strip() or "0.0.0.0"

        cert_path = os.path.join(_run_dir(), "whatudoin-cert.pem")
        key_path  = os.path.join(_run_dir(), "whatudoin-key.pem")
        have_https = os.path.isfile(cert_path) and os.path.isfile(key_path)

        print("=" * 48)
        print("  WhatUdoin 서버 시작")
        print(f"  BIND  : {bind_host}")
        print(f"  HTTP  : http://localhost:{PORT_HTTP}")
        if have_https:
            print(f"  HTTPS : https://localhost:{PORT_HTTPS}  (CA 설치 시)")
        else:
            print("  HTTPS : 미적용 (whatudoin-cert.pem / whatudoin-key.pem 없음)")
        print("  종료: 트레이 아이콘 우클릭 → 종료")
        print("=" * 48)

        http_cfg = uvicorn.Config(
            fastapi_app, host=bind_host, port=PORT_HTTP, log_level="info"
        )
        servers = [uvicorn.Server(http_cfg)]
        if have_https:
            https_cfg = uvicorn.Config(
                fastapi_app, host=bind_host, port=PORT_HTTPS, log_level="info",
                ssl_certfile=cert_path, ssl_keyfile=key_path,
            )
            servers.append(uvicorn.Server(https_cfg))

    async def _run_all():
        await asyncio.gather(*(s.serve() for s in servers))

    stop_event = threading.Event()
    server_exc: list = []

    def _server_thread():
        try:
            asyncio.run(_run_all())
        except Exception as e:
            server_exc.append(e)
        finally:
            stop_event.set()

    t = threading.Thread(target=_server_thread, daemon=True, name="uvicorn")
    t.start()

    # ── 트레이 / 콘솔창 설정 ─────────────────────────────────────
    if _HWND_REF[0]:
        _hide_from_taskbar(_HWND_REF[0])
        _disable_console_close(_HWND_REF[0])
        _intercept_console_close(_HWND_REF)
        threading.Thread(
            target=_watch_minimize, args=(_HWND_REF, stop_event),
            daemon=True, name="minimize-watcher"
        ).start()

    try:
        _make_tray(servers, _HWND_REF, stop_event).run()
    except KeyboardInterrupt:
        pass
    for s in servers:
        s.should_exit = True
    t.join(timeout=5)

    # ── sidecar graceful shutdown ─────────────────────────────────
    if _supervisor_instance is not None:
        try:
            _supervisor_instance.stop_all(timeout=5.0)
            print("  [sidecar] all services stopped")
        except Exception as exc:
            print(f"  [sidecar] stop_all error: {exc}")

    # ── 포트 충돌 오류 처리 ───────────────────────────────────────
    # _frontend_routing_enabled 시 servers=[] → server_exc는 항상 비어있음
    for e in server_exc:
        if "10048" in str(e) or "address already in use" in str(e).lower():
            _ph = locals().get("PORT_HTTP", 8000)
            _ps = locals().get("PORT_HTTPS", 8443)
            _hs = locals().get("have_https", False)
            port_str = f"{_ph}/{_ps}" if _hs else str(_ph)
            print()
            print("=" * 48)
            print(f"  [오류] 포트 {port_str}이 이미 사용 중입니다.")
            print("  다른 WhatUdoin이 실행 중이거나,")
            print("  해당 포트를 사용하는 프로그램을 종료 후")
            print("  다시 실행해 주세요.")
            print("=" * 48)
        else:
            print(f"\n[오류] 서버 시작 실패: {e}")
        if getattr(sys, "frozen", False):
            input("\n아무 키나 누르면 종료합니다...")
        sys.exit(1)
