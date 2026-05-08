import os

from fastapi import Request
import database as db

SESSION_COOKIE = "session_id"


def get_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "127.0.0.1"
    trusted_raw = os.environ.get("TRUSTED_PROXY", "")
    trusted = {ip.strip() for ip in trusted_raw.split(",") if ip.strip()}
    if trusted and peer in trusted:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def get_current_user(request: Request):
    """세션 쿠키 → IP 화이트리스트 순으로 현재 사용자 반환. 없으면 None (뷰어)."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        user = db.get_session_user(session_id)
        if user:
            return {**user, "login_via": "session"}
    # IP 화이트리스트 자동 로그인 (admin 계정은 제외)
    ip = get_client_ip(request)
    user = db.get_user_by_whitelist_ip(ip)
    if user and user.get("role") == "admin":
        return None
    if user:
        return {**user, "login_via": "ip"}
    return None


def is_ip_login(user) -> bool:
    return user is not None and user.get("login_via") == "ip"


def is_editor(user) -> bool:
    return user is not None and user.get("role") in ("editor", "admin")


def is_admin(user) -> bool:
    return user is not None and user.get("role") == "admin"


def can_edit_event(user, event: dict) -> bool:
    """해당 사용자가 이 일정을 수정할 수 있는지 확인"""
    if not is_editor(user):
        return False
    if is_admin(user):
        return True
    proj_name = event.get("project") or ""
    if proj_name:
        proj = db.get_project(proj_name)
        if proj and proj.get("is_hidden"):
            proj_id = proj.get("id")
            return proj_id is not None and db.is_hidden_project_visible(proj_id, user)
    event_team = event.get("team_id")
    if event_team is None:
        return True
    return event_team == user.get("team_id")


def can_edit_checklist(user, checklist: dict) -> bool:
    """해당 사용자가 이 체크리스트를 수정할 수 있는지 확인"""
    if not is_editor(user):
        return False
    if user.get("role") == "admin":
        return True
    proj_name = checklist.get("project") or ""
    if proj_name:
        proj = db.get_project(proj_name)
        if proj and proj.get("is_hidden"):
            proj_id = proj.get("id")
            return proj_id is not None and db.is_hidden_project_visible(proj_id, user)
    cl_team = checklist.get("team_id")
    if cl_team is None:
        return True
    return cl_team == user.get("team_id")


def can_edit_project(user, project: dict) -> bool:
    """해당 사용자가 이 프로젝트를 수정할 수 있는지 확인"""
    if user.get("role") == "admin":
        return True
    if project.get("is_hidden"):
        return project.get("owner_id") == user.get("id")
    proj_team = project.get("team_id")
    if proj_team is not None:
        return proj_team == user.get("team_id")
    return True
