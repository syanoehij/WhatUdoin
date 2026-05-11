"""
가시성 헬퍼 — app.py에서 분리하여 mcp_server.py에서도 재사용.
database.py는 이 파일을 import하지 않으므로 순환 import 없음.

팀 기능 그룹 A #10: work_team_ids 인자 추가.
- work_team_ids=None 이고 로그인 사용자면 → auth.user_team_ids(user) fallback
  (작업 팀 쿠키 도입 전 — #15 이후 호출부에서 명시 작업 팀 1개 set을 넘긴다)
- admin 은 work_team_ids 무관하게 전역 슈퍼유저로 통과
"""
import database as db
import auth


def _scope_team_ids(user, work_team_ids):
    """가시성 판단에 쓸 team_id 집합. work_team_ids 가 None 이면 사용자 소속 팀 전체로 fallback."""
    if work_team_ids is not None:
        return set(work_team_ids)
    return auth.user_team_ids(user)


def _author_tokens(user):
    """events/checklists.created_by 비교용 토큰 — 신규 쓰기는 str(user.id), legacy 는 사용자 이름."""
    if not user:
        return set()
    s = {str(user.get("id"))}
    if user.get("name"):
        s.add(user.get("name"))
    return s


def _can_read_doc(user, doc: dict, work_team_ids=None) -> bool:
    if not doc:
        return False
    if user and user.get("role") == "admin":
        return True
    if doc.get("is_public"):
        return True
    if not user:
        return False
    is_team_doc = bool(doc.get("is_team_doc"))
    doc_team = doc.get("team_id")
    # 개인 문서(is_team_doc=0): 작성자 본인은 현재 작업 팀·소속 팀과 무관하게 항상 노출 (계획서 §8)
    if not is_team_doc and doc.get("created_by") == user.get("id"):
        return True
    # 팀 문서(is_team_doc=1): "그 팀 소속이라서 보이는 것" — 작성자였다는 사실은 권한 근거가 아니다 (§8-1).
    #   추방되면 자기가 만든 팀 문서도 안 보인다. team_id NULL 잔존 row 는 작성자 본인만(아래 분기 외 — 호환).
    if doc_team is None:
        # team_id NULL: 개인 문서면 위에서 처리됨. 팀 문서 잔존 row 는 작성자 본인에게만(백필 실패 호환).
        if is_team_doc and doc.get("created_by") == user.get("id"):
            return True
        return False
    scope = _scope_team_ids(user, work_team_ids)
    if is_team_doc:
        return doc_team in scope
    # 개인 문서(is_team_doc=0, 작성자 아님): team_share=1 이고 현재 작업 팀이 그 team_id 일 때만 읽기
    if doc.get("team_share"):
        return doc_team in scope
    return False


def _can_read_checklist(user, cl: dict, work_team_ids=None) -> bool:
    if user and user.get("role") == "admin":
        return True
    proj_name = cl.get("project") or ""
    proj = db.get_project(proj_name) if proj_name else None
    if proj and proj.get("is_hidden"):
        if not user:
            return False
        return db.is_hidden_project_visible(proj["id"], user)
    if user:
        # 로그인 사용자: 작업 팀 컨텍스트 의존 (계획서 섹션 8 "체크 가시성")
        cl_team = cl.get("team_id")
        if cl_team is None:
            # team_id NULL 잔존 체크: 작성자 본인만 (created_by 는 신규 str(id)/legacy 이름)
            return cl.get("created_by") in _author_tokens(user)
        scope = _scope_team_ids(user, work_team_ids)
        return cl_team in scope
    # 비로그인: 기존 공개 정책 유지 (히든·private 제외, is_public=1 또는 프로젝트 연동)
    is_pub = cl.get("is_public")
    if is_pub == 1:
        return True
    if is_pub is None and proj_name:
        return bool(proj and not proj.get("is_private"))
    return False
