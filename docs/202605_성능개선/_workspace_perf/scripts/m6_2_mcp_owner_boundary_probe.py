"""
M6-2 MCP write owner boundary probe.

확인 시나리오:
1. mcp_command_registry import 부작용 0 (외부 import 0건)
2. MCP_WRITE_TOOL_RISK_CLASSES 4종 상수 존재
3. MCP_WRITE_TOOL_CLASSIFICATION 6+ 항목 + 각 항목 시그니처
4. MCP_WRITE_PRIORITY_CANDIDATES 2개 이상 + classification에 존재
5. is_destructive / web_api_target 헬퍼 동작
6. mcp_server에 _call_web_api_command 정의 + 시그니처
7. mcp_server write owner 원칙 grep — SQL/SQLite write 패턴 0건
8. mcp_server db.create_/db.update_/db.delete_/db.add_ 실제 호출 0건
9. mcp_command_registry 외부 import 0건 (AST 검증)
"""
import ast
import importlib
import inspect
import re
import sys
from pathlib import Path

# UTF-8 강제 (Windows 콘솔 안전)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")


# ── 1. import 부작용 0: AST로 외부 import 확인 ────────────────────────────
print("\n[1] mcp_command_registry 외부 import 0건")
reg_path = ROOT / "mcp_command_registry.py"
reg_src = reg_path.read_text(encoding="utf-8")
tree = ast.parse(reg_src, filename="mcp_command_registry.py")
external_imports = [
    n for n in ast.walk(tree)
    if isinstance(n, (ast.Import, ast.ImportFrom))
]
if external_imports:
    fail(f"외부 import 발견: {[ast.dump(n) for n in external_imports]}")
else:
    ok("외부 import 0건 (AST 검증)")

# ── 2. MCP_WRITE_TOOL_RISK_CLASSES 4종 ────────────────────────────────────
print("\n[2] MCP_WRITE_TOOL_RISK_CLASSES 4종 상수")
import mcp_command_registry as reg

expected_classes = ("safe", "moderate", "destructive", "admin_only")
if reg.MCP_WRITE_TOOL_RISK_CLASSES == expected_classes:
    ok(f"MCP_WRITE_TOOL_RISK_CLASSES = {reg.MCP_WRITE_TOOL_RISK_CLASSES}")
else:
    fail(f"MCP_WRITE_TOOL_RISK_CLASSES 불일치: {reg.MCP_WRITE_TOOL_RISK_CLASSES}")

# ── 3. MCP_WRITE_TOOL_CLASSIFICATION 6+ 항목 + 시그니처 ───────────────────
print("\n[3] MCP_WRITE_TOOL_CLASSIFICATION 항목 수 + 시그니처")
clf = reg.MCP_WRITE_TOOL_CLASSIFICATION
if len(clf) >= 6:
    ok(f"항목 수: {len(clf)} >= 6")
else:
    fail(f"항목 수 부족: {len(clf)}")

required_keys = {"risk", "web_api_path", "method", "permission", "audit"}
all_valid = True
for name, spec in clf.items():
    missing = required_keys - spec.keys()
    if missing:
        fail(f"  {name}: 누락 키 {missing}")
        all_valid = False
if all_valid:
    ok("모든 항목에 필수 키 5종 존재")

# risk 값이 유효한지 확인
valid_risks = set(reg.MCP_WRITE_TOOL_RISK_CLASSES)
all_risk_valid = True
for name, spec in clf.items():
    if spec["risk"] not in valid_risks:
        fail(f"  {name}: 유효하지 않은 risk '{spec['risk']}'")
        all_risk_valid = False
if all_risk_valid:
    ok("모든 항목 risk 값이 MCP_WRITE_TOOL_RISK_CLASSES에 속함")

# ── 4. MCP_WRITE_PRIORITY_CANDIDATES ──────────────────────────────────────
print("\n[4] MCP_WRITE_PRIORITY_CANDIDATES")
cands = reg.MCP_WRITE_PRIORITY_CANDIDATES
if len(cands) >= 2:
    ok(f"우선 후보 {len(cands)}개: {cands}")
else:
    fail(f"우선 후보 부족: {cands}")

missing_in_clf = [c for c in cands if c not in clf]
if not missing_in_clf:
    ok("모든 우선 후보가 MCP_WRITE_TOOL_CLASSIFICATION에 존재")
else:
    fail(f"분류표에 없는 우선 후보: {missing_in_clf}")

# ── 5. 헬퍼 동작 ──────────────────────────────────────────────────────────
print("\n[5] 헬퍼 함수 동작")
# is_destructive
if reg.is_destructive("delete_event") is True:
    ok("is_destructive('delete_event') = True")
else:
    fail("is_destructive('delete_event') != True")

if reg.is_destructive("create_event") is False:
    ok("is_destructive('create_event') = False")
else:
    fail("is_destructive('create_event') != False")

if reg.is_destructive("nonexistent_tool") is False:
    ok("is_destructive('nonexistent_tool') = False (미등록)")
else:
    fail("is_destructive('nonexistent_tool') != False")

# web_api_target
target = reg.web_api_target("create_event")
if target and "path" in target and "method" in target:
    ok(f"web_api_target('create_event') = {target}")
else:
    fail(f"web_api_target('create_event') 잘못된 반환: {target}")

if reg.web_api_target("nonexistent_tool") is None:
    ok("web_api_target('nonexistent_tool') = None (미등록)")
else:
    fail("web_api_target('nonexistent_tool') != None")

# ── 6. mcp_server._call_web_api_command 정의 + 시그니처 ───────────────────
print("\n[6] mcp_server._call_web_api_command")
import mcp_server as ms

if hasattr(ms, "_call_web_api_command"):
    ok("_call_web_api_command 존재")
else:
    fail("_call_web_api_command 미존재")
    print("\n=== SUMMARY ===")
    print(f"PASS: {PASS}, FAIL: {FAIL}")
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

# NotImplementedError 발생 확인
import asyncio
try:
    asyncio.run(ms._call_web_api_command("create_event", {}, None))
    fail("NotImplementedError가 발생하지 않음")
except NotImplementedError as e:
    ok(f"NotImplementedError 발생 (M6-2 boundary 잠금): {str(e)[:60]}")
except Exception as e:
    fail(f"예상치 못한 예외: {type(e).__name__}: {e}")

# unknown tool_name
try:
    asyncio.run(ms._call_web_api_command("unknown_tool", {}, None))
    fail("ValueError가 발생하지 않음")
except ValueError as e:
    ok(f"ValueError 발생 (미등록 tool): {e}")
except NotImplementedError:
    # 미등록 tool → ValueError 먼저 발생해야 함
    fail("미등록 tool에 ValueError 대신 NotImplementedError 발생")
except Exception as e:
    fail(f"예상치 못한 예외: {type(e).__name__}: {e}")

# ── 7. mcp_server write owner grep — SQL/SQLite 직접 사용 0건 ─────────────
print("\n[7] mcp_server.py write owner grep")
ms_src = (ROOT / "mcp_server.py").read_text(encoding="utf-8")

# AST로 실제 코드 노드만 분석 (주석/docstring 제외 문자열 grep)
# 접근: 소스에서 실제 ast.Str/Constant 노드 + ast.Call 노드를 검사
ms_tree = ast.parse(ms_src, filename="mcp_server.py")

# SQL 패턴 — 실제 문자열 리터럴에서만 검색
sql_pattern = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.IGNORECASE)
sql_violations = []
for node in ast.walk(ms_tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if sql_pattern.search(node.value):
            sql_violations.append((node.lineno, node.value[:80]))

if not sql_violations:
    ok("SQL write 패턴(INSERT/UPDATE/DELETE) 0건 (문자열 리터럴 AST 검사)")
else:
    fail(f"SQL write 패턴 발견: {sql_violations}")

# sqlite3.connect 직접 호출
sqlite_imports = [
    n for n in ast.walk(ms_tree)
    if isinstance(n, (ast.Import, ast.ImportFrom))
    and any(
        (getattr(a, "name", None) or "").startswith("sqlite3")
        for a in getattr(n, "names", [])
    ) or (
        isinstance(n, ast.ImportFrom)
        and (getattr(n, "module", None) or "").startswith("sqlite3")
    )
]
if not sqlite_imports:
    ok("sqlite3 직접 import 0건")
else:
    fail(f"sqlite3 직접 import 발견: {sqlite_imports}")

# ── 8. db.create_/update_/delete_/add_ 실제 호출 (Call 노드) ──────────────
print("\n[8] mcp_server.py db write 함수 호출 0건")
write_call_pattern = re.compile(r"^(create_|update_|delete_|add_|write_)")
write_calls = []
for node in ast.walk(ms_tree):
    # db.func_name(...) 패턴
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            # obj가 'db' 이름이고, attr이 write 패턴
            if isinstance(func.value, ast.Name) and func.value.id == "db":
                if write_call_pattern.match(func.attr):
                    write_calls.append((node.lineno, f"db.{func.attr}"))

if not write_calls:
    ok("db.create_/update_/delete_/add_/write_ 호출 0건 (AST Call 노드 검사)")
else:
    fail(f"db write 호출 발견: {write_calls}")

# ── 9. mcp_command_registry 외부 import 0건 재확인 (모듈 __dict__ 검사) ───
print("\n[9] mcp_command_registry 순수 데이터 모듈 재확인")
module_imported = [
    k for k, v in vars(reg).items()
    if inspect.ismodule(v) and not k.startswith("_")
]
if not module_imported:
    ok("mcp_command_registry namespace에 외부 모듈 0건")
else:
    fail(f"외부 모듈 발견: {module_imported}")

# ── Summary ────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"PASS: {PASS}, FAIL: {FAIL}")
if FAIL:
    sys.exit(1)
print("ALL PASS")
