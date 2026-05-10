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
    # 분리 1단계: Scheduler service. WHATUDOIN_ENABLE_SCHEDULER_SIDECAR=1 시
    # 별도 프로세스로 spawn하고 Web API lifespan에서 APScheduler 시작 분기 skip.
    # 토글 미설정 시 기존 fallback 동작 유지(VSCode 디버그/일상 운영 영향 0).
    _supervisor_instance = None
    _scheduler_sidecar_enabled = (
        os.environ.get("WHATUDOIN_ENABLE_SCHEDULER_SIDECAR", "").strip() == "1"
    )
    if _scheduler_sidecar_enabled:
        from supervisor import (
            WhatUdoinSupervisor, scheduler_service_spec,
            SCHEDULER_SERVICE_ENABLE_ENV,
        )
        _supervisor_instance = WhatUdoinSupervisor(run_dir=_run_dir())
        _supervisor_instance.ensure_internal_token()
        _scheduler_spec = scheduler_service_spec(
            command=[sys.executable, str(Path(_base_dir()) / "scheduler_service.py")],
        )
        _scheduler_state = _supervisor_instance.start_service(_scheduler_spec)
        # Web API lifespan이 APScheduler를 시작하지 않도록 분기 신호 주입
        os.environ[SCHEDULER_SERVICE_ENABLE_ENV] = "1"
        print(f"  [sidecar] scheduler service: pid={_scheduler_state.pid} status={_scheduler_state.status}")

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
    for e in server_exc:
        if "10048" in str(e) or "address already in use" in str(e).lower():
            port_str = f"{PORT_HTTP}/{PORT_HTTPS}" if have_https else str(PORT_HTTP)
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
