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

# Windows 콘솔 한글 깨짐 방지
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.system("chcp 65001 > nul 2>&1")


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


# ── 트레이 아이콘 / 콘솔창 제어 (frozen 환경 전용) ───────────────

def _console_hwnd() -> int:
    """PyInstaller 번들 환경에서만 콘솔창 HWND 반환."""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return 0
    return ctypes.windll.kernel32.GetConsoleWindow()


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


def _watch_minimize(hwnd: int, stop_event: threading.Event) -> None:
    """최소화 감지 시 창을 숨김으로 전환 (0.4초 폴링)."""
    import time
    user32 = ctypes.windll.user32
    SW_HIDE = 0
    while not stop_event.is_set():
        if hwnd and user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_HIDE)
        time.sleep(0.4)


def _make_tray(servers: list, hwnd: int, stop_event: threading.Event):
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

    # app.py / database.py 가 import 되기 전에 경로 설정
    os.environ.setdefault("WHATUDOIN_BASE_DIR", _base_dir())
    os.environ.setdefault("WHATUDOIN_RUN_DIR",  _run_dir())

    _ensure_credentials(_run_dir())
    _ensure_admin_guide(_run_dir())

    import uvicorn
    from app import app as fastapi_app  # noqa: E402

    PORT_HTTP  = 8000
    PORT_HTTPS = 8443

    cert_path = os.path.join(_run_dir(), "whatudoin-cert.pem")
    key_path  = os.path.join(_run_dir(), "whatudoin-key.pem")
    have_https = os.path.isfile(cert_path) and os.path.isfile(key_path)

    print("=" * 48)
    print("  WhatUdoin 서버 시작")
    print(f"  HTTP  : http://localhost:{PORT_HTTP}")
    if have_https:
        print(f"  HTTPS : https://localhost:{PORT_HTTPS}  (CA 설치 시)")
    else:
        print("  HTTPS : 미적용 (whatudoin-cert.pem / whatudoin-key.pem 없음)")
    print("  종료: 트레이 아이콘 우클릭 → 종료")
    print("=" * 48)

    http_cfg = uvicorn.Config(
        fastapi_app, host="0.0.0.0", port=PORT_HTTP, log_level="info"
    )
    servers = [uvicorn.Server(http_cfg)]
    if have_https:
        https_cfg = uvicorn.Config(
            fastapi_app, host="0.0.0.0", port=PORT_HTTPS, log_level="info",
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

    # ── 트레이 / 콘솔창 설정 (frozen exe 환경 전용) ──────────────
    hwnd = _console_hwnd()
    if hwnd:
        _hide_from_taskbar(hwnd)
        _disable_console_close(hwnd)
        threading.Thread(
            target=_watch_minimize, args=(hwnd, stop_event),
            daemon=True, name="minimize-watcher"
        ).start()

    # 트레이 아이콘은 개발 모드·frozen 모두 실행
    # (콘솔창 HWND 조작만 frozen 한정이므로 개발 시 터미널에 영향 없음)
    try:
        _make_tray(servers, hwnd, stop_event).run()
    except KeyboardInterrupt:
        pass
    for s in servers:
        s.should_exit = True
    t.join(timeout=5)

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
