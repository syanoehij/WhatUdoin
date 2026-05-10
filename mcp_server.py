"""
WhatUdoin MCP 서버 (v1, read-only)
- Transport: Streamable HTTP
- 인증: FastAPI 미들웨어에서 Bearer 토큰 검증 (OAuth 없음 — 일반 API 키 방식)
- 범위: 14개 read-only 도구 (write tool 0건 — M6 boundary 잠금)
- app.py를 import하지 않음 (순환 import 방지)

MCP write owner 원칙 (plan §9/§13 M6):
- 본 파일은 직접 SQLite write를 절대 하지 않는다.
- 향후 write/edit tool 추가 시 반드시 _call_web_api_command 헬퍼를 통해
  Web API write path 경유. sqlite3, cursor, db.create_/update_/delete_/add_
  직접 호출 금지.
"""
import contextvars
import hashlib

import database as db
from permissions import _can_read_doc, _can_read_checklist

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings

# 요청별 현재 사용자를 저장하는 context var (미들웨어에서 set)
_mcp_user: contextvars.ContextVar[dict | None] = contextvars.ContextVar("mcp_user", default=None)

mcp = FastMCP(
    "WhatUdoin",
    sse_path="/",
    message_path="/",
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _user_from_ctx(ctx) -> dict | None:  # noqa: ARG001
    """미들웨어가 검증·주입한 사용자를 반환한다."""
    return _mcp_user.get()


async def _call_web_api_command(tool_name: str, payload: dict, ctx) -> dict:
    """MCP write/edit tool이 호출하는 Web API write path proxy.

    원칙 (plan §9/§13 M6):
    - MCP service는 직접 SQLite write를 절대 하지 않는다.
    - 모든 write/edit는 Web API command endpoint 경유 — 권한, lock,
      history, SSE publish, audit 모든 후처리가 Web API에 통합되어 있다.
    - 향후 write tool 추가 시 본 헬퍼를 사용하고, mcp_command_registry
      의 분류표에서 web_api_path/method/permission을 읽어 호출한다.

    본 사이클(M6-2)에서는 실제 write tool 미추가 — 헬퍼 정의 + boundary
    잠금까지. 향후 write tool 추가는 별도 마일스톤 또는 §18 후속.
    """
    from mcp_command_registry import MCP_WRITE_TOOL_CLASSIFICATION
    spec = MCP_WRITE_TOOL_CLASSIFICATION.get(tool_name)
    if not spec:
        raise ValueError(f"unknown write tool: {tool_name}")
    # 실제 IPC 호출 구현(stdlib urllib.request, Authorization Bearer
    # WHATUDOIN_INTERNAL_TOKEN). Web API base URL은 env로 구성:
    # WHATUDOIN_WEB_API_INTERNAL_URL (기본 http://127.0.0.1:8000).
    raise NotImplementedError(
        "M6-2: write tool은 본 사이클 미추가. 운영 요구 발생 시 별도 step."
    )


def mount_mcp(app) -> None:
    """FastAPI 앱에 MCP 서버를 마운트한다.

    /mcp is kept as SSE for Cline compatibility.
    /mcp-codex is streamable HTTP for Codex CLI.
    """
    app.mount("/mcp", mcp.sse_app())
    app.mount("/mcp-codex", mcp.streamable_http_app())


def verify_bearer_token(authorization: str) -> dict | None:
    """Authorization 헤더에서 Bearer 토큰을 검증하고 사용자를 반환한다."""
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = db.get_user_by_mcp_token_hash(token_hash)
    if not user or not user.get("is_active"):
        return None
    return user


# ── 7개 read-only 도구 ────────────────────────────────────

@mcp.tool()
async def list_projects(ctx: Context, include_inactive: bool = False) -> list[dict]:
    """
    WhatUdoin의 프로젝트 목록을 조회합니다.

    Use this tool when:
    - 어떤 프로젝트들이 존재하는지 파악할 때 (다른 도구 호출 전 컨텍스트 확보)
    - 특정 프로젝트의 색상, 기간 정보가 필요할 때
    - 활성 프로젝트 목록으로 list_events/list_checklists의 project 파라미터 값을 결정할 때

    include_inactive=True로 설정하면 비활성(종료) 프로젝트도 포함합니다.
    반환 필드: name, color, is_active, start_date, end_date
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    with db.get_conn() as conn:
        return db.get_projects_for_mcp(conn, include_inactive=include_inactive, viewer=user)


@mcp.tool()
async def list_events(
    ctx: Context,
    project: str | None = None,
    start_after: str | None = None,
    end_before: str | None = None,
) -> list[dict]:
    """
    WhatUdoin의 일정 목록을 조회합니다 (경량 메타데이터만 반환).

    Use this tool when:
    - 오늘/이번 주/특정 기간의 일정을 확인할 때
    - 특정 프로젝트의 일정을 조회할 때
    - 담당자별 일정 현황을 파악할 때

    필터를 사용하면 토큰 효율이 높아집니다. 전체 조회보다 project/날짜 범위를 지정하세요.
    반환 필드: id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type
    상세 정보(설명, 위치, 반복 규칙 등)는 get_event()를 사용하세요.

    파라미터:
    - project: 프로젝트 이름 (예: "개발팀"). list_projects로 정확한 이름을 먼저 확인하세요.
    - start_after: 이 날짜 이후 시작하는 일정만 (ISO 8601, 예: "2026-01-01" 또는 "2026-01-01T00:00:00")
    - end_before: 이 날짜 이전 종료하는 일정만 (ISO 8601, 예: "2026-12-31T23:59:59")
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    with db.get_conn() as conn:
        return db.get_events_filtered(conn, project=project, start_after=start_after, end_before=end_before, viewer=user)


@mcp.tool()
async def get_event(ctx: Context, event_id: int) -> dict:
    """
    특정 일정의 상세 정보를 조회합니다.

    Use this tool when:
    - list_events로 얻은 id로 특정 일정의 전체 내용(설명, 위치, 바인딩 체크리스트 등)을 확인할 때
    - 반복 일정의 recurrence_rule이나 부모 일정 정보가 필요할 때

    삭제된 일정이나 종료 프로젝트 소속 일정은 error 객체를 반환합니다.
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    result = db.get_event_for_mcp(event_id, viewer=user)
    if result is None:
        return {"error": "not_found", "id": event_id, "reason": "이벤트가 존재하지 않거나 접근 권한이 없습니다."}
    return result


@mcp.tool()
async def list_documents(ctx: Context) -> list[dict]:
    """
    현재 사용자가 열람 가능한 문서(회의록) 목록을 조회합니다 (경량 메타데이터만 반환).

    Use this tool when:
    - 회의록이나 팀 문서 목록을 확인할 때
    - 특정 프로젝트나 팀과 관련된 문서를 찾을 때
    - get_document 호출 전 문서 id를 파악할 때

    공개 범위에 따라 열람 가능한 문서만 반환됩니다 (비공개 문서 자동 필터링).
    반환 필드: id, title, author_name, team_name, updated_at, event_count
    본문은 get_document()를 사용하세요.
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.get_all_meetings_summary(viewer=user)


@mcp.tool()
async def get_document(ctx: Context, doc_id: int) -> dict:
    """
    특정 문서(회의록)의 전체 내용을 조회합니다.

    Use this tool when:
    - list_documents로 얻은 id로 문서의 본문 내용을 확인할 때
    - 회의 내용, 결정 사항, 액션 아이템을 파악할 때

    열람 권한이 없거나 삭제된 문서는 error 객체를 반환합니다.
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    doc = db.get_meeting(doc_id)
    if not doc or not _can_read_doc(user, doc):
        return {"error": "not_found", "id": doc_id, "reason": "문서가 존재하지 않거나 접근 권한이 없습니다."}
    return doc


@mcp.tool()
async def list_checklists(ctx: Context, project: str | None = None) -> list[dict]:
    """
    체크리스트 목록을 조회합니다 (경량 메타데이터만 반환).

    Use this tool when:
    - 특정 프로젝트의 할 일 목록이나 체크리스트를 확인할 때
    - 완료/미완료 항목 현황을 파악할 때
    - get_checklist 호출 전 체크리스트 id를 파악할 때

    반환 필드: id, title, project, updated_at, item_count, done_count
    항목 상세(각 항목 텍스트, 담당자, 기한 등)는 get_checklist()를 사용하세요.

    파라미터:
    - project=None(기본값): 열람 가능한 모든 체크리스트 반환
    - project="프로젝트명": 해당 프로젝트 체크리스트만 반환
    - project="": 프로젝트 미지정 체크리스트만 반환 (None과 다름)
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.get_checklists_summary(project=project, viewer=user)


@mcp.tool()
async def get_checklist(ctx: Context, checklist_id: int) -> dict:
    """
    특정 체크리스트의 전체 내용(항목 목록 포함)을 조회합니다.

    Use this tool when:
    - list_checklists로 얻은 id로 체크리스트의 세부 항목을 확인할 때
    - 각 항목의 완료 상태, 담당자, 기한을 파악할 때

    열람 권한이 없거나 삭제된 체크리스트는 error 객체를 반환합니다.
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    cl = db.get_checklist(checklist_id)
    if not cl or not _can_read_checklist(user, cl):
        return {"error": "not_found", "id": checklist_id, "reason": "체크리스트가 존재하지 않거나 접근 권한이 없습니다."}
    return cl


@mcp.tool()
async def search_all(
    ctx: Context,
    query: str,
    type: str | None = None,
    start_after: str | None = None,
    end_before: str | None = None,
) -> list[dict]:
    """
    WhatUdoin 전체 데이터를 키워드로 검색합니다.

    Use this tool when:
    - 특정 내용이 어느 일정/문서/체크리스트에 있는지 모를 때
    - list_* 없이 바로 원하는 항목을 찾을 때
    - 여러 데이터 타입에 걸친 키워드 검색이 필요할 때

    파라미터:
    - query: 검색 키워드 (빈 문자열이면 빈 결과 반환)
    - type: "event" | "document" | "checklist" | None(전체 검색, 기본값)
    - start_after: 이벤트 날짜 범위 시작 (ISO 8601, 예: "2026-01-01"). 미지정 시 오늘-7일.
    - end_before: 이벤트 날짜 범위 종료 (ISO 8601, 예: "2026-12-31"). 미지정 시 오늘+7일.
      → 시작일이 범위를 벗어나도 종료일이 범위 안에 들어오면 포함됩니다.
      → 날짜 필터는 이벤트에만 적용됩니다. 문서·체크리스트는 날짜 무관 전체 매칭.
      → 특정 날짜 제한 없이 전체 이벤트를 검색하려면 start_after="2000-01-01"처럼 넓게 지정하세요.

    반환: type 필드 포함 경량 결과 목록
    - type="event": id, title, project, start_datetime, end_datetime, assignee, kanban_status
    - type="document": id, title, author_name, team_name, updated_at
    - type="checklist": id, title, project, updated_at

    상세 내용은 결과의 id로 get_event()/get_document()/get_checklist()를 호출하세요.
    """
    from datetime import datetime, timedelta
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    if start_after is None and end_before is None:
        today = datetime.now().date()
        start_after = (today - timedelta(days=7)).isoformat()
        end_before = (today + timedelta(days=7)).isoformat()
    return db.search_all(query=query, type=type, viewer=user,
                         start_after=start_after, end_before=end_before)


@mcp.tool()
async def search_events(
    ctx: Context,
    query: str,
    start_after: str | None = None,
    end_before: str | None = None,
) -> list[dict]:
    """
    WhatUdoin 일정을 키워드로 검색합니다.

    Use this tool when:
    - 특정 제목의 일정을 찾을 때
    - 특정 기간 안의 일정을 키워드로 검색할 때
    - 일정 타입 결과만 필요하고 문서·체크리스트는 불필요할 때

    파라미터:
    - query: 검색 키워드 (빈 문자열이면 빈 결과 반환)
    - start_after: 이벤트 날짜 범위 시작 (ISO 8601). 미지정 시 오늘-7일.
    - end_before: 이벤트 날짜 범위 종료 (ISO 8601). 미지정 시 오늘+7일.
      → 시작일이 범위를 벗어나도 종료일이 범위 안에 들어오면 포함됩니다.
      → 특정 날짜 제한 없이 전체를 검색하려면 start_after="2000-01-01"처럼 넓게 지정하세요.

    반환 필드: id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type
    상세 내용은 get_event()를 사용하세요.
    """
    from datetime import datetime, timedelta
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    if start_after is None and end_before is None:
        today = datetime.now().date()
        start_after = (today - timedelta(days=7)).isoformat()
        end_before = (today + timedelta(days=7)).isoformat()
    return db.search_events_mcp(query=query, start_after=start_after, end_before=end_before, viewer=user)


@mcp.tool()
async def list_kanban(
    ctx: Context,
    project: str | None = None,
) -> list[dict]:
    """
    칸반 보드 항목 목록을 조회합니다 (경량 메타데이터만 반환).

    Use this tool when:
    - 칸반 보드에 표시되는 전체 항목 현황을 파악할 때
    - 특정 프로젝트의 칸반 항목을 확인할 때
    - get_kanban_item 호출 전 id를 파악할 때

    칸반 항목 기준: kanban_status 설정됐거나 프로젝트 미지정 항목.
    삭제됐거나 비활성 프로젝트, 숨김 처리된 항목은 제외됩니다.
    반환 필드: id, title, project, kanban_status, priority, assignee, start_datetime, end_datetime
    상세 내용은 get_kanban_item()을 사용하세요.

    파라미터:
    - project=None(기본값): 열람 가능한 모든 칸반 항목 반환
    - project="프로젝트명": 해당 프로젝트 칸반 항목만 반환
    - project="": 프로젝트 미지정 항목만 반환 (None과 다름)
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.get_kanban_summary(project=project, viewer=user)


@mcp.tool()
async def get_kanban_item(ctx: Context, event_id: int) -> dict:
    """
    특정 칸반 항목의 상세 정보를 조회합니다.

    Use this tool when:
    - list_kanban으로 얻은 id로 칸반 항목의 전체 내용을 확인할 때
    - 칸반 맥락에서 특정 태스크의 설명, 담당자, 기간 등 상세 정보가 필요할 때

    내부적으로 get_event와 동일한 DB 함수를 사용합니다.
    삭제된 항목이나 존재하지 않는 id는 error 객체를 반환합니다.
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    result = db.get_event_for_mcp(event_id, viewer=user)
    if result is None:
        return {"error": "not_found", "id": event_id, "reason": "칸반 항목이 존재하지 않거나 접근 권한이 없습니다."}
    return result


@mcp.tool()
async def search_kanban(
    ctx: Context,
    query: str,
    project: str | None = None,
) -> list[dict]:
    """
    칸반 항목을 키워드로 검색합니다.

    Use this tool when:
    - 특정 제목의 칸반 태스크를 찾을 때
    - 특정 프로젝트의 칸반 항목을 키워드로 검색할 때
    - 날짜 무관하게 태스크 중심으로 검색할 때

    날짜 필터 없음 (칸반은 태스크 중심, 날짜 무관).
    반환 필드: id, title, project, kanban_status, priority, assignee
    상세 내용은 get_kanban_item()을 사용하세요.

    파라미터:
    - query: 검색 키워드 (빈 문자열이면 빈 결과 반환)
    - project=None(기본값): 전체 칸반 항목에서 검색
    - project="프로젝트명": 해당 프로젝트 항목만 검색
    - project="": 프로젝트 미지정 항목만 검색
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.search_kanban_mcp(query=query, project=project, viewer=user)


@mcp.tool()
async def search_documents(ctx: Context, query: str) -> list[dict]:
    """
    문서(회의록)를 키워드로 검색합니다.

    Use this tool when:
    - 특정 내용이 담긴 문서를 찾을 때
    - 문서 타입 결과만 필요하고 일정·체크리스트는 불필요할 때
    - 회의 내용, 결정 사항을 키워드로 검색할 때

    title과 content를 모두 검색합니다.
    열람 권한에 따라 접근 가능한 문서만 반환됩니다.
    반환 필드: id, title, author_name, team_name, updated_at
    본문은 get_document()를 사용하세요.

    파라미터:
    - query: 검색 키워드 (빈 문자열이면 빈 결과 반환)
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.search_documents_mcp(query=query, viewer=user)


@mcp.tool()
async def search_checklists(
    ctx: Context,
    query: str,
    project: str | None = None,
) -> list[dict]:
    """
    체크리스트를 키워드로 검색합니다.

    Use this tool when:
    - 특정 내용이 담긴 체크리스트를 찾을 때
    - 체크리스트 타입 결과만 필요할 때
    - 특정 프로젝트의 체크리스트를 키워드로 검색할 때

    title과 content를 모두 검색합니다.
    반환 필드: id, title, project, updated_at, item_count, done_count
    항목 상세는 get_checklist()를 사용하세요.

    파라미터:
    - query: 검색 키워드 (빈 문자열이면 빈 결과 반환)
    - project=None(기본값): 전체 체크리스트에서 검색
    - project="프로젝트명": 해당 프로젝트 체크리스트만 검색
    - project="": 프로젝트 미지정 체크리스트만 검색
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.search_checklists_mcp(query=query, project=project, viewer=user)
