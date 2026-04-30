from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
import asyncio
import json
import os
import re
import uuid

import requests as _requests

import io
import zipfile
from urllib.parse import quote

from fastapi import FastAPI, Request, HTTPException, Response, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import database as db
import llm_parser
import auth
import crypto
import backup
from broker import wu_broker

scheduler = AsyncIOScheduler()

# ── 경로 해석 ─────────────────────────────────────────────
# PyInstaller 번들: WHATUDOIN_BASE_DIR = sys._MEIPASS (읽기전용 자원)
#                   WHATUDOIN_RUN_DIR  = exe 옆 디렉토리 (쓰기 가능)
# 개발 실행:        두 값 모두 소스 파일 디렉토리
_BASE_DIR = Path(os.environ.get("WHATUDOIN_BASE_DIR", Path(__file__).parent))
_RUN_DIR  = Path(os.environ.get("WHATUDOIN_RUN_DIR",  Path(__file__).parent))

# 회의록 이미지 저장 루트 (앱 기동 전에 생성해야 StaticFiles 마운트 가능)
MEETINGS_DIR = _RUN_DIR / "meetings"
MEETINGS_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # SSE broker에 현재 이벤트 루프 등록 (sync 핸들러에서 publish 시 필수)
    wu_broker.start_on_loop(asyncio.get_running_loop())
    saved_url = db.get_setting("ollama_url")
    if saved_url:
        llm_parser.set_ollama_base_url(saved_url)
    db.finalize_expired_done()  # 서버 시작 시 만료된 done 일정 즉시 처리
    try:
        await run_in_threadpool(backup.run_backup, db.DB_PATH, _RUN_DIR)
    except Exception as _e:
        import logging
        logging.getLogger("whatudoin").warning("서버 시작 백업 실패: %s", _e)
    if not scheduler.running:
        # APScheduler: 1분마다 15분 후 일정 알람 체크
        scheduler.add_job(db.check_upcoming_event_alarms, "interval", minutes=1)
        # APScheduler: 매일 새벽 3시 휴지통 30일 초과 항목 정리
        scheduler.add_job(db.cleanup_old_trash, "cron", hour=3, minute=0)
        # APScheduler: 매일 새벽 3시 5분 done 7일 경과 일정 자동 완료 처리
        scheduler.add_job(db.finalize_expired_done, "cron", hour=3, minute=5)
        # APScheduler: 매일 새벽 2시 DB 백업 (90일 보관)
        scheduler.add_job(
            lambda: backup.run_backup(db.DB_PATH, _RUN_DIR),
            "cron", hour=2, minute=0,
            id="daily-db-backup", replace_existing=True,
        )
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="WhatUDoin", lifespan=lifespan)
app.mount("/static",          StaticFiles(directory=str(_BASE_DIR / "static")),   name="static")
app.mount("/uploads/meetings", StaticFiles(directory=str(MEETINGS_DIR)),           name="meetings_files")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.ico")


# ── 헬퍼 ────────────────────────────────────────────────

_HTTPS_CERT_PATH = _RUN_DIR / "whatudoin-cert.pem"
_HTTPS_KEY_PATH  = _RUN_DIR / "whatudoin-key.pem"


def _https_available() -> bool:
    return _HTTPS_CERT_PATH.is_file() and _HTTPS_KEY_PATH.is_file()


def _ctx(request: Request, **kwargs):
    user = auth.get_current_user(request)
    return {
        "request": request,
        "user": user,
        "https_available": _https_available(),
        "https_port": 8443,
        "http_port": 8000,
        **kwargs,
    }


def _require_editor(request: Request):
    user = auth.get_current_user(request)
    if not auth.is_editor(user):
        raise HTTPException(status_code=403, detail="로그인이 필요합니다.")
    return user


def _require_admin(request: Request):
    user = auth.get_current_user(request)
    if not auth.is_admin(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


def _can_read_doc(user, doc: dict) -> bool:
    if not doc:
        return False
    if user and user.get("role") == "admin":
        return True
    if doc.get("is_public"):
        return True
    if not user:
        return False
    if doc.get("created_by") == user["id"]:
        return True
    if doc.get("is_team_doc") and doc.get("team_id") == user.get("team_id"):
        return True
    if not doc.get("is_team_doc") and doc.get("team_share") and doc.get("team_id") == user.get("team_id"):
        return True
    return False


def _can_write_doc(user, doc: dict) -> bool:
    if not user or not doc:
        return False
    if user.get("role") == "admin":
        return True
    if not auth.is_editor(user):
        return False
    if doc.get("is_team_doc"):
        return doc.get("team_id") == user.get("team_id")
    return doc.get("created_by") == user["id"]


def _can_read_checklist(user, cl: dict) -> bool:
    if user and user.get("role") == "admin":
        return True
    if user:
        return True
    is_pub = cl.get("is_public")
    if is_pub == 1:
        return True
    if is_pub is None:
        proj_name = cl.get("project") or ""
        if proj_name:
            proj = db.get_project(proj_name)
            return bool(proj and not proj.get("is_private"))
    return False


# ── 페이지 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if auth.get_current_user(request) is None:
        return RedirectResponse("/kanban", status_code=303)
    teams = db.get_all_teams()
    return templates.TemplateResponse(request, "home.html", _ctx(request, teams=teams))


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
    if auth.get_current_user(request) is None:
        return RedirectResponse("/", status_code=303)
    teams = db.get_all_teams()
    return templates.TemplateResponse(request, "calendar.html", _ctx(request, teams=teams))


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
    return templates.TemplateResponse(request, "admin.html", _ctx(
        request, teams=teams, pending=pending, members=users
    ))


@app.get("/kanban", response_class=HTMLResponse)
def kanban_page(request: Request):
    teams = db.get_all_teams()
    return templates.TemplateResponse(request, "kanban.html", _ctx(request, teams=teams))


@app.get("/gantt", response_class=HTMLResponse)
def project_page(request: Request):
    teams = db.get_all_teams()
    return templates.TemplateResponse(request, "project.html", _ctx(request, teams=teams))


@app.get("/project-manage", response_class=HTMLResponse)
def project_manage_page(request: Request):
    _require_editor(request)
    return templates.TemplateResponse(request, "project_manage.html", _ctx(request))


@app.get("/doc", response_class=HTMLResponse)
def docs_page(request: Request):
    user = auth.get_current_user(request)
    docs = db.get_all_meetings(viewer=user)
    teams = db.get_all_teams()
    return templates.TemplateResponse(request, "doc_list.html", _ctx(
        request, docs=docs, teams=teams,
        default_model=llm_parser.DEFAULT_MODEL,
    ))


@app.get("/doc/new", response_class=HTMLResponse)
def doc_new_page(request: Request):
    user = auth.get_current_user(request)
    if not auth.is_editor(user):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "doc_editor.html", _ctx(request, doc=None, doc_events=[], can_edit=True))


@app.get("/doc/{meeting_id}", response_class=HTMLResponse)
def doc_detail_page(request: Request, meeting_id: int):
    doc = db.get_meeting(meeting_id)
    current_user = auth.get_current_user(request)
    if not _can_read_doc(current_user, doc):
        raise HTTPException(status_code=404)
    events = db.get_events_by_meeting(meeting_id)
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
    return templates.TemplateResponse(request, "doc_editor.html", _ctx(
        request, doc=doc, doc_events=events,
        locked_by=locked_by, lock_type=lock_type, can_edit=can_edit, done_projects=done_projects,
    ))


@app.get("/doc/{meeting_id}/history", response_class=HTMLResponse)
def doc_history_page(request: Request, meeting_id: int):
    doc = db.get_meeting(meeting_id)
    current_user = auth.get_current_user(request)
    if current_user is None or not _can_read_doc(current_user, doc):
        raise HTTPException(status_code=404)
    histories = db.get_meeting_histories(meeting_id)
    return templates.TemplateResponse(request, "doc_history.html", _ctx(
        request, doc=doc, histories=histories
    ))


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
    notice = db.get_latest_notice()
    return templates.TemplateResponse(request, "notice.html", _ctx(request, notice=notice))


@app.get("/notice/history", response_class=HTMLResponse)
def notice_history_page(request: Request):
    histories = db.get_notice_history()
    return templates.TemplateResponse(request, "notice_history.html", _ctx(request, histories=histories))


@app.get("/check", response_class=HTMLResponse)
def check_page(request: Request):
    user = auth.get_current_user(request)
    all_projs = db.get_all_projects_with_events()
    visible = [p for p in all_projs if user or not p.get("is_private", 0)]
    active_projs = [p for p in visible if p.get("is_active", 1)]
    done_projs   = [p for p in visible if not p.get("is_active", 1)]
    return templates.TemplateResponse(request, "check.html",
        _ctx(request, projects=active_projs, done_projects=done_projs))


@app.get("/check/new/edit", response_class=HTMLResponse)
def check_new_page(request: Request, proj: str = ""):
    user = auth.get_current_user(request)
    if not user or user.get("role") not in ("editor", "admin"):
        return RedirectResponse("/check")
    all_projs = db.get_all_projects_with_events()
    projects = [p for p in all_projs if p.get("is_active", 1)]
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
    all_projs = db.get_all_projects_with_events()
    projects = [p for p in all_projs if p.get("is_active", 1)]
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
    histories = db.get_checklist_histories(checklist_id)
    return templates.TemplateResponse(
        request, "check_history.html",
        _ctx(request, checklist=item, histories=histories)
    )


# ── 체크리스트 API ────────────────────────────────────────────

@app.get("/api/checklists")
def list_checklists(request: Request, project: str = None, active: int = None, include_done: int = 0):
    viewer = auth.get_current_user(request)
    active_only = None if active is None else bool(active)
    return db.get_checklists(project=project, viewer=viewer, active_only=active_only, include_done_projects=bool(include_done))


@app.post("/api/checklists")
async def create_checklist(request: Request):
    user = _require_editor(request)
    data = await request.json()
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    project = data.get("project", "").strip()
    content = data.get("content", "").strip()
    is_public = 1 if data.get("is_public") else 0
    cid = db.create_checklist(project, title, content, user["name"], is_public=is_public)
    wu_broker.publish("checks.changed", {"id": cid, "action": "create"})
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
    _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.set_checklist_active(checklist_id, is_active)
    wu_broker.publish("checks.changed", {"id": checklist_id, "action": "update"})
    return {"ok": True, "is_active": is_active}


@app.patch("/api/checklists/bulk-visibility")
async def bulk_checklist_visibility(request: Request):
    _require_editor(request)
    data = await request.json()
    project  = data.get("project")   # None 또는 "" → 미지정, 문자열 → 해당 프로젝트
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    count = db.bulk_update_checklist_visibility(project, is_public)
    return {"ok": True, "updated": count}


@app.patch("/api/events/bulk-visibility")
async def bulk_event_visibility(request: Request):
    _require_editor(request)
    data = await request.json()
    project  = data.get("project")   # None 또는 "" → 미지정, 문자열 → 해당 프로젝트
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    count = db.bulk_update_event_visibility(project, is_public)
    wu_broker.publish("events.changed", {"id": None, "action": "bulk_update", "team_id": None})
    return {"ok": True, "updated": count}


@app.patch("/api/checklists/{checklist_id}/visibility")
async def rotate_checklist_visibility(checklist_id: int, request: Request):
    _require_editor(request)
    cl = db.get_checklist(checklist_id)
    if not cl:
        raise HTTPException(status_code=404)
    body = await request.body()
    data = await request.json() if body else {}
    if "is_public" in data:
        raw = data["is_public"]
        new_pub = None if raw is None else (1 if raw else 0)
    else:
        # legacy toggle (check.html 2-state vis-seg 호환): None→1, 1→0, 0→None
        cur = cl.get("is_public")
        new_pub = 1 if cur is None else (0 if cur == 1 else None)
    db.update_checklist_visibility(checklist_id, new_pub)
    return {"ok": True, "is_public": new_pub}


@app.patch("/api/checklists/{checklist_id}")
async def update_checklist(checklist_id: int, request: Request):
    _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    data = await request.json()
    title = data.get("title", item["title"]).strip()
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    old_proj = (item.get("project") or "").strip()
    project  = data.get("project", item["project"])
    project  = "" if project is None else str(project).strip()
    db.update_checklist(checklist_id, title, project)
    # 프로젝트에서 미지정으로 이동 → 항상 외부 비공개
    if old_proj and not project:
        db.update_checklist_visibility(checklist_id, 0)
    wu_broker.publish("checks.changed", {"id": checklist_id, "action": "update"})
    return {"ok": True}


@app.patch("/api/checklists/{checklist_id}/content")
async def update_checklist_content(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
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
    wu_broker.publish("checks.changed", {"id": checklist_id, "action": "content"})
    return {"ok": True}


@app.get("/api/checklists/{checklist_id}/histories")
def get_checklist_histories(checklist_id: int):
    return db.get_checklist_histories(checklist_id)


@app.post("/api/checklists/{checklist_id}/histories/{history_id}/restore")
async def restore_checklist_history(checklist_id: int, history_id: int, request: Request):
    user = _require_editor(request)
    ok = db.restore_checklist_from_history(checklist_id, history_id, user["name"])
    if not ok:
        raise HTTPException(status_code=404, detail="이력을 찾을 수 없습니다.")
    wu_broker.publish("checks.changed", {"id": checklist_id, "action": "content"})
    return db.get_checklist(checklist_id)


@app.delete("/api/checklists/{checklist_id}")
def delete_checklist(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    db.release_checklist_lock(checklist_id)  # 삭제 시 강제 해제
    db.delete_checklist(checklist_id, deleted_by=user["name"], team_id=user.get("team_id"))
    wu_broker.publish("checks.changed", {"id": checklist_id, "action": "delete"})
    return {"ok": True}


# ── 체크리스트 잠금 API ───────────────────────────────────────

@app.post("/api/checklists/{checklist_id}/lock")
def lock_checklist(checklist_id: int, request: Request):
    user = _require_editor(request)
    tab_token = request.query_params.get("tab_token", "")
    ok = db.acquire_checklist_lock(checklist_id, user["name"], tab_token)
    if not ok:
        lock = db.get_checklist_lock(checklist_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    wu_broker.publish("checks.changed", {"id": checklist_id, "action": "lock"})
    return {"ok": True}


@app.put("/api/checklists/{checklist_id}/lock")
def heartbeat_checklist_lock(checklist_id: int, request: Request):
    _require_editor(request)
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
        tab_token = request.query_params.get("tab_token") or None
        if tab_token:  # tab_token 없으면 no-op — 다른 편집자 잠금 보호
            db.release_checklist_lock(checklist_id, tab_token)
            wu_broker.publish("checks.changed", {"id": checklist_id, "action": "unlock"})
    return {"ok": True}


@app.get("/api/checklists/{checklist_id}/lock")
def get_checklist_lock_status(checklist_id: int, request: Request):
    lock = db.get_checklist_lock(checklist_id)
    if not lock:
        return {"locked_by": None, "lock_type": None}
    user = auth.get_current_user(request)
    lock_type = "self_tab" if (user and lock["user_name"] == user["name"]) else "other_user"
    return {"locked_by": lock["user_name"], "lock_type": lock_type}


@app.patch("/api/checklists/{checklist_id}/is-locked")
async def set_checklist_is_locked(checklist_id: int, request: Request):
    _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    data = await request.json()
    locked = 1 if data.get("locked") else 0
    db.set_checklist_is_locked(checklist_id, locked)
    return {"ok": True, "is_locked": locked}


@app.get("/api/notice")
def api_get_notice():
    return db.get_latest_notice() or {}


@app.post("/api/notice")
async def api_save_notice(request: Request):
    user = _require_editor(request)
    data = await request.json()
    content = data.get("content", "")
    notice_id = db.save_notice(content, user["name"])
    return {"id": notice_id}


@app.post("/api/notice/notify")
async def api_notify_notice(request: Request):
    user = _require_editor(request)
    notice = db.get_latest_notice()
    if not notice:
        return {"ok": False, "reason": "no_notice"}
    content = notice["content"]
    preview = content.replace("#", "").strip()[:40]
    msg = f"📢 팀 공지 업데이트: {preview}…" if len(content.replace("#", "").strip()) > 40 else f"📢 팀 공지 업데이트: {preview}"
    db.create_notification_for_all("notice", msg, exclude_user=user["name"])
    return {"ok": True}


# ── 알림 API ─────────────────────────────────────────────

@app.get("/api/notifications/count")
def get_notification_count(request: Request):
    user = auth.get_current_user(request)
    if not user:
        return {"count": 0}
    return {"count": db.get_notification_count(user["name"])}


@app.get("/api/notifications/pending")
def get_pending_notifications(request: Request):
    user = auth.get_current_user(request)
    if not user:
        return []
    return db.get_pending_notifications(user["name"])


@app.post("/api/notifications/read-all")
def mark_all_notifications_read(request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    db.mark_all_notifications_read(user["name"])
    return {"ok": True}


# ── 인증 API ────────────────────────────────────────────

@app.post("/api/login")
async def login(request: Request, response: Response):
    data = await request.json()
    password = data.get("password", "").strip()
    if not password:
        raise HTTPException(status_code=400, detail="비밀번호를 입력하세요.")
    user = db.get_user_by_password(password)
    if not user:
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
    session_id = db.create_session(user["id"], role=user["role"])
    db.record_ip(user["id"], auth.get_client_ip(request))
    response.set_cookie(auth.SESSION_COOKIE, session_id, httponly=True, max_age=86400 * 30)
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
    existing = db.get_user_by_password(current_pw)
    if not existing or existing["id"] != user["id"]:
        raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다.")
    db.reset_user_password(user["id"], new_pw)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    session_id = request.cookies.get(auth.SESSION_COOKIE)
    if session_id:
        db.delete_session(session_id)
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.post("/api/register")
async def register(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    password = data.get("password", "").strip()
    memo = data.get("memo", "").strip()
    if not name or not password:
        raise HTTPException(status_code=400, detail="이름과 비밀번호를 입력하세요.")
    err = db.check_register_duplicate(name, password)
    if err:
        raise HTTPException(status_code=409, detail=err)
    db.create_pending_user(name, password, memo)
    return {"ok": True}


# ── 관리자 인증 API ──────────────────────────────────────

@app.post("/api/admin/login")
async def admin_login(request: Request, response: Response):
    data = await request.json()
    name = data.get("name", "").strip()
    password = data.get("password", "").strip()
    user = db.get_user_by_credentials(name, password)
    if not user:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    session_id = db.create_session(user["id"], role=user["role"])
    db.record_ip(user["id"], auth.get_client_ip(request))
    response.set_cookie(auth.SESSION_COOKIE, session_id, httponly=True, max_age=300)
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


@app.get("/api/admin/users")
def admin_users(request: Request):
    _require_admin(request)
    return db.get_all_users()


@app.put("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    # 관리자 계정 비활성화 보호
    if not data.get("is_active"):
        user = db.get_user(user_id)
        if user and user.get("role") == "admin":
            raise HTTPException(status_code=400, detail="관리자 계정은 비활성화할 수 없습니다.")
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
    return {"ollama_url": db.get_setting("ollama_url") or llm_parser.OLLAMA_BASE_URL}


@app.put("/api/admin/settings/llm")
async def admin_set_llm_settings(request: Request):
    _require_admin(request)
    data = await request.json()
    url = data.get("ollama_url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL을 입력하세요.")
    db.set_setting("ollama_url", url)
    llm_parser.set_ollama_base_url(url)
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


@app.get("/api/admin/teams")
def admin_teams(request: Request):
    _require_admin(request)
    return db.get_all_teams()


@app.post("/api/admin/teams")
async def admin_create_team(request: Request):
    _require_admin(request)
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="팀 이름을 입력하세요.")
    team_id = db.create_team(name)
    return {"id": team_id, "name": name}


@app.put("/api/admin/teams/{team_id}")
async def admin_update_team(team_id: int, request: Request):
    _require_admin(request)
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="팀 이름을 입력하세요.")
    db.update_team(team_id, name)
    return {"ok": True}


@app.delete("/api/admin/teams/{team_id}")
def admin_delete_team(team_id: int, request: Request):
    _require_admin(request)
    db.delete_team(team_id)
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
    db.toggle_ip_whitelist(ip_id, enable)
    return {"ok": True}


# ── 프로젝트 자동 색상 (간트와 동일 팔레트·해시) ─────────────
_PROJECT_COLOR_PALETTE = [
    '#0984e3', '#00b894', '#00a8a8', '#6c5ce7', '#00875a',
    '#1565c0', '#00838f', '#7c4dff', '#9b59b6', '#5c35a0',
    '#546e7a', '#8d6e63', '#7b5e00', '#d48806', '#e67e22',
]

def _project_color(name: str) -> str:
    h = 0
    for ch in (name or ''):
        h = (h * 31 + ord(ch)) & 0xffff
    return _PROJECT_COLOR_PALETTE[h % len(_PROJECT_COLOR_PALETTE)]


# ── 이벤트 API ───────────────────────────────────────────

@app.get("/api/events")
def list_events():
    events = db.get_all_events()
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
        proj_color = proj_colors.get(proj_name) if proj_name else None

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
        color = proj_color or (proj_name and _project_color(proj_name))
        if color:
            ev["backgroundColor"] = color
            ev["borderColor"]     = color
        result.append(ev)
    return result


@app.get("/api/events/by-project-range")
def events_by_project_range(project: str, start: str, end: str, include_subtasks: int = 0):
    return db.get_events_by_project_range(project, start, end, include_subtasks=bool(include_subtasks))


@app.get("/api/events/search-parent")
def search_parent_events(project: str = "", q: str = "", exclude_id: str = None):
    """하위 업무 모달의 '상위 업무' 오토컴플릿 전용 검색"""
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
            f"SELECT id, title, project, start_datetime, end_datetime FROM events e WHERE {' AND '.join(where)} ORDER BY e.start_datetime LIMIT 30",
            params
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/events/{event_id}/subtasks")
def get_event_subtasks(event_id: int):
    """특정 이벤트의 하위 업무 목록"""
    return db.get_subtasks(event_id)


@app.get("/api/events/{event_id}")
def get_event(event_id: int):
    event = db.get_event(event_id)
    if not event:
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
    data["created_by"] = str(user["id"])
    data["team_id"] = user.get("team_id")
    event_id = db.create_event(data)
    # 일지·하위 업무는 담당자 알림 없음
    if data.get("event_type") not in ("journal", "subtask"):
        assignees = [a.strip() for a in (data.get("assignee") or "").split(",") if a.strip()]
        for name in assignees:
            if name != user["name"]:
                db.create_notification(name, "assigned", f"📌 담당자로 지정됨: {data.get('title','')}", event_id)
    wu_broker.publish("events.changed", {"id": event_id, "action": "create", "team_id": user.get("team_id")})
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

    # 새로 추가된 담당자에게만 알림 (등록자 본인 제외, 일지 제외)
    if data.get("event_type") != "journal":
        new_assignees = set(a.strip() for a in (data.get("assignee") or "").split(",") if a.strip())
        for name in new_assignees - prev_assignees:
            if name != user["name"]:
                db.create_notification(name, "assigned", f"📌 담당자로 지정됨: {data.get('title', event.get('title',''))}", event_id)

    # 반복 3분기(this/from_here/all)·단일 모두 공통 return이므로 여기서 1회 publish
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
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
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
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
    wu_broker.publish("events.changed", {"id": event_id, "action": "delete", "team_id": user.get("team_id")})
    return {"ok": True}


# ── SSE 실시간 스트림 ────────────────────────────────────────
@app.get("/api/stream")
async def sse_stream(request: Request):
    """캘린더·칸반·간트 실시간 동기화용 SSE 엔드포인트.

    - 비로그인 게스트 포함 — 페이로드는 id/action 메타 한정
    - 25초마다 ping 주석으로 프록시·브라우저 타임아웃 방지
    - 클라이언트 연결 종료 시 subscribe한 큐를 자동 해제
    """

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
    return db.get_kanban_events(team_id, viewer=viewer)


@app.get("/api/my-meetings")
def get_my_meetings(request: Request):
    """내 담당 회의 일정 (오늘 이후, 최대 7개)"""
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="로그인이 필요합니다.")
    return db.get_upcoming_meetings(assignee_name=user["name"], limit=5)


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
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
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
    db.update_event_project(event_id, data.get("project"))
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


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
    team_id = user.get("team_id")
    existing = db.get_events_for_conflict_check(team_id)

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
    team_id    = user.get("team_id")

    if not all_events:
        return {"results": []}

    # 서버에서 직접 DB 재조회 후 similar 구간 재계산 (클라이언트 check_results 무시)
    existing = db.get_events_for_conflict_check(team_id)
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
    data = await request.json()
    kwargs = {}
    if "kanban_status" in data:
        kwargs["kanban_status"] = data["kanban_status"]
    if "priority" in data:
        kwargs["priority"] = data["priority"]
    db.update_kanban_status(event_id, **kwargs)
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


@app.get("/api/conflicts")
def check_conflicts(start: str, end: str = None, team_id: int = None, exclude_id: int = None):
    conflicts = db.check_conflicts(start, end or start, team_id, exclude_id)
    return {"conflicts": conflicts}


# ── 프로젝트 ─────────────────────────────────────────────

@app.get("/api/projects")
def list_projects():
    return [p["name"] for p in db.get_unified_project_list()]


@app.get("/api/project-timeline")
def project_timeline(request: Request, team_id: int = None):
    viewer = auth.get_current_user(request)
    return db.get_project_timeline(team_id, viewer=viewer)


# ── 통합 프로젝트 목록 API ──────────────────────────────────

@app.get("/api/project-list")
def api_project_list(request: Request):
    """모든 페이지에서 공통으로 사용하는 통합 프로젝트 목록.
    projects 테이블 + events.project + checklists.project 합산, [{name, color, is_active, id}]
    """
    _require_editor(request)
    return db.get_unified_project_list()


# ── 프로젝트 관리 API ────────────────────────────────────

@app.get("/api/manage/projects")
def manage_list_projects(request: Request):
    _require_editor(request)
    return db.get_all_projects_with_events()


@app.post("/api/manage/projects")
async def manage_create_project(request: Request):
    _require_editor(request)
    data = await request.json()
    name = data.get("name", "").strip()
    color = data.get("color") or None
    memo  = (data.get("memo") or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="프로젝트 이름을 입력하세요.")
    try:
        proj_id = db.create_project(name, color, memo)
    except Exception:
        raise HTTPException(status_code=409, detail="같은 이름의 프로젝트가 이미 있습니다.")
    wu_broker.publish("projects.changed", {"name": name, "action": "create"})
    return {"id": proj_id, "name": name}


@app.put("/api/manage/projects/{name:path}")
async def manage_rename_project(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    new_name = data.get("name", "").strip()
    force    = data.get("force", False)
    if not new_name:
        raise HTTPException(status_code=400, detail="새 이름을 입력하세요.")
    if new_name != name and not force and db.project_name_exists(new_name):
        raise HTTPException(status_code=409, detail=f'"{new_name}" 프로젝트가 이미 존재합니다. 병합하시겠습니까?')
    db.rename_project(name, new_name)
    wu_broker.publish("projects.changed", {"name": new_name, "action": "update"})
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/status")
async def manage_project_status(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.update_project_status(name, is_active)
    wu_broker.publish("projects.changed", {"name": name, "action": "update"})
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/privacy")
async def manage_project_privacy(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    is_private = 1 if data.get("is_private") else 0
    db.update_project_privacy(name, is_private)
    wu_broker.publish("projects.changed", {"name": name, "action": "update"})
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/memo")
async def manage_project_memo(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    db.update_project_memo(name, data.get("memo"))
    wu_broker.publish("projects.changed", {"name": name, "action": "update"})
    return {"ok": True}


@app.get("/api/project-colors")
def project_colors_api():
    """프로젝트명 → 색상 딕셔너리 반환 (색상이 설정된 항목만)"""
    return db.get_project_colors()


@app.patch("/api/manage/projects/{name:path}/color")
async def manage_project_color(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    color = data.get("color", "").strip() or None
    db.update_project_color(name, color)
    wu_broker.publish("projects.changed", {"name": name, "action": "update"})
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/dates")
async def manage_project_dates(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    db.update_project_dates(name, data.get("start_date"), data.get("end_date"))
    wu_broker.publish("projects.changed", {"name": name, "action": "update"})
    return {"ok": True}


@app.delete("/api/manage/projects/{name:path}")
async def manage_delete_project(name: str, request: Request):
    user = _require_editor(request)
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    delete_events = data.get("delete_events", False)
    db.delete_project(name, delete_events, deleted_by=user["name"], team_id=user.get("team_id"))
    # 프로젝트 삭제는 이벤트에도 영향 → 두 채널 모두 publish
    wu_broker.publish("projects.changed", {"name": name, "action": "delete"})
    wu_broker.publish("events.changed", {"id": None, "action": "bulk_update", "team_id": user.get("team_id")})
    return {"ok": True}


_IMG_URL_RE = re.compile(r'/uploads/meetings/\d{4}/\d{2}/[\w\-.]+')


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
        rewritten, found = _rewrite_image_paths(raw)
        if images is not None:
            images.extend(found)
        lines.append(rewritten)
    else:
        lines.append("_(내용 없음)_")
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


def _rewrite_image_paths(content: str) -> tuple[str, list[tuple[Path, str]]]:
    """content 내 /uploads/meetings/… URL을 ../attachments/{basename} 로 치환.
    Returns (rewritten_content, [(disk_path, zip_archive_path), ...])"""
    collected: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def _repl(m: re.Match) -> str:
        url = m.group(0)
        basename = url.rsplit("/", 1)[-1]
        if basename not in seen:
            seen.add(basename)
            rel = url.replace("/uploads/meetings/", "", 1)
            disk = MEETINGS_DIR / rel
            collected.append((disk, f"attachments/{basename}"))
        return f"../attachments/{basename}"

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
        rewritten, found = _rewrite_image_paths(raw)
        if images is not None:
            images.extend(found)
        lines.append(rewritten)
    else:
        lines.append("_(내용 없음)_")
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
    _require_editor(request)
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
    _require_editor(request)
    ev = db.get_event(event_id)
    if not ev or ev.get("deleted_at"):
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
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
    data = await request.json()
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
        "team_id":        user.get("team_id"),
    }
    event_id = db.create_event(payload)
    wu_broker.publish("events.changed", {"id": event_id, "action": "create", "team_id": user.get("team_id")})
    return {"id": event_id}


@app.put("/api/manage/events/{event_id}")
async def manage_update_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
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
    db.update_event(event_id, updated)
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}


@app.patch("/api/manage/events/{event_id}/status")
async def manage_event_status(event_id: int, request: Request):
    user = _require_editor(request)
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.update_event_active_status(event_id, is_active)
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True}



@app.patch("/api/events/{event_id}/visibility")
async def update_event_visibility_api(event_id: int, request: Request):
    user = _require_editor(request)
    body = await request.body()
    data = await request.json() if body else {}
    # is_public: null=프로젝트 연동, 0=비공개, 1=공개
    if "is_public" in data:
        raw = data["is_public"]
        is_public = None if raw is None else (1 if raw else 0)
    else:
        # 값 없으면 서버에서 cycling: None→1→0→None
        ev = db.get_event(event_id)
        cur = ev.get("is_public") if ev else None
        is_public = 1 if cur is None else (0 if cur == 1 else None)
    db.update_event_visibility(event_id, is_public)
    wu_broker.publish("events.changed", {"id": event_id, "action": "update", "team_id": user.get("team_id")})
    return {"ok": True, "is_public": is_public}


@app.delete("/api/manage/events/{event_id}")
def manage_delete_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    db.delete_event(event_id, deleted_by=user["name"], team_id=user.get("team_id"))
    # sync 핸들러 → broker 내부의 call_soon_threadsafe가 루프 스레드로 안전 전달
    wu_broker.publish("events.changed", {"id": event_id, "action": "delete", "team_id": user.get("team_id")})
    return {"ok": True}


@app.get("/api/members")
def list_members():
    users = db.get_all_users()
    return [u["name"] for u in users if u.get("is_active") and u.get("role") != "admin"]


# ── 링크 API ─────────────────────────────────────────────

@app.get("/api/links")
def api_get_links(request: Request):
    user = auth.get_current_user(request)
    if not user:
        return []
    return db.get_links(user["name"], user.get("team_id"))


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
    if scope not in ("personal", "team"):
        scope = "personal"
    team_id = user.get("team_id") if scope == "team" else None
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
    ok = db.update_link(link_id, title, url, desc, user["name"])
    if not ok:
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    return {"ok": True}


@app.delete("/api/links/{link_id}")
def api_delete_link(link_id: int, request: Request):
    user = _require_editor(request)
    ok = db.delete_link(link_id, user["name"], user.get("role", "editor"))
    if not ok:
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
    return {"ok": True}


# ── 문서 API ─────────────────────────────────────────────

@app.get("/api/doc")
def list_docs(request: Request):
    user = auth.get_current_user(request)
    return db.get_all_meetings(viewer=user)


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
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    meeting_id = db.create_meeting(
        title, content, user.get("team_id"), user["id"],
        meeting_date, is_team_doc, is_public, team_share
    )
    wu_broker.publish("docs.changed", {"action": "create", "id": meeting_id})
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
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    old_title = (doc.get("title") or "").strip()
    db.update_meeting(meeting_id, title, content, user["id"], meeting_date, is_team_doc, is_public, team_share)
    if title != old_title:
        wu_broker.publish("docs.changed", {"action": "update", "id": meeting_id})
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
    else:
        if   (is_pub, t_share) == (0, 0): new_pub, new_share = 0, 1
        elif (is_pub, t_share) == (0, 1): new_pub, new_share = 1, 0
        else:                              new_pub, new_share = 0, 0
    db.update_meeting_visibility(meeting_id, is_team, new_pub, new_share)
    wu_broker.publish("docs.changed", {"action": "update", "id": meeting_id})
    return {"ok": True, "is_team_doc": is_team, "is_public": new_pub, "team_share": new_share}


# ── 문서 편집 잠금 ────────────────────────────────────────
@app.post("/api/doc/{meeting_id}/lock")
def lock_doc(meeting_id: int, request: Request):
    user = _require_editor(request)
    tab_token = request.query_params.get("tab_token", "")
    ok = db.acquire_meeting_lock(meeting_id, user["name"], tab_token)
    if not ok:
        lock = db.get_meeting_lock(meeting_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    return {"ok": True}


@app.put("/api/doc/{meeting_id}/lock")
def heartbeat_doc_lock(meeting_id: int, request: Request):
    _require_editor(request)
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
        tab_token = request.query_params.get("tab_token") or None
        if tab_token:  # tab_token 없으면 no-op — 다른 편집자 잠금 보호
            db.release_meeting_lock(meeting_id, tab_token)
    return {"ok": True}


@app.get("/api/doc/{meeting_id}/lock")
def get_doc_lock(meeting_id: int, request: Request):
    lock = db.get_meeting_lock(meeting_id)
    if not lock:
        return {"locked_by": None, "lock_type": None}
    user = auth.get_current_user(request)
    lock_type = "self_tab" if (user and lock["user_name"] == user["name"]) else "other_user"
    return {"locked_by": lock["user_name"], "lock_type": lock_type}


@app.get("/api/doc/calendar")
def docs_calendar(request: Request):
    user = auth.get_current_user(request)
    if user is None:
        return []
    docs = db.get_all_meetings(viewer=user)
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


@app.post("/api/upload/image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """회의록 이미지 업로드 — meetings/{year}/{month}/{uuid}.ext 로 저장"""
    _require_editor(request)
    ext = Path(file.filename).suffix.lower() if file.filename else ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        ext = ".png"
    now = datetime.now()
    folder = MEETINGS_DIR / str(now.year) / f"{now.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    (folder / filename).write_bytes(await file.read())
    return {"url": f"/uploads/meetings/{now.year}/{now.month:02d}/{filename}"}


def _delete_meeting_images(content: str):
    """마크다운 content에서 /uploads/meetings/… 경로 이미지를 찾아 파일 삭제"""
    for url in re.findall(r'/uploads/meetings/\d{4}/\d{2}/[\w\-.]+', content or ""):
        # URL: /uploads/meetings/2026/04/abc.png → 실제 파일: meetings/2026/04/abc.png
        p = Path(url.replace("/uploads/", "", 1))
        if p.exists():
            p.unlink()


@app.delete("/api/doc/{meeting_id}")
def delete_doc(meeting_id: int, request: Request):
    user = _require_editor(request)
    doc = db.get_meeting(meeting_id)
    if not doc:
        raise HTTPException(status_code=404)
    if not _can_write_doc(user, doc):
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
    db.delete_meeting(meeting_id, deleted_by=user["name"])
    wu_broker.publish("docs.changed", {"action": "delete", "id": meeting_id})
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
    return db.get_events_by_meeting(meeting_id)


# ── AI 파싱 ──────────────────────────────────────────────

@app.post("/api/ai/parse")
async def ai_parse(request: Request):
    body = await request.json()
    text = body.get("text", "").strip()
    model = body.get("model", llm_parser.DEFAULT_MODEL)
    if not text:
        raise HTTPException(status_code=400, detail="텍스트를 입력하세요.")
    try:
        events = await run_in_threadpool(llm_parser.parse_schedule, text, model)
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
    team_id    = user.get("team_id")

    # 저장 직전 재검사 — force=True가 아닌 경우만
    if not force:
        existing = db.get_events_for_conflict_check(team_id)
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
        wu_broker.publish("events.changed", {"id": None, "action": "bulk_create", "team_id": team_id})
    return {"saved": saved, "created": created, "blocked": [], "skipped": skipped, "requires_force": False}


@app.post("/api/ai/refine")
async def ai_refine(request: Request):
    """2차 AI: 검토자 — 1차 추출 결과를 원본 텍스트와 함께 재검토."""
    body = await request.json()
    text   = body.get("text", "").strip()
    events = body.get("events", [])
    model  = body.get("model", llm_parser.DEFAULT_MODEL)
    if not text:
        raise HTTPException(status_code=400, detail="텍스트를 입력하세요.")
    try:
        refined = await run_in_threadpool(llm_parser.refine_schedule, text, events, model)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")
    return {"events": refined}


@app.get("/api/ai/models")
def ai_models():
    models, ok = llm_parser.get_available_models_with_status()
    if not ok:
        raise HTTPException(status_code=502, detail=f"Ollama 서버({llm_parser.OLLAMA_BASE_URL})에 연결할 수 없습니다.")
    return {"models": models}


@app.post("/api/ai/generate-event-checklist")
async def ai_generate_event_checklist(request: Request):
    _require_editor(request)
    body = await request.json()
    event_ids = body.get("event_ids", [])
    model = body.get("model", llm_parser.DEFAULT_MODEL)
    project = body.get("project", "")

    events = [db.get_event(eid) for eid in event_ids]
    events = [e for e in events if e]

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

    # P2-1: 요청된 team_id가 본인 팀이 아니면 본인 팀으로 강제 (admin 제외)
    requested_team_id = body.get("team_id") or None
    if auth.is_admin(user):
        team_id = requested_team_id
    else:
        team_id = user.get("team_id") or None

    base_dt      = _dt.strptime(base_date, "%Y-%m-%d")
    past_start   = (base_dt - _td(days=7)).strftime("%Y-%m-%d")
    past_end     = base_date
    future_start = (base_dt + _td(days=1)).strftime("%Y-%m-%d")
    future_end   = (base_dt + _td(days=6)).strftime("%Y-%m-%d")

    # 겹침 쿼리로 변경됐으므로 7일 이전 시작 이벤트도 포함됨
    past_events   = db.get_events_by_date_range(past_start, past_end,   team_id)
    future_events = db.get_events_by_date_range(future_start, future_end, team_id)

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
    checklists = db.get_checklists_by_date_range(past_start, past_end)

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
    return templates.TemplateResponse(request, "trash.html", _ctx(request))


@app.get("/api/trash")
def api_get_trash(request: Request):
    user = _require_editor(request)
    team_id = user.get("team_id")
    return db.get_trash_items(team_id)


@app.post("/api/trash/{item_type}/{item_id}/restore")
def api_restore_trash(item_type: str, item_id: int, request: Request):
    user = _require_editor(request)
    if item_type not in ("event", "meeting", "checklist", "project"):
        raise HTTPException(status_code=400, detail="잘못된 항목 타입입니다.")
    if user.get("role") != "admin":
        item_team = db.get_trash_item_team(item_type, item_id)
        if item_team is None or item_team != user.get("team_id"):
            raise HTTPException(status_code=403, detail="권한이 없습니다.")
    ok = db.restore_trash_item(item_type, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    # sync 핸들러 → broker 내부의 call_soon_threadsafe가 루프 스레드로 안전 전달
    # event/project 복원 시에만 관련 채널로 publish
    if item_type == "event":
        wu_broker.publish("events.changed", {"id": item_id, "action": "update", "team_id": user.get("team_id")})
    elif item_type == "project":
        wu_broker.publish("projects.changed", {"name": None, "action": "update"})
        wu_broker.publish("events.changed", {"id": None, "action": "bulk_update", "team_id": user.get("team_id")})
    return {"ok": True}


# ── AVR (WUDeskop 원격 데스크톱 연동) ────────────────────────────────────────

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
        wudeskop_url = crypto.decrypt(avr_url_enc)
        wudeskop_secret = crypto.decrypt(avr_secret_enc)
        resp = _requests.post(
            f"{wudeskop_url}/api/issue-token",
            json={"secret": wudeskop_secret},
            timeout=3,
        )
        resp.raise_for_status()
        token = resp.json()["token"]
        viewer_url = f"{wudeskop_url}/viewer?token={token}"
    except Exception as e:
        return templates.TemplateResponse(
            request, "avr.html",
            _ctx(request, viewer_url=None, error=f"WUDeskop 연결 실패: {e}"),
        )
    return templates.TemplateResponse(
        request, "avr.html",
        _ctx(request, viewer_url=viewer_url, error=None),
    )


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
