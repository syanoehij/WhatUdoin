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


def is_admin(user) -> bool:
    return user is not None and user.get("role") == "admin"


def is_member(user) -> bool:
    """팀 기능 그룹 A #2 신규.

    'member' (Phase 2 백필 후 새 기본 role) 또는 'admin'을 통과시킨다.
    호환을 위해 백필 전 기존 'editor' role도 동일 등급으로 인정한다.
    """
    return user is not None and user.get("role") in ("member", "editor", "admin")


def is_editor(user) -> bool:
    """기존 호환 — 내부적으로 is_member에 위임.

    라우트 호출부(`app.py:_require_editor`)는 본 사이클에서 변경하지 않는다(#16 책임).
    """
    return is_member(user)


# ── 신규 권한 헬퍼 (팀 기능 그룹 A #2) ─────────────────────────────

def user_team_ids(user) -> set:
    """user_teams(approved)에 등록된 team_id 집합. admin은 row 없으므로 빈 집합 반환.

    DB 조회 결과를 호출 단위로 캐시하지 않는다 — 호출 빈도가 낮고,
    승인/탈퇴 흐름이 즉시 반영되어야 하므로.
    """
    if user is None:
        return set()
    uid = user.get("id")
    if uid is None:
        return set()
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT team_id FROM user_teams WHERE user_id = ? AND status = 'approved'",
                (uid,),
            ).fetchall()
        return {row[0] for row in rows if row[0] is not None}
    except Exception:
        # 마이그레이션 전 호출 등 예외 상황: legacy users.team_id로 fallback
        legacy = user.get("team_id")
        return {legacy} if legacy else set()


def user_can_access_team(user, team_id) -> bool:
    """admin은 슈퍼유저 정책으로 모든 팀 접근 True.
    그 외는 user_teams approved 멤버십 확인.
    """
    if user is None or team_id is None:
        return False
    if is_admin(user):
        return True
    return team_id in user_team_ids(user)


def is_team_admin(user, team_id) -> bool:
    """user_teams.role == 'admin' 인 팀 관리자 여부.

    글로벌 admin(users.role == 'admin')도 모든 팀에 대해 True 반환 (슈퍼유저 정책).
    """
    if user is None or team_id is None:
        return False
    if is_admin(user):
        return True
    uid = user.get("id")
    if uid is None:
        return False
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_teams "
                "WHERE user_id = ? AND team_id = ? AND status = 'approved' AND role = 'admin'",
                (uid, team_id),
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def require_work_team_access(user, team_id) -> None:
    """팀 컨텍스트 진입 전 검증. 실패 시 HTTPException 403."""
    from fastapi import HTTPException
    if not user_can_access_team(user, team_id):
        raise HTTPException(status_code=403, detail="해당 팀에 대한 접근 권한이 없습니다.")


# work_team_id 결정 쿠키 — UI 통합은 #15 책임. 본 사이클은 헬퍼 시그니처 + fallback만.
WORK_TEAM_COOKIE = "work_team_id"


def resolve_work_team(request: Request, user, explicit_id=None):
    """현재 작업 컨텍스트의 team_id를 결정한다.

    우선순위: explicit_id → 쿠키 → 사용자의 대표 팀 → users.team_id legacy → None.
    admin은 명시 work_team_id가 없으면 None을 반환한다 (admin_team_scope의 의도).
    """
    # 1. 명시 인자
    if explicit_id is not None:
        try:
            tid = int(explicit_id)
        except (TypeError, ValueError):
            tid = None
        if tid is not None:
            return tid

    # 2. 쿠키
    cookie_val = request.cookies.get(WORK_TEAM_COOKIE) if request else None
    if cookie_val:
        try:
            return int(cookie_val)
        except (TypeError, ValueError):
            pass

    # 3. admin은 명시 미지정 시 None (전 팀 슈퍼유저 — 특정 팀 컨텍스트 강제 안 함)
    if is_admin(user):
        return None

    # 4. 사용자의 대표 팀 (user_teams 첫 번째 approved row)
    team_ids = user_team_ids(user)
    if team_ids:
        # 결정성 위해 최소 id 선택
        return min(team_ids)

    # 5. legacy fallback
    legacy = user.get("team_id") if user else None
    return legacy


def admin_team_scope(user):
    """admin이 명시 work_team_id 없이 호출될 때의 scope. 항상 None (전 팀)."""
    if not is_admin(user):
        return user.get("team_id") if user else None
    return None


# ── 기존 권한 헬퍼 (호환 위임) ─────────────────────────────────────

def can_edit_event(user, event: dict) -> bool:
    """해당 사용자가 이 일정을 수정할 수 있는지 확인.

    내부적으로 user_can_access_team에 위임 (호환 단계).
    """
    if not is_member(user):
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
    return user_can_access_team(user, event_team)


def can_edit_checklist(user, checklist: dict) -> bool:
    """해당 사용자가 이 체크리스트를 수정할 수 있는지 확인.

    내부적으로 user_can_access_team에 위임 (호환 단계).
    """
    if not is_member(user):
        return False
    if is_admin(user):
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
    return user_can_access_team(user, cl_team)


def can_edit_project(user, project: dict) -> bool:
    """해당 사용자가 이 프로젝트를 수정할 수 있는지 확인.

    내부적으로 user_can_access_team에 위임 (호환 단계).
    """
    if is_admin(user):
        return True
    if project.get("is_hidden"):
        return project.get("owner_id") == user.get("id")
    proj_team = project.get("team_id")
    if proj_team is not None:
        return user_can_access_team(user, proj_team)
    return True
