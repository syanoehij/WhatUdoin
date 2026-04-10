from fastapi import Request
import database as db

SESSION_COOKIE = "session_id"


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def get_current_user(request: Request):
    """세션 쿠키 → IP 화이트리스트 순으로 현재 사용자 반환. 없으면 None (뷰어)."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        user = db.get_session_user(session_id)
        if user:
            return user
    # IP 화이트리스트 자동 로그인
    ip = get_client_ip(request)
    return db.get_user_by_whitelist_ip(ip)


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
    event_team = event.get("team_id")
    if event_team is None:
        return True
    return event_team == user.get("team_id")
