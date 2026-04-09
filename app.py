from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import database as db
import llm_parser


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield

app = FastAPI(title="WhatUDoin", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── 페이지 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="calendar.html")


# ── 이벤트 API (JSON) ────────────────────────────────────

@app.get("/api/events")
def list_events():
    events = db.get_all_events()
    # FullCalendar 형식으로 변환
    return [
        {
            "id": e["id"],
            "title": e["title"],
            "start": e["start_datetime"],
            "end": e["end_datetime"] or e["start_datetime"],
            "allDay": bool(e["all_day"]),
            "extendedProps": {
                "project":     e["project"],
                "description": e["description"],
                "location":    e["location"],
                "assignee":    e["assignee"],
                "all_day":     bool(e["all_day"]),
                "source":      e["source"],
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
    data = await request.json()
    data.setdefault("project", None)
    data.setdefault("description", "")
    data.setdefault("location", "")
    data.setdefault("assignee", None)
    data.setdefault("all_day", 0)
    data.setdefault("end_datetime", None)
    data.setdefault("created_by", "editor")
    data.setdefault("source", "manual")
    event_id = db.create_event(data)
    return {"id": event_id}


@app.put("/api/events/{event_id}")
async def update_event(event_id: int, request: Request):
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    data = await request.json()
    db.update_event(event_id, data)
    return {"ok": True}


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int):
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    db.delete_event(event_id)
    return {"ok": True}


# ── 프로젝트 ─────────────────────────────────────────────

@app.get("/api/projects")
def list_projects():
    return db.get_projects()


# ── AI 파싱 ──────────────────────────────────────────────

@app.get("/ai-import", response_class=HTMLResponse)
def ai_import_page(request: Request):
    return templates.TemplateResponse(request=request, name="ai_import.html")


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
    body = await request.json()
    events = body.get("events", [])
    saved = 0
    for e in events:
        payload = llm_parser.to_event_payload(e)
        if payload["start_datetime"]:
            db.create_event(payload)
            saved += 1
    return {"saved": saved}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
