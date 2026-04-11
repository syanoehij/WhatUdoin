from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import database as db
import llm_parser
import auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="WhatUDoin", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.ico")


# ── 헬퍼 ────────────────────────────────────────────────

def _ctx(request: Request, **kwargs):
    user = auth.get_current_user(request)
    return {"request": request, "user": user, **kwargs}


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


# ── 페이지 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
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


@app.get("/project", response_class=HTMLResponse)
def project_page(request: Request):
    teams = db.get_all_teams()
    return templates.TemplateResponse(request, "project.html", _ctx(request, teams=teams))


@app.get("/meetings", response_class=HTMLResponse)
def meetings_page(request: Request):
    meetings = db.get_all_meetings()
    teams    = db.get_all_teams()
    return templates.TemplateResponse(request, "meeting_list.html", _ctx(
        request, meetings=meetings, teams=teams,
        default_model=llm_parser.DEFAULT_MODEL,
    ))


@app.get("/meetings/new", response_class=HTMLResponse)
def meeting_new_page(request: Request):
    user = auth.get_current_user(request)
    if not auth.is_editor(user):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "meeting_editor.html", _ctx(request, meeting=None, meeting_events=[]))


@app.get("/meetings/{meeting_id}", response_class=HTMLResponse)
def meeting_detail_page(request: Request, meeting_id: int):
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404)
    events = db.get_events_by_meeting(meeting_id)
    return templates.TemplateResponse(request, "meeting_editor.html", _ctx(
        request, meeting=meeting, meeting_events=events
    ))


@app.get("/meetings/{meeting_id}/history", response_class=HTMLResponse)
def meeting_history_page(request: Request, meeting_id: int):
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404)
    histories = db.get_meeting_histories(meeting_id)
    return templates.TemplateResponse(request, "meeting_history.html", _ctx(
        request, meeting=meeting, histories=histories
    ))


@app.get("/ai-import", response_class=HTMLResponse)
def ai_import_page(request: Request):
    return templates.TemplateResponse(request, "ai_import.html", _ctx(request))


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
    db.update_user(user_id, data)
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
    return [
        {
            "id": e["id"],
            "title": e["title"],
            "start": e["start_datetime"],
            "end": e["end_datetime"] or e["start_datetime"],
            "allDay": bool(e["all_day"]),
            "extendedProps": {
                "project":       e["project"],
                "description":   e["description"],
                "location":      e["location"],
                "assignee":      e["assignee"],
                "all_day":       bool(e["all_day"]),
                "source":        e["source"],
                "team_id":       e["team_id"],
                "meeting_id":    e["meeting_id"],
                "kanban_status": e.get("kanban_status"),
                "priority":      e.get("priority", "normal"),
            },
        }
        for e in events
    ]


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
    data["created_by"] = str(user["id"])
    data["team_id"] = user.get("team_id")
    event_id = db.create_event(data)
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
    data.setdefault("kanban_status", event.get("kanban_status"))
    data.setdefault("priority", event.get("priority", "normal"))
    db.update_event(event_id, data)
    return {"ok": True}


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, request: Request):
    user = _require_editor(request)
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not auth.can_edit_event(user, event):
        raise HTTPException(status_code=403, detail="다른 팀의 일정은 삭제할 수 없습니다.")
    db.delete_event(event_id)
    return {"ok": True}


@app.get("/api/kanban")
def get_kanban_events(team_id: int = None):
    return db.get_kanban_events(team_id)


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
    return db.get_projects()


@app.get("/api/project-timeline")
def project_timeline(team_id: int = None):
    return db.get_project_timeline(team_id)


@app.get("/api/members")
def list_members():
    users = db.get_all_users()
    return [u["name"] for u in users if u.get("is_active") and u.get("role") != "admin"]


# ── 회의록 API ───────────────────────────────────────────

@app.get("/api/meetings")
def list_meetings():
    return db.get_all_meetings()


@app.post("/api/meetings")
async def create_meeting(request: Request):
    user = _require_editor(request)
    data = await request.json()
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    meeting_date = data.get("meeting_date") or None
    is_team_doc  = 1 if data.get("is_team_doc", True) else 0
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    meeting_id = db.create_meeting(title, content, user.get("team_id"), user["id"], meeting_date, is_team_doc)
    return {"id": meeting_id}


@app.put("/api/meetings/{meeting_id}")
async def update_meeting(meeting_id: int, request: Request):
    user = _require_editor(request)
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404)
    data = await request.json()
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    meeting_date = data.get("meeting_date") or None
    is_team_doc  = 1 if data.get("is_team_doc", True) else 0
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력하세요.")
    db.update_meeting(meeting_id, title, content, user["id"], meeting_date, is_team_doc)
    return {"ok": True}


@app.get("/api/meetings/calendar")
def meetings_calendar():
    meetings = db.get_all_meetings()
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


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: int, request: Request):
    _require_editor(request)
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404)
    db.delete_meeting(meeting_id)
    return {"ok": True}


@app.get("/api/meetings/{meeting_id}/histories")
def meeting_histories(meeting_id: int):
    return db.get_meeting_histories(meeting_id)


@app.get("/api/meetings/{meeting_id}/events")
def meeting_events_api(meeting_id: int):
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
    return {"models": llm_parser.get_available_models()}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
