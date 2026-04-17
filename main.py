"""
WhatUdoin 실행 진입점
- 개발: python main.py
- 배포: PyInstaller onedir 빌드 후 WhatUdoin.exe 실행
"""
import sys
import os
import multiprocessing

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
    """credentials.json이 없으면 자동 생성 (crypto_key)."""
    import json
    from cryptography.fernet import Fernet

    creds_path = os.path.join(run_dir, "credentials.json")
    if os.path.exists(creds_path):
        return
    key = Fernet.generate_key().decode()
    with open(creds_path, "w", encoding="utf-8") as f:
        json.dump({"crypto_key": key}, f, indent=2, ensure_ascii=False)
    print(f"[WhatUdoin] credentials.json 생성됨: {creds_path}")


if __name__ == "__main__":
    multiprocessing.freeze_support()

    # app.py / database.py 가 import 되기 전에 경로 설정
    os.environ.setdefault("WHATUDOIN_BASE_DIR", _base_dir())
    os.environ.setdefault("WHATUDOIN_RUN_DIR",  _run_dir())

    _ensure_credentials(_run_dir())

    import uvicorn
    from app import app as fastapi_app  # noqa: E402

    PORT = 8000

    print("=" * 48)
    print("  WhatUdoin 서버 시작")
    print(f"  http://localhost:{PORT}  으로 접속하세요")
    print("  종료: Ctrl+C")
    print("=" * 48)

    try:
        uvicorn.run(
            fastapi_app,
            host="0.0.0.0",
            port=PORT,
            log_level="info",
        )
    except OSError as e:
        if "10048" in str(e) or "address already in use" in str(e).lower():
            print()
            print("=" * 48)
            print(f"  [오류] 포트 {PORT}이 이미 사용 중입니다.")
            print("  다른 WhatUdoin이 실행 중이거나,")
            print(f"  {PORT}번 포트를 사용하는 프로그램을 종료 후")
            print("  다시 실행해 주세요.")
            print("=" * 48)
        else:
            print(f"\n[오류] 서버 시작 실패: {e}")
        input("\n아무 키나 누르면 종료합니다...")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
