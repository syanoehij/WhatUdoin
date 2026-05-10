"""
M6-1 + M6-2: MCP write owner boundary 회귀 테스트.

확인 시나리오:
1. mcp_command_registry 외부 import 0건 (AST)
2. MCP_WRITE_TOOL_RISK_CLASSES 4종 상수
3. MCP_WRITE_TOOL_CLASSIFICATION 6+ 항목 + 필수 키 5종
4. MCP_WRITE_PRIORITY_CANDIDATES 2개 이상 + classification에 존재
5. is_destructive / web_api_target 헬퍼 동작
6. mcp_server._call_web_api_command 정의 + 시그니처 (tool_name, payload, ctx)
7. _call_web_api_command: 미등록 tool → ValueError, 등록 tool → NotImplementedError
8. mcp_server.py SQL write 패턴 0건 (AST 문자열 리터럴 검사)
9. mcp_server.py sqlite3 직접 import 0건
10. mcp_server.py db.create_/update_/delete_/add_ 실제 호출 0건 (AST)
11. supervisor.py WEB_API_INTERNAL_URL_ENV 상수 존재

Run:
    python tests/phase75_m6_mcp_owner_boundary.py
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import re
import sys
from pathlib import Path

# UTF-8 강제
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_PASS = 0
_FAIL = 0
_ERRORS: list[str] = []


def ok(label: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  PASS  {label}")


def fail(label: str) -> None:
    global _FAIL
    _FAIL += 1
    _ERRORS.append(label)
    print(f"  FAIL  {label}")


# ── [1] mcp_command_registry 외부 import 0건 (AST) ────────────────────────
print("\n[1] mcp_command_registry 외부 import 0건 (AST)")
reg_path = ROOT / "mcp_command_registry.py"
assert reg_path.exists(), f"mcp_command_registry.py 미존재: {reg_path}"
reg_src = reg_path.read_text(encoding="utf-8")
reg_tree = ast.parse(reg_src, filename="mcp_command_registry.py")
ext_imports = [n for n in ast.walk(reg_tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
if ext_imports:
    fail(f"외부 import {len(ext_imports)}건: {[ast.dump(n) for n in ext_imports]}")
else:
    ok("외부 import 0건")

# ── [2] MCP_WRITE_TOOL_RISK_CLASSES ───────────────────────────────────────
print("\n[2] MCP_WRITE_TOOL_RISK_CLASSES 4종")
import mcp_command_registry as reg  # noqa: E402

expected_classes = ("safe", "moderate", "destructive", "admin_only")
if reg.MCP_WRITE_TOOL_RISK_CLASSES == expected_classes:
    ok(f"4종 모두 존재: {reg.MCP_WRITE_TOOL_RISK_CLASSES}")
else:
    fail(f"불일치: {reg.MCP_WRITE_TOOL_RISK_CLASSES}")

# ── [3] MCP_WRITE_TOOL_CLASSIFICATION ─────────────────────────────────────
print("\n[3] MCP_WRITE_TOOL_CLASSIFICATION")
clf = reg.MCP_WRITE_TOOL_CLASSIFICATION

if len(clf) >= 6:
    ok(f"항목 수 {len(clf)} >= 6")
else:
    fail(f"항목 수 부족: {len(clf)}")

required_keys = {"risk", "web_api_path", "method", "permission", "audit"}
sig_all_ok = True
for tname, spec in clf.items():
    missing = required_keys - spec.keys()
    if missing:
        fail(f"  {tname}: 누락 키 {missing}")
        sig_all_ok = False
if sig_all_ok:
    ok("모든 항목에 필수 키 5종 존재")

valid_risks = set(reg.MCP_WRITE_TOOL_RISK_CLASSES)
risk_all_ok = True
for tname, spec in clf.items():
    if spec["risk"] not in valid_risks:
        fail(f"  {tname}: 유효하지 않은 risk '{spec['risk']}'")
        risk_all_ok = False
if risk_all_ok:
    ok("모든 항목 risk 값이 MCP_WRITE_TOOL_RISK_CLASSES에 속함")

# ── [4] MCP_WRITE_PRIORITY_CANDIDATES ─────────────────────────────────────
print("\n[4] MCP_WRITE_PRIORITY_CANDIDATES")
cands = reg.MCP_WRITE_PRIORITY_CANDIDATES
if len(cands) >= 2:
    ok(f"우선 후보 {len(cands)}개: {cands}")
else:
    fail(f"후보 부족: {cands}")

missing_in_clf = [c for c in cands if c not in clf]
if not missing_in_clf:
    ok("모든 우선 후보가 분류표에 존재")
else:
    fail(f"분류표 미존재 후보: {missing_in_clf}")

# ── [5] 헬퍼 함수 동작 ────────────────────────────────────────────────────
print("\n[5] 헬퍼 함수")
if reg.is_destructive("delete_event") is True:
    ok("is_destructive('delete_event') = True")
else:
    fail("is_destructive('delete_event') != True")

if reg.is_destructive("create_event") is False:
    ok("is_destructive('create_event') = False")
else:
    fail("is_destructive('create_event') != False")

if reg.is_destructive("nonexistent") is False:
    ok("is_destructive 미등록 = False")
else:
    fail("is_destructive 미등록 != False")

target = reg.web_api_target("create_event")
if target and "path" in target and "method" in target:
    ok(f"web_api_target('create_event') = {target}")
else:
    fail(f"web_api_target('create_event') 잘못된 반환: {target}")

if reg.web_api_target("nonexistent") is None:
    ok("web_api_target 미등록 = None")
else:
    fail("web_api_target 미등록 != None")

# ── [6] mcp_server._call_web_api_command 시그니처 ─────────────────────────
print("\n[6] mcp_server._call_web_api_command")
import mcp_server as ms  # noqa: E402

if hasattr(ms, "_call_web_api_command"):
    ok("_call_web_api_command 존재")
else:
    fail("_call_web_api_command 미존재")
    print("\n[ABORT] _call_web_api_command 미존재. 이후 테스트 생략.")
    print(f"\n{'='*50}")
    print(f"PASS: {_PASS}, FAIL: {_FAIL}")
    sys.exit(1)

sig = inspect.signature(ms._call_web_api_command)
params = list(sig.parameters.keys())
if params == ["tool_name", "payload", "ctx"]:
    ok(f"시그니처 OK: {params}")
else:
    fail(f"시그니처 불일치: {params}")

if inspect.iscoroutinefunction(ms._call_web_api_command):
    ok("async def 확인")
else:
    fail("_call_web_api_command가 async가 아님")

# ── [7] NotImplementedError / ValueError 발생 확인 ────────────────────────
print("\n[7] boundary 예외 발생")
try:
    asyncio.run(ms._call_web_api_command("create_event", {}, None))
    fail("등록된 tool에서 NotImplementedError 미발생")
except NotImplementedError as e:
    ok(f"NotImplementedError (M6-2 boundary): {str(e)[:60]}")
except Exception as e:
    fail(f"예상 밖 예외: {type(e).__name__}: {e}")

try:
    asyncio.run(ms._call_web_api_command("unknown_tool_xyz", {}, None))
    fail("미등록 tool에서 ValueError 미발생")
except ValueError as e:
    ok(f"ValueError (미등록 tool): {e}")
except Exception as e:
    fail(f"예상 밖 예외: {type(e).__name__}: {e}")

# ── [8] mcp_server.py SQL write 패턴 0건 ─────────────────────────────────
print("\n[8] mcp_server.py SQL write 패턴 0건")
ms_src = (ROOT / "mcp_server.py").read_text(encoding="utf-8")
ms_tree = ast.parse(ms_src, filename="mcp_server.py")

sql_pattern = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.IGNORECASE
)
sql_violations: list[tuple[int, str]] = []
for node in ast.walk(ms_tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if sql_pattern.search(node.value):
            sql_violations.append((node.lineno, node.value[:80]))

if not sql_violations:
    ok("SQL write 패턴 0건 (AST Constant 검사)")
else:
    fail(f"SQL write 발견: {sql_violations}")

# ── [9] sqlite3 직접 import 0건 ───────────────────────────────────────────
print("\n[9] mcp_server.py sqlite3 직접 import 0건")
sqlite_imports = []
for node in ast.walk(ms_tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("sqlite3"):
                sqlite_imports.append((node.lineno, alias.name))
    elif isinstance(node, ast.ImportFrom):
        if (node.module or "").startswith("sqlite3"):
            sqlite_imports.append((node.lineno, node.module))

if not sqlite_imports:
    ok("sqlite3 직접 import 0건")
else:
    fail(f"sqlite3 직접 import 발견: {sqlite_imports}")

# ── [10] db.create_/update_/delete_/add_ 실제 호출 0건 ───────────────────
print("\n[10] mcp_server.py db write 함수 호출 0건")
write_call_pattern = re.compile(r"^(create_|update_|delete_|add_|write_)")
write_calls: list[tuple[int, str]] = []
for node in ast.walk(ms_tree):
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id == "db":
                if write_call_pattern.match(func.attr):
                    write_calls.append((node.lineno, f"db.{func.attr}"))

if not write_calls:
    ok("db write 함수 호출 0건 (AST Call 노드 검사)")
else:
    fail(f"db write 호출 발견: {write_calls}")

# ── [11] supervisor.py WEB_API_INTERNAL_URL_ENV 상수 ─────────────────────
print("\n[11] supervisor.py WEB_API_INTERNAL_URL_ENV 상수")
import supervisor  # noqa: E402

if hasattr(supervisor, "WEB_API_INTERNAL_URL_ENV"):
    val = supervisor.WEB_API_INTERNAL_URL_ENV
    if val == "WHATUDOIN_WEB_API_INTERNAL_URL":
        ok(f"WEB_API_INTERNAL_URL_ENV = '{val}'")
    else:
        fail(f"WEB_API_INTERNAL_URL_ENV 값 불일치: '{val}'")
else:
    fail("supervisor.WEB_API_INTERNAL_URL_ENV 미존재")

# ── Summary ────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"PASS: {_PASS}, FAIL: {_FAIL}")
if _ERRORS:
    print("\n실패 항목:")
    for e in _ERRORS:
        print(f"  - {e}")
    sys.exit(1)
print("ALL PASS")
