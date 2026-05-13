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
                "SELECT ut.team_id FROM user_teams ut "
                "JOIN teams t ON t.id = ut.team_id "
                "WHERE ut.user_id = ? AND ut.status = 'approved' AND t.deleted_at IS NULL",
                (uid,),
            ).fetchall()
        return {row[0] for row in rows if row[0] is not None}
    except Exception:
        # 마이그레이션 전 호출 등 예외 상황: legacy users.team_id로 fallback
        legacy = user.get("team_id")
        if not legacy:
            return set()
        try:
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT 1 FROM teams WHERE id = ? AND deleted_at IS NULL", (legacy,)
                ).fetchone()
            return {legacy} if row else set()
        except Exception:
            return {legacy}


def is_unassigned(user) -> bool:
    """팀 미배정 로그인 사용자 여부 — 팀 기능 그룹 B #12.

    로그인했으나 approved 소속 팀이 0개인 비-admin 사용자.
    `user_team_ids`가 이미 `deleted_at IS NULL` 필터를 적용하므로
    "삭제 예정 팀만 남은 사용자"도 자동으로 미배정으로 취급된다 (계획서 섹션 6·7).
    admin은 user_teams row가 없어도 슈퍼유저이므로 미배정 아님.
    """
    if user is None:
        return False
    if is_admin(user):
        return False
    return len(user_team_ids(user)) == 0


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


# work_team_id 결정 쿠키 — 팀 기능 그룹 B #15: 발급/검증/Set-Cookie 통합.
WORK_TEAM_COOKIE = "work_team_id"


def _team_is_active(team_id) -> bool:
    """teams.deleted_at IS NULL 인 팀이 존재하는지 — 작업 팀 쿠키/명시 값 검증용."""
    if team_id is None:
        return False
    try:
        return db.get_team_active(team_id) is not None
    except Exception:
        return False


def _work_team_default(user):
    """쿠키가 없거나 무효일 때의 대표 작업 팀.

    - 비admin: user_teams approved + 비삭제 팀 중 joined_at 가장 이른 팀(대표 팀).
               approved 소속이 없으면 legacy users.team_id (비삭제일 때만) → None.
    - admin:   첫 번째 비삭제 팀(id 최소). 비삭제 팀이 없으면 None.
    """
    if is_admin(user):
        return db.first_active_team_id()
    uid = user.get("id") if user else None
    if uid is not None:
        primary = db.primary_team_id_for_user(uid)
        if primary is not None:
            return primary
    # legacy fallback (마이그레이션 전 호출 방어) — 비삭제 팀일 때만
    legacy = user.get("team_id") if user else None
    if legacy is not None and _team_is_active(legacy):
        return legacy
    return None


def resolve_work_team(request: Request, user, explicit_id=None):
    """현재 작업 컨텍스트의 team_id를 결정한다 (팀 기능 그룹 B #15).

    우선순위: explicit_id(검증 안 함 — 호출부가 require_work_team_access/_work_scope 로 검증)
              → work_team_id 쿠키(검증: 사용자 접근 가능 + 비삭제 팀)
              → 대표 작업 팀(_work_team_default).
    쿠키 값이 무효(삭제 예정 팀 / 소속 빠짐)면 무시하고 대표 팀으로 fallback.
    admin 의 대표 팀은 첫 번째 비삭제 팀 (계획서 §7 — '마지막 선택 팀'은 쿠키가 담당).
    """
    # 1. 명시 인자 (호출부 책임으로 무조건 신뢰 — _work_scope/require_work_team_access 가 검증)
    if explicit_id is not None:
        try:
            tid = int(explicit_id)
        except (TypeError, ValueError):
            tid = None
        if tid is not None:
            return tid

    # 2. 쿠키 — 검증 통과 시에만 사용
    cookie_val = request.cookies.get(WORK_TEAM_COOKIE) if request else None
    if cookie_val:
        try:
            ctid = int(cookie_val)
        except (TypeError, ValueError):
            ctid = None
        if ctid is not None and user_can_access_team(user, ctid) and _team_is_active(ctid):
            return ctid
        # 무효 쿠키 → 아래 fallback (호출부가 Set-Cookie 로 갱신)

    # 3. 대표 작업 팀
    return _work_team_default(user)


def admin_team_scope(user):
    """admin이 명시 work_team_id 없이 호출될 때의 scope. 항상 None (전 팀)."""
    if not is_admin(user):
        return user.get("team_id") if user else None
    return None


# 팀 기능 그룹 C #16 — admin 쓰기 요청에서 work_team_id 명시 검증.
_REQUIRE_ADMIN_WORK_TEAM_MSG = (
    "현재 작업 팀이 선택되지 않았습니다. 프로필 메뉴에서 작업 팀을 선택해 주세요."
)


def require_admin_work_team(request: Request, user, explicit_id=None) -> int:
    """신규 row 생성 라우트 전용 — 현재 작업 팀을 명시적으로 보장한다 (#16).

    - admin: explicit_id → 검증(active + 접근 가능) 후 반환. 없으면 work_team_id 쿠키 →
             동일 검증 후 반환. 둘 다 없거나 무효이면 HTTPException 400.
             admin에 대한 묵시적 first_active_team_id fallback 금지(의도된 명시성).
    - 비admin: explicit_id → user_can_access_team 통과 시 반환. 그렇지 않으면 쿠키 →
              대표 팀(joined_at 가장 이른 approved 팀) 순으로 결정.
              최종 결과가 None이면 HTTPException 400 (미배정 사용자의 NULL team_id 신규 row 차단).
              **개인 문서(meetings.is_team_doc=0) 작성은 호출부가 이 헬퍼를 건너뛰고
              직접 NULL을 허용해야 한다.**

    반환값은 항상 유효한 int team_id 이거나 HTTPException raise.
    `require_work_team_access`와 달리 명시적인 작업 팀 결정을 강제한다.
    """
    from fastapi import HTTPException

    def _validate(tid_raw):
        if tid_raw is None:
            return None
        try:
            tid = int(tid_raw)
        except (TypeError, ValueError):
            return None
        if not _team_is_active(tid):
            return None
        if not user_can_access_team(user, tid):
            return None
        return tid

    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    # 1) explicit_id 우선 (admin·비admin 공통)
    tid = _validate(explicit_id)
    if tid is not None:
        return tid

    # 2) admin: 쿠키만 추가로 시도 (묵시적 first_active fallback 금지)
    if is_admin(user):
        cookie_raw = request.cookies.get(WORK_TEAM_COOKIE) if request else None
        tid = _validate(cookie_raw)
        if tid is not None:
            return tid
        raise HTTPException(status_code=400, detail=_REQUIRE_ADMIN_WORK_TEAM_MSG)

    # 3) 비admin
    #    explicit_id 가 (1)을 통과 못 했다는 건 비소속/비활성/형식오류 → 403.
    #    phase86/87 의 "비admin + 비소속 explicit team_id → 403" 보안 경계를 #16 헬퍼에서 보존.
    if explicit_id is not None:
        raise HTTPException(status_code=403, detail="해당 팀에 접근 권한이 없습니다.")
    tid = resolve_work_team(request, user, explicit_id=None)
    if tid is None:
        raise HTTPException(status_code=400, detail=_REQUIRE_ADMIN_WORK_TEAM_MSG)
    # 방어적 재검증 — 쿠키 무효화 race / legacy fallback 결과의 active·소속 확인
    if not _team_is_active(tid) or not user_can_access_team(user, tid):
        raise HTTPException(status_code=400, detail=_REQUIRE_ADMIN_WORK_TEAM_MSG)
    return tid


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


def can_edit_meeting(user, doc: dict) -> bool:
    """문서(meetings) 편집·삭제 권한 — 팀 기능 그룹 A #10 (계획서 8-1 혼합 모델).

    - admin: 전역 슈퍼유저로 항상 True (work_team_id 명시 강제는 #16 책임 — 본 사이클 미적용)
    - is_team_doc=1 (팀 문서): 같은 팀 승인 멤버 누구나. created_by 는 권한 판단에 쓰지 않는다 (§8-1:
      추방되면 자기가 만든 팀 문서도 편집 불가). team_id NULL 잔존 row(백필 실패)는 작성자 본인만 — 읽기 정책과 정합.
    - is_team_doc=0 (개인 문서): 작성자 본인(meetings.created_by == user.id)만. team_share=1이라도
      다른 멤버는 읽기만(섹션 8). 추방·탈퇴 후에도 작성자 본인은 계속 보유.
    """
    if not user or not doc:
        return False
    if is_admin(user):
        return True
    if not is_member(user):
        return False
    if doc.get("is_team_doc"):
        doc_team = doc.get("team_id")
        if doc_team is None:
            # 백필 실패 잔존 팀 문서(팀 없음) — 작성자 본인만 (orphan 을 임의 멤버에게 노출하지 않음)
            return doc.get("created_by") == user.get("id")
        return user_can_access_team(user, doc_team)
    # 개인 문서: 작성자 본인만
    return doc.get("created_by") == user.get("id")
