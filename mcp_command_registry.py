"""
MCP write/edit tool 위험도 분류 레지스트리 (M6-1).

원칙 (plan §9/§13 M6):
- MCP service는 직접 SQLite write를 절대 하지 않는다.
- 모든 write/edit tool은 Web API write path 경유.
- 본 모듈은 순수 데이터 모듈 — 외부 import 0건, 모듈 import 부작용 0.

현재 상태(M6-1):
- write tool은 본 사이클 미추가 (운영 요구 미관측).
- 분류표는 향후 write tool 추가 시 설계 근거로 사용.
- DB command service 도입 여부(M6-3) 결정 시 risk/permission 분류를 그대로 재사용 가능.
"""

# 위험도 enum 상수
MCP_WRITE_TOOL_RISK_CLASSES = ("safe", "moderate", "destructive", "admin_only")

# 향후 write/edit tool 후보 분류표.
# 실제 등록(mcp.tool() 데코레이터)은 안 됨 — 분류 의도 + Web API 경로 명시만.
# 각 항목 필수 키: risk, web_api_path, method, permission, audit
MCP_WRITE_TOOL_CLASSIFICATION: dict[str, dict] = {
    "create_event": {
        "risk": "moderate",
        "web_api_path": "/api/events",
        "method": "POST",
        "permission": "editor",
        "audit": True,
    },
    "add_checklist_item": {
        "risk": "moderate",
        "web_api_path": "/api/checklists/{id}/items",
        "method": "POST",
        "permission": "editor",
        "audit": True,
    },
    "update_event": {
        "risk": "moderate",
        "web_api_path": "/api/events/{id}",
        "method": "PUT",
        "permission": "editor",
        "audit": True,
    },
    "delete_event": {
        "risk": "destructive",
        "web_api_path": "/api/events/{id}",
        "method": "DELETE",
        "permission": "editor",
        "audit": True,
    },
    "delete_document": {
        "risk": "destructive",
        "web_api_path": "/api/doc/{id}",
        "method": "DELETE",
        "permission": "editor",
        "audit": True,
    },
    "bulk_project_update": {
        "risk": "destructive",
        "web_api_path": "/api/projects/{name}",
        "method": "PUT",
        "permission": "admin",
        "audit": True,
    },
}

# 우선 시범 후보 — 좁은 범위에서 시작 (write tool 추가 시 이 두 개가 첫 후보)
MCP_WRITE_PRIORITY_CANDIDATES = ("create_event", "add_checklist_item")


def is_destructive(tool_name: str) -> bool:
    """tool_name이 destructive 이상 위험도이면 True."""
    spec = MCP_WRITE_TOOL_CLASSIFICATION.get(tool_name)
    if not spec:
        return False
    return spec["risk"] in ("destructive", "admin_only")


def web_api_target(tool_name: str) -> dict | None:
    """tool_name의 Web API 경로·메서드 정보를 반환. 미등록 시 None."""
    spec = MCP_WRITE_TOOL_CLASSIFICATION.get(tool_name)
    if not spec:
        return None
    return {"path": spec["web_api_path"], "method": spec["method"]}
