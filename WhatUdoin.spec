# -*- mode: python ; coding: utf-8 -*-
"""
WhatUdoin PyInstaller 스펙 파일
빌드: pyinstaller WhatUdoin.spec
결과: dist/WhatUdoin/ 디렉토리
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 정적 자원 (번들 내부, 읽기전용)
        ('templates',       'templates'),
        ('static',          'static'),
        ('docs',            'docs'),
        ('changelog',       'changelog'),
        ('credentials.json', '.'),
        # 분리 service 진입점 (42차 사이클 — 향후 frozen self re-spawn 시 사용)
        # 현재 fallback 모드에서는 hiddenimports만으로 충분하지만, 분리 토글
        # 활성화 시 supervisor가 sub-process로 띄울 수 있게 .py 파일도 번들에 포함.
        ('sse_service.py',          '.'),
        ('scheduler_service.py',    '.'),
        ('ollama_service.py',       '.'),
        ('media_service.py',        '.'),
        ('front_router.py',         '.'),
        ('supervisor.py',           '.'),
        ('publisher.py',            '.'),
        ('maintenance_owners.py',   '.'),
        ('mcp_command_registry.py', '.'),
        # 그룹 A 보강 사이클 — 운영자 진단 도구.
        # main.py가 `--doctor` sub-command로 dispatch (tools.migration_doctor).
        ('tools/__init__.py',         'tools'),
        ('tools/migration_doctor.py', 'tools'),
    ],
    hiddenimports=[
        # ── uvicorn ──────────────────────────────────────
        'uvicorn',
        'uvicorn.config',
        'uvicorn.main',
        'uvicorn.server',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        # ── fastapi / starlette ───────────────────────────
        'fastapi',
        'fastapi.routing',
        'fastapi.responses',
        'fastapi.staticfiles',
        'fastapi.templating',
        'starlette',
        'starlette.routing',
        'starlette.staticfiles',
        'starlette.templating',
        'starlette.middleware',
        'starlette.middleware.cors',
        'starlette.responses',
        'starlette.requests',
        'starlette.background',
        'starlette.concurrency',
        'starlette.datastructures',
        'starlette.exceptions',
        'starlette.formparsers',
        'starlette.websockets',
        # ── pydantic ─────────────────────────────────────
        'pydantic',
        'pydantic.v1',
        'pydantic_core',
        # ── jinja2 ───────────────────────────────────────
        'jinja2',
        'jinja2.ext',
        'markupsafe',
        # ── 비동기 / 네트워크 ──────────────────────────────
        'anyio',
        'anyio._backends._asyncio',
        'anyio._backends._trio',
        'sniffio',
        'h11',
        'httptools',
        'websockets',
        'websockets.legacy',
        'websockets.legacy.server',
        'wsproto',
        'watchfiles',
        # ── 파일 / 폼 ────────────────────────────────────
        'aiofiles',
        'multipart',
        'python_multipart',
        # ── 표준 라이브러리 보조 ──────────────────────────
        'sqlite3',
        'email.mime',
        'email.mime.text',
        'email.mime.multipart',
        'logging.handlers',
        # ── HTTP 클라이언트 (Ollama 연동) ─────────────────
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        # ── 시스템 트레이 ────────────────────────────────
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        # ── APScheduler (알림 스케줄러) ───────────────────
        'apscheduler',
        'apscheduler.schedulers',
        'apscheduler.schedulers.background',
        'apscheduler.executors',
        'apscheduler.executors.pool',
        'apscheduler.jobstores',
        'apscheduler.jobstores.memory',
        'apscheduler.triggers',
        'apscheduler.triggers.interval',
        'apscheduler.triggers.cron',
        'apscheduler.triggers.date',
        # ── 42차 사이클: 분리 인프라 모듈 ─────────────────
        # fallback 모드(분리 토글 미설정) 정상 import 보장 + 향후
        # frozen self re-spawn 시 sub-process가 import할 service 모듈.
        'publisher',
        'supervisor',
        'sse_service',
        'scheduler_service',
        'ollama_service',
        'media_service',
        'front_router',
        'maintenance_owners',
        'mcp_command_registry',
        'broker',
        # ── 그룹 A 보강 사이클 — migration_doctor sub-command ──
        'tools',
        'tools.migration_doctor',
        # ── httpx (Front Router reverse HTTP proxy 모드) ──
        # 분리 4단계(WHATUDOIN_ENABLE_FRONTEND_ROUTING=1) 활성화 시 사용.
        # fallback 모드에서는 import 자체가 발생하지 않으니 부담 0.
        'httpx',
        'httpx._transports',
        'httpx._transports.default',
        'httpx._transports.asgi',
        'httpcore',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 불필요한 무거운 패키지 제거
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PyQt5',
        'PyQt6',
        'wx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WhatUdoin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # 보조 콘솔(AllocConsole)로 관리 — primary console 없음
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/favicon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WhatUdoin',
)
