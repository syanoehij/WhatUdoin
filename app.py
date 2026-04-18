from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
import os
import re
import uuid

import requests as _requests

import io
import zipfile
from urllib.parse import quote

from fastapi import FastAPI, Request, HTTPException, Response, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import database as db
import llm_parser
import auth
import crypto

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
    saved_url = db.get_setting("ollama_url")
    if saved_url:
        llm_parser.set_ollama_base_url(saved_url)
    if not scheduler.running:
        # APScheduler: 1분마다 15분 후 일정 알람 체크
        scheduler.add_job(db.check_upcoming_event_alarms, "interval", minutes=1)
        # APScheduler: 매일 새벽 3시 휴지통 30일 초과 항목 정리
        scheduler.add_job(db.cleanup_old_trash, "cron", hour=3, minute=0)
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


def _can_read_meeting(user, meeting: dict) -> bool:
    if not meeting:
        return False
    if user and user.get("role") == "admin":
        return True
    if meeting.get("is_public"):
        return True
    if not user:
        return False
    if meeting.get("created_by") == user["id"]:
        return True
    if meeting.get("is_team_doc") and meeting.get("team_id") == user.get("team_id"):
        return True
    if not meeting.get("is_team_doc") and meeting.get("team_share") and meeting.get("team_id") == user.get("team_id"):
        return True
    return False


def _can_write_meeting(user, meeting: dict) -> bool:
    if not user or not meeting:
        return False
    if user.get("role") == "admin":
        return True
    if not auth.is_editor(user):
        return False
    if meeting.get("is_team_doc"):
        return meeting.get("team_id") == user.get("team_id")
    return meeting.get("created_by") == user["id"]


# ── 페이지 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    teams = db.get_all_teams()
    return templates.TemplateResponse(request, "home.html", _ctx(request, teams=teams))


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
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


@app.get("/memo", response_class=HTMLResponse)
def meetings_page(request: Request):
    user = auth.get_current_user(request)
    meetings = db.get_all_meetings(viewer=user)
    teams    = db.get_all_teams()
    return templates.TemplateResponse(request, "meeting_list.html", _ctx(
        request, meetings=meetings, teams=teams,
        default_model=llm_parser.DEFAULT_MODEL,
    ))


@app.get("/memo/new", response_class=HTMLResponse)
def meeting_new_page(request: Request):
    user = auth.get_current_user(request)
    if not auth.is_editor(user):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "meeting_editor.html", _ctx(request, meeting=None, meeting_events=[], can_edit=True))


@app.get("/memo/{meeting_id}", response_class=HTMLResponse)
def meeting_detail_page(request: Request, meeting_id: int):
    meeting = db.get_meeting(meeting_id)
    current_user = auth.get_current_user(request)
    if not _can_read_meeting(current_user, meeting):
        raise HTTPException(status_code=404)
    events = db.get_events_by_meeting(meeting_id)
    can_edit = _can_write_meeting(current_user, meeting)
    lock = db.get_meeting_lock(meeting_id)
    locked_by = None
    if lock and current_user and lock["user_name"] != current_user["name"]:
        locked_by = lock["user_name"]
    return templates.TemplateResponse(request, "meeting_editor.html", _ctx(
        request, meeting=meeting, meeting_events=events,
        locked_by=locked_by, can_edit=can_edit
    ))


@app.get("/memo/{meeting_id}/history", response_class=HTMLResponse)
def meeting_history_page(request: Request, meeting_id: int):
    meeting = db.get_meeting(meeting_id)
    current_user = auth.get_current_user(request)
    if current_user is None or not _can_read_meeting(current_user, meeting):
        raise HTTPException(status_code=404)
    histories = db.get_meeting_histories(meeting_id)
    return templates.TemplateResponse(request, "meeting_history.html", _ctx(
        request, meeting=meeting, histories=histories
    ))


@app.get("/ai-import", response_class=HTMLResponse)
def ai_import_page(request: Request):
    return templates.TemplateResponse(request, "ai_import.html", _ctx(request))


# ── 변경 이력 페이지 ──────────────────────────────────────
@app.get("/changelog", response_class=HTMLResponse)
def changelog_page(request: Request):
    return templates.TemplateResponse(request, "changelog.html", _ctx(request))


# ── 알람 설정 (인증서 다운로드) 페이지 ─────────────────────
@app.get("/alarm-setup", response_class=HTMLResponse)
def alarm_setup_page(request: Request):
    cert_path = _RUN_DIR / "whatudoin-rootCA.pem"
    return templates.TemplateResponse(
        request, "alarm_setup.html",
        _ctx(request, cert_ready=cert_path.is_file()),
    )


@app.get("/api/cert/rootCA.crt")
def download_rootca():
    cert_path = _RUN_DIR / "whatudoin-rootCA.pem"
    if not cert_path.is_file():
        raise HTTPException(status_code=404, detail="루트 인증서가 아직 준비되지 않았습니다.")
    filename = "WhatUdoin-인증서.crt"
    return FileResponse(
        cert_path,
        media_type="application/x-x509-ca-cert",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    all_projs = db.get_all_projects_with_events()
    projects = [p for p in all_projs if p.get("is_active", 1)]
    return templates.TemplateResponse(request, "check.html", _ctx(request, projects=projects))


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
    locked_by = lock["user_name"] if lock else None
    return templates.TemplateResponse(
        request, "check_editor.html",
        _ctx(request, checklist=item, locked_by=locked_by, projects=projects)
    )


@app.get("/check/{checklist_id}/history", response_class=HTMLResponse)
def check_history_page(request: Request, checklist_id: int):
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
def list_checklists(project: str = None):
    return db.get_checklists(project=project)


@app.post("/api/checklists")
async def create_checklist(request: Request):
    user = _require_editor(request)
    data = await request.json()
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    project = data.get("project", "").strip()
    content = data.get("content", "").strip()
    cid = db.create_checklist(project, title, content, user["name"])
    return {"id": cid}


@app.get("/api/checklists/{checklist_id}")
def get_checklist(checklist_id: int):
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    return item


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
    project = data.get("project", item["project"]).strip()
    db.update_checklist(checklist_id, title, project)
    return {"ok": True}


@app.patch("/api/checklists/{checklist_id}/content")
async def update_checklist_content(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    data = await request.json()
    content = data.get("content", "")
    save_history = data.get("save_history", True)
    db.update_checklist_content(checklist_id, content, user["name"], save_history=save_history)
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
    return db.get_checklist(checklist_id)


@app.delete("/api/checklists/{checklist_id}")
def delete_checklist(checklist_id: int, request: Request):
    user = _require_editor(request)
    item = db.get_checklist(checklist_id)
    if not item:
        raise HTTPException(status_code=404)
    db.release_checklist_lock(checklist_id, user["name"])
    db.delete_checklist(checklist_id, deleted_by=user["name"], team_id=user.get("team_id"))
    return {"ok": True}


# ── 체크리스트 잠금 API ───────────────────────────────────────

@app.post("/api/checklists/{checklist_id}/lock")
def lock_checklist(checklist_id: int, request: Request):
    user = _require_editor(request)
    ok = db.acquire_checklist_lock(checklist_id, user["name"])
    if not ok:
        lock = db.get_checklist_lock(checklist_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    return {"ok": True}


@app.put("/api/checklists/{checklist_id}/lock")
def heartbeat_checklist_lock(checklist_id: int, request: Request):
    user = _require_editor(request)
    db.heartbeat_checklist_lock(checklist_id, user["name"])
    return {"ok": True}


@app.delete("/api/checklists/{checklist_id}/lock")
def unlock_checklist(checklist_id: int, request: Request):
    user = auth.get_current_user(request)
    if user:
        db.release_checklist_lock(checklist_id, user["name"])
    return {"ok": True}


@app.get("/api/checklists/{checklist_id}/lock")
def get_checklist_lock_status(checklist_id: int):
    lock = db.get_checklist_lock(checklist_id)
    return {"locked_by": lock["user_name"] if lock else None}


@app.get("/api/notice")
def api_get_notice():
    return db.get_latest_notice() or {}


@app.post("/api/notice")
async def api_save_notice(request: Request):
    user = _require_editor(request)
    data = await request.json()
    content = data.get("content", "")
    notice_id = db.save_notice(content, user["name"])
    # 팀 공지 저장 시 전체 유저 알림 (작성자 제외)
    preview = content.replace("#", "").strip()[:40]
    db.create_notification_for_all(
        "notice",
        f"📢 팀 공지 업데이트: {preview}…" if len(content) > 40 else f"📢 팀 공지 업데이트: {preview}",
        exclude_user=user["name"]
    )
    return {"id": notice_id}


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


# ── 이벤트 API ───────────────────────────────────────────

@app.get("/api/events")
def list_events():
    events = db.get_all_events()
    proj_colors = db.get_project_colors()
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

        ev = {
            "id": e["id"],
            "title": e["title"],
            "start": e["start_datetime"],
            "end": ev_end,
            "allDay": is_all_day,
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
                "event_type":            e.get("event_type", "schedule"),
                "recurrence_rule":       e.get("recurrence_rule"),
                "recurrence_parent_id":  e.get("recurrence_parent_id"),
            },
        }
        if proj_color:
            ev["backgroundColor"] = proj_color
            ev["borderColor"]     = proj_color
        result.append(ev)
    return result


@app.get("/api/events/{event_id}")
def get_event(event_id: int):
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


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
    data["created_by"] = str(user["id"])
    data["team_id"] = user.get("team_id")
    event_id = db.create_event(data)
    # 담당자 지정 알림 (등록자 본인 제외)
    assignees = [a.strip() for a in (data.get("assignee") or "").split(",") if a.strip()]
    for name in assignees:
        if name != user["name"]:
            db.create_notification(name, "assigned", f"📌 일정 담당자로 지정됨: {data.get('title','')}", event_id)
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

    # 새로 추가된 담당자에게만 알림 (등록자 본인 제외)
    new_assignees = set(a.strip() for a in (data.get("assignee") or "").split(",") if a.strip())
    for name in new_assignees - prev_assignees:
        if name != user["name"]:
            db.create_notification(name, "assigned", f"📌 일정 담당자로 지정됨: {data.get('title', event.get('title',''))}", event_id)

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
    return {"ok": True}


@app.get("/api/kanban")
def get_kanban_events(team_id: int = None):
    return db.get_kanban_events(team_id)


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
    return {"ok": True}


@app.post("/api/events/check-conflicts")
async def check_event_conflicts(request: Request):
    """AI 파싱 결과와 기존 일정의 중복 여부를 검사합니다."""
    from datetime import datetime as _dt
    _require_editor(request)
    data = await request.json()
    candidates = data.get("events", [])
    existing = db.get_events_for_conflict_check()

    results = []
    for cand in candidates:
        cand_title = (cand.get("title") or "").strip().lower()
        cand_date  = (cand.get("date") or "")[:10]

        conflict = None
        for ex in existing:
            ex_date  = (ex.get("start_datetime") or "")[:10]
            ex_title = (ex.get("title") or "").strip().lower()

            if not cand_date or not ex_date:
                continue

            # 날짜 차이 계산 (AI 파싱 오차 ±1일 허용)
            try:
                date_diff = abs((_dt.strptime(cand_date, "%Y-%m-%d") - _dt.strptime(ex_date, "%Y-%m-%d")).days)
            except ValueError:
                continue

            # 제목 일치: ±1일 이내
            if date_diff <= 1 and cand_title and ex_title == cand_title:
                cand_assignee = (cand.get("assignee") or "").strip()
                ex_assignee   = (ex.get("assignee") or "").strip()
                # 양쪽 담당자가 모두 존재하고 다르면 별개 일정으로 취급 — 중복 아님
                if cand_assignee and ex_assignee and cand_assignee != ex_assignee:
                    continue
                conflict = {"type": "exact", "existing_id": ex["id"], "existing_title": ex["title"]}
                break

            # 부분 포함: 같은 날짜만 + 담당자도 같을 때만
            if date_diff == 0 and cand_title and ex_title and len(cand_title) >= 2 and (
                cand_title in ex_title or ex_title in cand_title
            ):
                cand_assignee = (cand.get("assignee") or "").strip()
                ex_assignee   = (ex.get("assignee") or "").strip()
                # 담당자가 다르면 별개 일정 — 중복 아님
                if cand_assignee and ex_assignee and cand_assignee != ex_assignee:
                    continue
                conflict = {"type": "similar", "existing_id": ex["id"], "existing_title": ex["title"]}
                break

        results.append({"conflict": conflict})

    return {"results": results}


@app.post("/api/events/ai-conflict-review")
async def ai_conflict_review(request: Request):
    """신규 일정 전체를 기존 일정과 비교해 AI가 중복 여부를 최종 판단합니다."""
    _require_editor(request)
    data = await request.json()
    candidates = data.get("events", [])   # [{title, date, assignee}]
    model      = data.get("model", llm_parser.DEFAULT_MODEL)
    if not candidates:
        return {"results": []}

    # AI 검토용 기존 일정: 넓은 풀 사용 (날짜 오차 대응)
    existing = db.get_events_for_conflict_check()

    results = llm_parser.review_all_conflicts(candidates, existing, model)
    return {"results": results}


@app.patch("/api/events/{event_id}/kanban")
async def update_event_kanban(event_id: int, request: Request):
    _require_editor(request)
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
def project_timeline(team_id: int = None):
    return db.get_project_timeline(team_id)


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
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/status")
async def manage_project_status(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.update_project_status(name, is_active)
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/memo")
async def manage_project_memo(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    db.update_project_memo(name, data.get("memo"))
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
    return {"ok": True}


@app.patch("/api/manage/projects/{name:path}/dates")
async def manage_project_dates(name: str, request: Request):
    _require_editor(request)
    data = await request.json()
    db.update_project_dates(name, data.get("start_date"), data.get("end_date"))
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


def _build_index_md(name: str, proj: dict | None, events: list[dict], checklists: list[dict],
                    event_files: list[str], checklist_files: list[str], exported_at: str) -> str:
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
    lines.append(f"## ✅ 체크리스트 ({len(checklists)}개)")
    lines.append("")
    if checklist_files:
        for stem in checklist_files:
            lines.append(f"- [[체크리스트/{stem}]]")
    else:
        lines.append("_연결된 체크리스트 없음_")
    lines.append("")
    return "\n".join(lines)


def _build_event_md(project_name: str, ev: dict, exported_at: str) -> str:
    title = _esc(ev.get("title")) or "(제목 없음)"
    lines = ["---", "type: event", f"project: {_yaml_str(project_name)}", f"title: {_yaml_str(title)}"]
    if ev.get("start_datetime"): lines.append(f"start: {_esc(ev.get('start_datetime'))}")
    if ev.get("end_datetime"):   lines.append(f"end: {_esc(ev.get('end_datetime'))}")
    if ev.get("assignee"):       lines.append(f"assignee: {_yaml_str(ev.get('assignee'))}")
    if ev.get("priority"):       lines.append(f"priority: {_yaml_str(ev.get('priority'))}")
    if ev.get("location"):       lines.append(f"location: {_yaml_str(ev.get('location'))}")
    status = "완료" if ev.get("is_active") == 0 else "진행 중"
    lines.append(f"status: {_yaml_str(status)}")
    lines += [f"exported_at: {exported_at}", "tags: [whatudoin, event]", "---", "",
              f"# {title}", "",
              f"← [[index]]", ""]
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


def _build_checklist_md(project_name: str, cl: dict, exported_at: str,
                        images: list | None = None) -> str:
    title = _esc(cl.get("title")) or "(제목 없음)"
    updated = _esc(cl.get("updated_at"))
    lines = ["---", "type: checklist", f"project: {_yaml_str(project_name)}", f"title: {_yaml_str(title)}"]
    if updated:
        lines.append(f"updated_at: {updated[:19].replace('T', ' ')}")
    lines += [f"exported_at: {exported_at}", "tags: [whatudoin, checklist]", "---", "",
              f"# {title}", "",
              f"← [[index]]", ""]
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


def _build_project_zip(name: str) -> bytes:
    proj = db.get_project(name)
    events = db.get_events_by_project(name)
    cl_metas = db.get_checklists(project=name)
    checklists = [db.get_checklist(c["id"]) for c in cl_metas]
    checklists = [c for c in checklists if c]

    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    root = _safe_filename(name, "project")

    event_entries = []
    used_ev_stems: set[str] = set()
    for ev in events:
        stem = _uniq_filename(_safe_filename(ev.get("title") or "일정", "일정"), used_ev_stems)
        event_entries.append((stem, ev))

    cl_entries = []
    used_cl_stems: set[str] = set()
    for cl in checklists:
        stem = _uniq_filename(_safe_filename(cl.get("title") or "체크리스트", "체크리스트"), used_cl_stems)
        cl_entries.append((stem, cl))

    images: list[tuple[Path, str]] = []
    cl_mds = [(stem, _build_checklist_md(name, cl, exported_at, images)) for stem, cl in cl_entries]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        index_md = _build_index_md(
            name, proj, events, checklists,
            [s for s, _ in event_entries],
            [s for s, _ in cl_entries],
            exported_at,
        )
        zf.writestr(f"{root}/index.md", index_md)
        for stem, ev in event_entries:
            zf.writestr(f"{root}/일정/{stem}.md", _build_event_md(name, ev, exported_at))
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
    disposition = f"attachment; filename=\"{quote(filename)}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


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
    db.update_event(event_id, updated)
    return {"ok": True}


@app.patch("/api/manage/events/{event_id}/status")
async def manage_event_status(event_id: int, request: Request):
    _require_editor(request)
    data = await request.json()
    is_active = 1 if data.get("is_active", True) else 0
    db.update_event_active_status(event_id, is_active)
    return {"ok": True}


@app.patch("/api/manage/events/{event_id}/kanban-hidden")
async def manage_event_kanban_hidden(event_id: int, request: Request):
    _require_editor(request)
    data = await request.json()
    hidden = bool(data.get("hidden", False))
    db.update_event_kanban_hidden(event_id, hidden)
    return {"ok": True}


@app.delete("/api/manage/events/{event_id}")
def manage_delete_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    db.delete_event(event_id, deleted_by=user["name"], team_id=user.get("team_id"))
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


# ── 회의록 API ───────────────────────────────────────────

@app.get("/api/memo")
def list_meetings(request: Request):
    user = auth.get_current_user(request)
    return db.get_all_meetings(viewer=user)


@app.post("/api/memo")
async def create_meeting(request: Request):
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
    return {"id": meeting_id}


@app.put("/api/memo/{meeting_id}")
async def update_meeting(meeting_id: int, request: Request):
    user = _require_editor(request)
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404)
    if not _can_write_meeting(user, meeting):
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
    db.update_meeting(meeting_id, title, content, user["id"], meeting_date, is_team_doc, is_public, team_share)
    # 저장 완료 시 잠금 해제
    db.release_meeting_lock(meeting_id, user["name"])
    return {"ok": True}


# ── 회의록 편집 잠금 ────────────────────────────────────────
@app.post("/api/memo/{meeting_id}/lock")
def lock_meeting(meeting_id: int, request: Request):
    user = _require_editor(request)
    ok = db.acquire_meeting_lock(meeting_id, user["name"])
    if not ok:
        lock = db.get_meeting_lock(meeting_id)
        locked_by = lock["user_name"] if lock else "알 수 없음"
        raise HTTPException(status_code=423, detail=f"{locked_by}님이 편집 중입니다.")
    return {"ok": True}


@app.put("/api/memo/{meeting_id}/lock")
def heartbeat_meeting_lock(meeting_id: int, request: Request):
    user = _require_editor(request)
    db.heartbeat_meeting_lock(meeting_id, user["name"])
    return {"ok": True}


@app.delete("/api/memo/{meeting_id}/lock")
def unlock_meeting(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    if user:
        db.release_meeting_lock(meeting_id, user["name"])
    return {"ok": True}


@app.get("/api/memo/{meeting_id}/lock")
def get_meeting_lock(meeting_id: int):
    lock = db.get_meeting_lock(meeting_id)
    return {"locked_by": lock["user_name"] if lock else None}


@app.get("/api/memo/calendar")
def meetings_calendar(request: Request):
    user = auth.get_current_user(request)
    if user is None:
        return []
    meetings = db.get_all_meetings(viewer=user)
    result = []
    for m in meetings:
        if not m.get("is_team_doc", 1):  # 개인 문서는 캘린더 미노출
            continue
        date = m.get("meeting_date") or m["created_at"][:10]
        result.append({
            "id": f"meeting-{m['id']}",
            "title": f"📋 {m['title']}",
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


@app.delete("/api/memo/{meeting_id}")
def delete_meeting(meeting_id: int, request: Request):
    user = _require_editor(request)
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404)
    if not _can_write_meeting(user, meeting):
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
    db.delete_meeting(meeting_id, deleted_by=user["name"])
    return {"ok": True}


@app.get("/api/memo/{meeting_id}/histories")
def meeting_histories(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    meeting = db.get_meeting(meeting_id)
    if not _can_read_meeting(user, meeting):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return db.get_meeting_histories(meeting_id)


@app.post("/api/memo/{meeting_id}/histories/{history_id}/restore")
def restore_meeting_history(meeting_id: int, history_id: int, request: Request):
    user = _require_editor(request)
    meeting = db.get_meeting(meeting_id)
    if not _can_write_meeting(user, meeting):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    ok = db.restore_meeting_from_history(meeting_id, history_id, user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="이력을 찾을 수 없습니다.")
    return {"ok": True}


@app.get("/api/memo/{meeting_id}/events")
def meeting_events_api(meeting_id: int, request: Request):
    user = auth.get_current_user(request)
    meeting = db.get_meeting(meeting_id)
    if not _can_read_meeting(user, meeting):
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
        events = llm_parser.parse_schedule(text, model)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")
    return {"events": events}


@app.post("/api/ai/confirm")
async def ai_confirm(request: Request):
    user = _require_editor(request)
    body = await request.json()
    events = body.get("events", [])
    meeting_id = body.get("meeting_id")
    saved = 0
    for e in events:
        payload = llm_parser.to_event_payload(e)
        if payload["start_datetime"]:
            payload["team_id"] = user.get("team_id")
            payload["created_by"] = str(user["id"])
            payload["meeting_id"] = meeting_id
            db.create_event(payload)
            saved += 1
    return {"saved": saved}


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
        refined = llm_parser.refine_schedule(text, events, model)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")
    return {"events": refined}


@app.get("/api/ai/models")
def ai_models():
    models, ok = llm_parser.get_available_models_with_status()
    if not ok:
        raise HTTPException(status_code=502, detail=f"Ollama 서버({llm_parser.OLLAMA_BASE_URL})에 연결할 수 없습니다.")
    return {"models": models}


@app.post("/api/ai/weekly-report")
async def ai_weekly_report(request: Request):
    user = _require_editor(request)
    from datetime import date as _date, datetime as _dt, timedelta as _td
    body = await request.json()
    base_date = (body.get("base_date") or _date.today().isoformat()).strip()
    team_id   = body.get("team_id") or None
    model     = body.get("model", llm_parser.DEFAULT_MODEL)

    base_dt    = _dt.strptime(base_date, "%Y-%m-%d")
    past_start = (base_dt - _td(days=7)).strftime("%Y-%m-%d")
    past_end   = (base_dt - _td(days=1)).strftime("%Y-%m-%d")
    future_end = (base_dt + _td(days=6)).strftime("%Y-%m-%d")

    past_events   = db.get_events_by_date_range(past_start, past_end, team_id)
    future_events = db.get_events_by_date_range(base_date,  future_end, team_id)

    try:
        report = llm_parser.generate_weekly_report(past_events, future_events, base_date, model)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e}")

    title      = f"주간 업무 보고 ({base_date})"
    meeting_id = db.create_meeting(title, report, user.get("team_id"), user["id"], base_date)

    return {
        "meeting_id":   meeting_id,
        "past_count":   len(past_events),
        "future_count": len(future_events),
        "base_date":    base_date,
        "past_start":   past_start,
        "future_end":   future_end,
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
