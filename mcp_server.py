"""
WhatUdoin MCP 서버 (v1, read-only)
- Transport: Streamable HTTP
- 인증: FastAPI 미들웨어에서 Bearer 토큰 검증 (OAuth 없음 — 일반 API 키 방식)
- 범위: 6개 read-only 도구
- app.py를 import하지 않음 (순환 import 방지)
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


# ── 6개 read-only 도구 ────────────────────────────────────

@mcp.tool()
async def list_events(ctx: Context) -> list[dict]:
    """활성 프로젝트의 삭제되지 않은 모든 일정을 반환한다."""
    if _user_from_ctx(ctx) is None:
        raise PermissionError("인증이 필요합니다.")
    return db.get_all_events()


@mcp.tool()
async def get_event(ctx: Context, event_id: int) -> dict | None:
    """특정 일정을 반환한다. 삭제된 일정이나 종료 프로젝트 소속 일정은 null."""
    if _user_from_ctx(ctx) is None:
        raise PermissionError("인증이 필요합니다.")
    return db.get_event_for_mcp(event_id)


@mcp.tool()
async def list_documents(ctx: Context) -> list[dict]:
    """현재 사용자가 볼 수 있는 문서(회의록) 목록을 반환한다."""
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.get_all_meetings(viewer=user)


@mcp.tool()
async def get_document(ctx: Context, doc_id: int) -> dict | None:
    """특정 문서를 반환한다. 열람 권한이 없거나 삭제된 문서는 null."""
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    doc = db.get_meeting(doc_id)
    if not doc or not _can_read_doc(user, doc):
        return None
    return doc


@mcp.tool()
async def list_checklists(ctx: Context, project: str | None = None) -> list[dict]:
    """체크리스트 목록을 반환한다.
    project=None이면 가시 전체, project=""이면 미지정 항목만 반환.
    빈 문자열을 None으로 변환하지 않는다.
    """
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    return db.get_checklists(project=project, viewer=user)


@mcp.tool()
async def get_checklist(ctx: Context, checklist_id: int) -> dict | None:
    """특정 체크리스트를 반환한다. 열람 권한이 없거나 삭제된 체크리스트는 null."""
    user = _user_from_ctx(ctx)
    if user is None:
        raise PermissionError("인증이 필요합니다.")
    cl = db.get_checklist(checklist_id)
    if not cl or not _can_read_checklist(user, cl):
        return None
    return cl
