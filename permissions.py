"""
가시성 헬퍼 — app.py에서 분리하여 mcp_server.py에서도 재사용.
database.py는 이 파일을 import하지 않으므로 순환 import 없음.
"""
import database as db


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


def _can_read_checklist(user, cl: dict) -> bool:
    if user and user.get("role") == "admin":
        return True
    proj_name = cl.get("project") or ""
    proj = db.get_project(proj_name) if proj_name else None
    if proj and proj.get("is_hidden"):
        if not user:
            return False
        return db.is_hidden_project_visible(proj["id"], user)
    if user:
        return True
    is_pub = cl.get("is_public")
    if is_pub == 1:
        return True
    if is_pub is None and proj_name:
        return bool(proj and not proj.get("is_private"))
    return False
