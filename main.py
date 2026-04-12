"""
WhatUdoin 실행 진입점
- 개발: python main.py
- 배포: PyInstaller onedir 빌드 후 WhatUdoin.exe 실행
"""
import sys
import os
import multiprocessing


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


if __name__ == "__main__":
    multiprocessing.freeze_support()

    # app.py / database.py 가 import 되기 전에 경로 설정
    os.environ.setdefault("WHATUDOIN_BASE_DIR", _base_dir())
    os.environ.setdefault("WHATUDOIN_RUN_DIR",  _run_dir())

    import uvicorn
    from app import app as fastapi_app  # noqa: E402

    print("=" * 48)
    print("  WhatUdoin 서버 시작")
    print("  http://localhost:8000  으로 접속하세요")
    print("  종료: Ctrl+C")
    print("=" * 48)

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
