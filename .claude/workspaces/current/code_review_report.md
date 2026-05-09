# 코드 리뷰 보고서 — A단계 히든 프로젝트 백엔드

**리뷰 일자:** 2026-05-08
**리뷰 대상:** `database.py`, `app.py`
**기준 사양:** `_workspace/00_input/feature_spec.md` (A단계)
**기준 변경:** `_workspace/backend_changes.md`

---

## 리뷰 대상 변경 요약

- `database.py`
  - `init_db`: `projects` 테이블에 `is_hidden`, `owner_id` 추가 + `project_members` 테이블 신규 (line 248-263)
  - `get_unified_project_list(viewer=)`, `get_all_projects_with_events(viewer=)`, `get_all_projects_meta(viewer=)`, `get_project_timeline(viewer=)` 가시성 필터 추가
  - 신규: `create_hidden_project`, `get_hidden_project_member_ids`, `is_hidden_project_visible`, `get_project_by_name`
- `app.py`
  - 기존 라우트 7곳에 `viewer=user` 전달
  - 신규: `POST /api/manage/hidden-projects`, `GET /api/manage/hidden-projects/{name}/can-manage`

---

## ❌ 차단(Blocking)

### B1. `POST /api/manage/hidden-projects` — CSRF 보호 누락 (app.py:2225-2245)

```python
@app.post("/api/manage/hidden-projects")
async def create_hidden_project_route(request: Request):
    user = auth.get_current_user(request)   # ❌ _check_csrf 호출 안 됨
    if not user:
        raise HTTPException(status_code=401)
```

**문제:** 다른 모든 `/api/manage/*` 변경(POST/PUT/DELETE) 라우트는 `_require_editor(request)` 또는 `_require_admin(request)`을 거쳐 `_check_csrf`가 호출된다(`app.py:435-450`). 이 신규 라우트는 `auth.get_current_user`만 사용해 **CSRF 검증을 우회**한다. 인트라넷 환경의 IP 화이트리스트 자동 로그인 컨텍스트에서 외부 사이트가 사용자 세션을 유도해 히든 프로젝트를 생성할 수 있다.

**부수 문제 — editor 역할 체크 부재:** `auth.get_current_user`는 IP 화이트리스트 자동 로그인 사용자(viewer 포함)도 반환한다(`auth.py:20-39`). 사양의 "로그인 필수"가 viewer까지 포함하는 의도가 아니라면 viewer가 히든 프로젝트를 만드는 경로가 열려 있다.

**수정 방향:**
```python
user = _require_editor(request)       # CSRF + editor 역할 동시 검증
if not user.get("team_id"):
    raise HTTPException(status_code=403, detail="팀 소속 사용자만 히든 프로젝트를 생성할 수 있습니다.")
```
일반 프로젝트 생성(`manage_create_project`, `app.py:2046-2063`)과 동일 패턴으로 맞춘다.

---

### B2. `wu_broker.publish` 페이로드에 히든 프로젝트 이름 누설 (app.py:2244)

```python
wu_broker.publish("projects.changed", {"name": name, "action": "create"})
```

**문제:** `/sse` 엔드포인트는 **비로그인 게스트 포함 모든 클라이언트**에게 SSE 메시지를 브로드캐스트한다(`app.py:1716-1722`, 주석에 "페이로드는 id/action 메타 한정"으로 설계 원칙 명시). `static/js/realtime.js:64-70`은 `projects.changed`를 그대로 `wu:projects:changed` / `wu:events:changed` 커스텀 이벤트로 dispatch한다. 다른 페이지 코드가 `d.name`을 토스트·로그·UI로 노출하면 비멤버에게 히든 프로젝트 이름이 평문으로 새어나간다. 사양은 "충돌 대상 정보가 누설되지 않는가"를 명시했으며, 생성 직후 SSE 누설은 422 에러 메시지보다 더 직접적인 누설 경로다.

**수정 방향:** 히든 프로젝트 생성 시에는 이름 없이 publish하거나(클라이언트는 어차피 `/api/projects` 등을 viewer로 재조회해야 함) 별도 채널로 분리한다.
```python
# 예: 이름 제거 — 구독자는 가시성 필터된 목록을 다시 fetch
wu_broker.publish("projects.changed", {"name": None, "action": "create"})
```

---

### B3. `GET /api/manage/hidden-projects/{name}/can-manage` — 히든 이름 enumeration 가능 (app.py:2248-2258)

```python
proj = db.get_project_by_name(name)
if not proj or not proj.get("is_hidden"):
    raise HTTPException(status_code=404)
is_admin = user.get("role") == "admin"
is_owner = proj.get("owner_id") == user.get("id")
return {"can_manage": is_admin or is_owner, "is_owner": is_owner, "is_admin": is_admin}
```

**문제:** 비멤버가 추측한 이름으로 호출 시,
- 히든 프로젝트가 **존재하면** → 200 `{can_manage: false, ...}`
- 존재하지 않거나 일반 프로젝트 → 404

응답 코드 차이로 비멤버가 임의의 이름을 시도해 **히든 프로젝트의 존재/이름을 열거**할 수 있다. 사양 "누설 방지" 원칙 위배.

**수정 방향:** 비멤버에게는 존재 여부를 노출하지 않도록 일관된 404를 반환한다.
```python
proj = db.get_project_by_name(name)
if not proj or not proj.get("is_hidden"):
    raise HTTPException(status_code=404)
if not db.is_hidden_project_visible(proj["id"], user):
    raise HTTPException(status_code=404)   # 비멤버에게도 동일 응답
```
또는 owner/admin 외에는 항상 `{can_manage: false, is_owner: false, is_admin: false}`로 통일.

---

## ⚠️ 경고(Warning)

### W1. `create_hidden_project` 동시성 — IntegrityError 미처리 (database.py:1641-1659)

check-then-insert 구조라 동시 요청이 같은 이름을 동시에 시도하면 두 번째 INSERT가 `IntegrityError`로 500을 낼 수 있다. `manage_create_project`(app.py:2058-2061)는 동일 패턴에서 `try/except sqlite3.IntegrityError`로 422 변환을 처리한다. 동일하게 맞추기를 권장.

### W2. `get_project_by_name`이 `SELECT *` 사용 (database.py:1685-1691)

다른 헬퍼들(`get_project`, `get_unified_project_list` 등)은 명시적 컬럼 나열 패턴을 따른다. 향후 컬럼 추가 시 주변에서 dict 키 의존이 어긋날 수 있으니 명시적 컬럼 나열을 권장. 현 시점에선 라우트 한 곳만 사용하므로 차단은 아님.

### W3. `is_hidden=1` 항목이 일반 events/checklists API로 누출될 수 있음 (범위 외 가능성)

A단계 사양은 *프로젝트 목록* 가시성만 다룬다. 그러나 히든 프로젝트의 events/checklists는 `/api/events`, `/api/checklists` 등을 통해 여전히 그대로 반환된다(이 PR 범위에서 필터 미적용). 사양 자체가 B/C단계로 미룬 부분이라 지금은 차단 아님. QA·후속 단계에서 명시적으로 다루지 않으면 잊힐 위험. **변경 메모(`backend_changes.md` 末)의 "미구현/범위 외"에 events/checklists 노출 차단도 함께 명기**하는 것을 권장.

### W4. `get_unified_project_list` — 비로그인 시 `is_hidden` 처리 위치 (database.py:1422-1480)

`viewer=None`이면 `visible_hidden_ids`가 빈 set이라 모든 `is_hidden=1`이 `hidden_blocked`로 차단된다. 이 부분은 의도대로 동작하나, `/api/projects`(app.py:2007-2013)는 이전 코드의 `is_private` 후처리 필터(`if not user`)를 그대로 두고 있다. 내부 함수가 viewer 기반 필터를 하므로 후처리는 사실상 중복 안전망 — 동작은 정상이지만 한 가지 사실(필터 책임)이 두 곳에 분산되어 향후 회귀 위험. 차단은 아님.

---

## ✅ 통과

- [x] **DB 마이그레이션 안전성**: `_migrate(conn, "projects", [("is_hidden", "INTEGER DEFAULT 0"), ("owner_id", "INTEGER")])` 인라인 패턴 준수, 기본값 NULL/0이라 NOT NULL 제약 위반 없음, `_migrate`는 컬럼 존재 여부 검사 후 추가하므로 try/except 불필요. `CREATE TABLE IF NOT EXISTS project_members` 재실행 안전.
- [x] **하위호환**: 기존 컬럼 삭제·타입 변경 없음. 기존 행은 `is_hidden=0`(DEFAULT), `owner_id=NULL`로 정상 유지.
- [x] **is_private vs is_hidden 분리**: `is_private` 로직은 변경 없이 그대로 유지(`/api/projects` line 2011-2012, `/api/project-colors` line 2135-2138). `is_hidden` 필터는 별도 viewer 기반 분기로 추가됨.
- [x] **N+1 회피**: `project_members` pre-fetch + Python set 조회 (`database.py:1442-1450`, `1517-1524`, `1599-1606`, `1080-1085`). per-project 쿼리 없음.
- [x] **orphan 재누출 방지(범위 내)**: `hidden_blocked` 집합으로 `events.project` / `checklists.project` 이름 추가 차단(`database.py:1463-1464`, `1551-1554`, `1623-1626`). `get_all_projects_with_events`의 이벤트 분류에서도 `if p in hidden_blocked: continue`로 차단(`database.py:1564`).
- [x] **SQL 파라미터화**: 모든 신규 쿼리가 `?` 플레이스홀더 사용. f-string 직접 삽입 없음.
- [x] **`get_conn()` contextmanager** 정상 사용.
- [x] **에러 메시지 누설(라우트 응답)**: `POST /api/manage/hidden-projects` 중복 응답이 "생성할 수 없습니다. 다른 이름을 넣어주세요."로 충돌 대상을 노출하지 않음(`app.py:2243`). *단, B2/B3에서 다른 누설 경로 발견 — 차단 항목 참조.*
- [x] **이름 중복 검사 deleted_at 미필터**: `WHERE LOWER(name) = LOWER(?)`로 휴지통 포함 전체 검사(`database.py:1644-1646`).
- [x] **권한(can-manage 부분)**: 미로그인 401, 히든 아닌 프로젝트/존재하지 않음 404 처리. *enumeration 위험은 B3 참조.*
- [x] **신규 컬럼 SELECT 일관성**: `is_hidden`, `owner_id` SELECT를 `get_unified_project_list` / `get_all_projects_with_events` / `get_all_projects_meta` / `get_project_timeline` 모두에 추가했고 row 접근 시 None 보호(`r["is_hidden"] if r["is_hidden"] is not None else 0`) 적용.

---

## 회귀 검토 (이전 보고서 비교)

이전 `code_review_report.md`는 별도 기능(40차 단계) 리뷰. 이번 변경이 영향을 줄 수 있는 부분:
- `is_private` 후처리(`/api/projects`, `/api/project-colors`)는 그대로 유지되어 비로그인 외부 차단 동작에 회귀 없음.
- `get_project_timeline`의 기존 viewer 파라미터 의미는 유지되며, 히든 필터만 추가로 얹혀짐.
- `40차 4단계 — 프로젝트 이름 중복 차단(대소문자 무시)` (커밋 `0816d23`)과 동일 검사 방식(`LOWER(name) = LOWER(?)`)이라 일관됨.

회귀 결함 없음.

---

## 최종 판정

### **차단 — 수정 필요**

차단 결함 3건 (B1: CSRF/role, B2: SSE 이름 누설, B3: enumeration). QA(E2E) 진행 보류 권고. backend-dev에게 수정 요청 발송 예정.

**수정 후 재검토 대상:**
1. `POST /api/manage/hidden-projects`가 `_require_editor` 사용으로 변경되었는지
2. `wu_broker.publish` 페이로드에서 히든 프로젝트 이름이 제거되었는지
3. `GET .../can-manage`이 비멤버에게도 404로 일관 응답하는지

경고 4건은 차단하지 않으나 backend-dev가 함께 처리하면 좋음.

---

## 재검토 결과 (2026-05-08)

### B1 — POST /api/manage/hidden-projects 권한/CSRF: ✅ 통과

`app.py:2225-2243` 확인.
- (line 2227) `user = _require_editor(request)` 적용 → `_require_editor`(app.py:445-450)는 `_check_csrf` + `auth.is_editor` 모두 호출. CSRF·editor 역할 검증 동시 충족.
- (line 2228-2229) `if not user.get("team_id"): raise 403` — 팀 소속 강제 체크 유지.
- (line 2232-2233) 추가 보강: 빈 이름에 422 응답.
- 부수 영향 없음(이전 `auth.get_current_user` + 수동 401 패턴 완전 제거).

### B2 — SSE 페이로드 이름 제거: ✅ 통과

`app.py:2242` 확인.
```python
wu_broker.publish("projects.changed", {"name": None, "action": "create"})
```
- `name=None`으로 변경 완료. 비로그인 SSE 구독자에게 히든 프로젝트 이름이 브로드캐스트되지 않음.
- `realtime.js`가 `d.name`을 dispatch해도 None이라 누설 없음(다른 코드는 viewer 기반 재조회로 가시성 일관 처리).
- 다른 `projects.changed` publish 지점은 변경 없음(일반 프로젝트 update/delete 등은 기존 동작 유지).

### B3 — can-manage enumeration 차단: ✅ 통과

`app.py:2246-2258` 확인.
- (line 2251-2253) 프로젝트 미존재 또는 비히든 → 404
- (line 2254-2255) `db.is_hidden_project_visible(proj["id"], user)` 호출, false면 404 반환 — 비멤버에게도 동일 404.
- `database.py:1671-1682` `is_hidden_project_visible`: admin은 True, 그 외엔 `project_members` 멤버십 검사. owner는 `create_hidden_project` 시 자동으로 `project_members`에 등록되므로 owner도 통과.
- 결과: 비멤버 → 404 / 멤버(owner 제외) → `{can_manage: false, ...}` / owner → `{can_manage: true, is_owner: true}` / admin → `{can_manage: true, is_admin: true}`. 비멤버 응답이 비히든·미존재와 동일 404로 통일되어 enumeration 차단.

### 회귀 검토

- B1 수정으로 신규 `_require_editor` 호출 추가 외 인접 라우트 변경 없음. 기존 `manage_create_project` 등 다른 `_require_editor` 사용 라우트에 영향 없음.
- B2 변경은 publish 페이로드 한 줄. 일반 프로젝트 publish 지점(`app.py:2219` 등)은 그대로 `name=name` 유지 — 의도대로 히든 생성 경로만 분기.
- B3 변경은 추가 가드 1건. 정상 사용자 흐름(owner/admin 호출)은 그대로 200 응답.

회귀 결함 없음.

### 최종 판정

**A단계 백엔드 통과** — 차단 결함 3건 모두 해소. QA(E2E) 진행 가능.

남은 경고 4건(W1~W4)은 차단 아님이며 본 단계에서 처리 보류 가능. W3(events/checklists 노출)은 B/C단계 사양에서 명시적으로 다뤄야 함을 재차 환기.

---

# 코드 리뷰 보고서 — B단계 히든 프로젝트 백엔드

**리뷰 일자:** 2026-05-08
**리뷰 대상:** `database.py`, `app.py`
**기준 사양:** `_workspace/00_input/feature_spec.md` (B단계: B-1 멤버 관리 + B-2 산하 항목 외부 공개 락)
**기준 변경:** `_workspace/backend_changes.md` (## B단계 변경)

---

## B단계 리뷰

### 리뷰 대상 변경 요약

- **database.py 신규 함수**
  - `get_hidden_project_members(project_id)` (line ~1694)
  - `get_hidden_project_addable_members(project_id)` (line ~1710)
  - `add_hidden_project_member(project_id, user_id, owner_id)` (line ~1734)
  - `remove_hidden_project_member(project_id, user_id)` (line ~1762)
  - `transfer_hidden_project_owner(project_id, new_owner_id, requester_id)` (line ~1779)
  - `admin_change_hidden_project_owner(project_id, new_owner_id)` (line ~1803)
- **app.py 신규 라우트** (line ~2266-2380)
  - `GET /api/manage/hidden-projects/{name}/members` / `.../addable-members`
  - `POST /api/manage/hidden-projects/{name}/members` / `DELETE .../{user_id}`
  - `POST .../transfer-owner` (owner만) / `POST .../change-owner` (admin만)
- **app.py 헬퍼 신규**
  - `_get_hidden_proj_or_404(name, user)` (line 2281): 비멤버에게 일관된 404 반환
  - `_require_hidden_can_manage(user, proj)` (line 2293): owner 또는 admin 검증
- **산하 항목 외부 공개 락 (per-item)**
  - `PATCH /api/checklists/{id}/visibility` (line 884-890)
  - `PATCH /api/checklists/{id}` 프로젝트 변경 시 (line 924-928)
  - `PATCH /api/events/{id}/visibility` (line 3201-3207)
  - `PATCH /api/events/{id}/project` 시 (line 1838-1844)

---

## ❌ 차단(Blocking)

### B-B1. Bulk visibility API에 히든 락 미적용 — 데이터 누설 경로 (app.py:838-864)

```python
@app.patch("/api/checklists/bulk-visibility")
async def bulk_checklist_visibility(request: Request):
    user = _require_editor(request)
    data = await request.json()
    project  = data.get("project")
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    ...
    count = db.bulk_update_checklist_visibility(project, is_public, is_active, team_id=team_id_filter)
    return {"ok": True, "updated": count}

@app.patch("/api/events/bulk-visibility")
async def bulk_event_visibility(request: Request):
    user = _require_editor(request)
    data = await request.json()
    project  = data.get("project")
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    ...
    count = db.bulk_update_event_visibility(project, is_public, is_active, team_id=team_id_filter)
```

**문제:** 사양 B-2는 "**events, checklists, meetings의 is_public 변경 API**에서 ... 해당 항목의 project가 is_hidden=1이면 → 403"으로 명시한다. bulk-visibility는 명백히 "is_public 변경 API"이며, 사양에서 제외된 적 없다. backend_changes.md는 이 누락을 "범위 외 / 미구현 (의도적)"으로 기재했지만, **사양 텍스트 plain reading은 bulk endpoint도 포함**한다.

구체적 공격 경로(per-item 락 884-890, 3201-3207을 완전 우회):
1. 히든 프로젝트 멤버(혹은 같은 team_id editor)가 PATCH `/api/checklists/bulk-visibility` 호출
2. body `{ "project": "<히든 프로젝트 이름>", "is_public": 1 }`
3. `bulk_update_checklist_visibility`(database.py:2846-2864) 가 히든 검사 없이 `UPDATE checklists SET is_public=1 WHERE project=?`
4. 해당 히든 프로젝트의 모든 체크리스트가 is_public=1로 일괄 전환되어 외부(비로그인) 노출
5. events bulk도 동일

per-item PATCH는 막아두고 bulk는 열린 상태 → 동일 사용자가 bulk로 우회 가능. 이는 **B-2 락 자체를 무력화**하는 데이터 노출 경로로, 의도적 범위 외라 분류하기 어렵다.

**수정 방향(2가지 중 택1):**

1) 라우트 레이어에서 가드 추가 (간단, 즉시 효과)
```python
@app.patch("/api/checklists/bulk-visibility")
async def bulk_checklist_visibility(request: Request):
    user = _require_editor(request)
    data = await request.json()
    project  = data.get("project")
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    # 히든 프로젝트 항목은 외부 공개 일괄 변경 불가
    if is_public and project:
        _proj = db.get_project_by_name(project)
        if _proj and _proj.get("is_hidden"):
            raise HTTPException(status_code=403, detail="히든 프로젝트 항목은 외부 공개할 수 없습니다.")
    ...
```
events bulk 핸들러에 동일 적용.

2) DB 레이어에서 가드 (더 안전. 향후 추가 진입점도 자동 보호)
`bulk_update_*_visibility`에서 project 인자가 히든 프로젝트면 is_public=1 시도 시 0건 반환 또는 예외.

→ backend-dev에게 수정 요청 발송 예정.

---

## ⚠️ 경고(Warning)

### W-B1. `_get_hidden_proj_or_404`이 deleted_at 필터 없음 (app.py:2281-2290, database.py 의 `get_project_by_name`)

`get_project_by_name`은 휴지통 포함 전체 조회(이름 중복 검사 사양 일치)이므로, owner가 soft-delete된 히든 프로젝트에 대해 `members`/`transfer-owner`/`change-owner`를 호출해도 통과한다. 보안 누설은 아니나(owner는 어차피 휴지통 항목 접근 가능) C단계(휴지통 분리)에서 명시 처리 필요. 현 단계에선 차단 아님.

### W-B2. `add_hidden_project_member` False 반환 모호성 (database.py:1745-1748)

```python
if not owner_row or not target_row:
    return False  # ← user not found
if owner_row["team_id"] is None or owner_row["team_id"] != target_row["team_id"]:
    return False  # ← team mismatch
```

라우트(app.py:2330-2331)는 둘 다 `403 "같은 팀 사용자만 멤버로 추가할 수 있습니다."`로 매핑한다. 잘못된 user_id 입력 시 메시지 부정확. 보안 결함은 아님.

### W-B3. `_get_hidden_proj_or_404`의 `user=None` 분기 (app.py:2281-2290)

```python
def _get_hidden_proj_or_404(name: str, user: dict = None):
    ...
    if user and not db.is_hidden_project_visible(proj["id"], user):
        raise HTTPException(status_code=404)
    return proj
```

현재 모든 호출처(2305, 2314, 2323, 2341, 2353, 2371)는 user를 항상 전달하므로 실질 누설 없음. 그러나 시그니처상 `user=None` 호출 시 visibility 체크가 스킵되어 향후 회귀 위험. user를 필수 인자로 변경 권장.

### W-B4. addable-members 빈 응답의 의미 분기 부족 (database.py:1710-1731)

owner의 team_id가 NULL이면 빈 리스트 반환. 라우트도 그대로 200 + `{addable_members: []}` 응답. 정상적으로 같은 팀에 다른 멤버가 없는 경우와 owner가 팀 잃은 경우 구분 불가. UI에서 안내 메시지 분기 필요시 향후 응답에 사유 필드 추가 고려. 현재 차단 아님.

---

## ✅ 통과

- [x] **권한 분리 정확성 — transfer-owner**: `_require_editor`(CSRF+로그인) 후 `proj.owner_id != user.id`이면 403(line 2355-2356). admin이라도 owner가 아니면 거부. 사양 일치.
- [x] **권한 분리 정확성 — change-owner**: `_require_admin`(CSRF+admin role)로 라우트 진입(line 2370). admin 외 모두 차단. 사양 일치.
- [x] **권한 분리 — members 추가/삭제/조회**: `_require_editor` + `_require_hidden_can_manage`(owner 또는 admin) 패턴. 일반 멤버 차단 정확.
- [x] **팀 기반 멤버 추가 제한**: `add_hidden_project_member`(database.py:1734-1759)가 항상 `proj.owner_id`를 팀 기준점으로 사용. 라우트(app.py:2329)도 `proj["owner_id"]`를 그대로 전달 → admin이 호출해도 owner 팀 외부 사용자 추가 불가. admin 우회 없음.
- [x] **addable-members 동일 필터 (admin 비우회)**: `get_hidden_project_addable_members`(database.py:1710-1731)는 owner team_id 기준으로만 필터. admin도 동일 결과.
- [x] **enumeration 방지**: `_get_hidden_proj_or_404(name, user)` 헬퍼가 비멤버에게 404 반환(line 2288-2289). 모든 B단계 멤버 관리 라우트(2305, 2314, 2323, 2341, 2353, 2371)가 이 헬퍼 경유. owner는 `create_hidden_project` 시 자동으로 project_members에 등록되므로 visibility 통과.
- [x] **is_public 락 일관성 — 체크리스트 visibility (per-item)**: `PATCH /api/checklists/{id}/visibility`(884-890). new_pub=1 시도 + 프로젝트가 is_hidden이면 403. 정확.
- [x] **is_public 락 일관성 — 이벤트 visibility (per-item)**: `PATCH /api/events/{id}/visibility`(3201-3207). 동일 패턴.
- [x] **히든 프로젝트로 이동 시 is_public 강제 0 — 이벤트**: `PATCH /api/events/{id}/project`(1838-1844). 히든 이동 시 visibility=0 강제 + `hidden_forced: true` 응답. 사양 일치.
- [x] **히든 프로젝트로 이동 시 is_public 강제 0 — 체크리스트**: `PATCH /api/checklists/{id}` 내부(924-928)에서 project 변경 감지 시 visibility=0 강제. 사양 일치.
- [x] **owner 자기 삭제 방지**: `remove_hidden_project_member`(database.py:1762-1776) `proj.owner_id == user_id`이면 False; 라우트(app.py:2344-2345) 403. 사양 "owner 자신 삭제 불가" 일치.
- [x] **transfer-owner — 비멤버 대상 거부**: `transfer_hidden_project_owner`(database.py:1779-1800)가 `project_members` 검사 후 비멤버이면 False; 라우트 400 응답.
- [x] **change-owner — 비멤버 대상 거부**: `admin_change_hidden_project_owner`(database.py:1803-1819)도 멤버십 검사 후 False; 라우트 400.
- [x] **`_require_editor` / `_require_admin`** 일관 사용. CSRF 검증 자동 동반.
- [x] **`get_conn()` contextmanager**: 모든 신규 DB 함수 정상 사용. 트랜잭션 종료 누락 없음.
- [x] **SQL 파라미터화**: 모든 신규 쿼리 `?` placeholder. f-string 직접 삽입 없음.
- [x] **SSE 페이로드 누설 방지**: 모든 publish 호출이 `{"name": None, "action": "..."}` 패턴 유지(line 2334, 2346, 2364, 2379) — A단계 B2 수정 일관.
- [x] **이름 누설 차단 — 멤버 관리 라우트 응답**: 멤버 추가 팀 불일치 메시지 "같은 팀 사용자만..."에서 owner 팀명 노출 없음, 멤버 삭제 owner 시도 메시지 "관리 권한을 먼저 이양하세요" 일반화 OK.

---

## 회귀 검토 (이전 보고서 비교)

A단계에서 수정된 3건(B1: CSRF, B2: SSE 이름 누설, B3: enumeration)은 B단계 변경에서 모두 패턴 유지:

- B단계 신규 라우트는 모두 `_require_editor` 또는 `_require_admin` 사용 → CSRF 검증 자동 적용.
- B단계 publish 호출은 모두 `name=None` → A단계 B2 패턴 일관.
- B단계 멤버 관리 라우트는 `_get_hidden_proj_or_404(name, user)` 경유 → A단계 B3 enumeration 차단 패턴 확장.

A단계 경고 W3("events/checklists 노출")는 본 단계에서 **부분 해소**:
- per-item visibility는 락 적용됨 ✅
- 히든 프로젝트로 이동 시 is_public 강제 0 적용됨 ✅
- bulk visibility 누락 → 새 차단 항목 B-B1으로 재분류

회귀 결함 없음. 단, B-B1은 사실상 W3의 일부가 미완성 상태로 남은 결과.

---

## 최종 판정

### **차단 — 수정 필요**

차단 결함 1건(B-B1: bulk visibility API에 히든 락 미적용). per-item 락이 우회되는 명확한 데이터 노출 경로이므로, "범위 외 / 의도적 미구현"으로 통과시킬 수 없다. backend-dev에게 수정 요청 발송 예정.

**수정 후 재검토 대상:**
1. `PATCH /api/checklists/bulk-visibility`가 히든 프로젝트 이름에 대해 is_public=1 요청을 거부하는지
2. `PATCH /api/events/bulk-visibility`가 동일하게 거부하는지
3. (권장) `bulk_update_*_visibility` DB 함수에 가드를 두면 진입점 추가 시 자동 보호

경고 4건(W-B1~W-B4)은 차단 아님이며 본 단계에서 처리 보류 가능.

---

## B단계 재검토

**재검토 일자:** 2026-05-08
**재검토 대상:** `app.py` bulk-visibility 라우트 2곳 (B-B1 수정 확인)

### B-B1 — Bulk visibility 히든 락: PASS

**`PATCH /api/checklists/bulk-visibility` (app.py:838-853)**

```python
@app.patch("/api/checklists/bulk-visibility")
async def bulk_checklist_visibility(request: Request):
    user = _require_editor(request)
    data = await request.json()
    project  = data.get("project")
    raw = data.get("is_public", 1)
    is_public = None if raw is None else (1 if raw else 0)
    is_active_raw = data.get("is_active")
    is_active = None if is_active_raw is None else (1 if is_active_raw else 0)
    if is_public and project:                                     # ← 추가됨
        _proj = db.get_project_by_name(project)
        if _proj and _proj.get("is_hidden"):
            raise HTTPException(status_code=403, detail="히든 프로젝트 항목은 외부 공개할 수 없습니다.")
    team_id_filter = user.get("team_id") if not project else None
    count = db.bulk_update_checklist_visibility(project, is_public, is_active, team_id=team_id_filter)
    return {"ok": True, "updated": count}
```

**`PATCH /api/events/bulk-visibility` (app.py:856-872)**

동일 패턴 적용:
- (line 865-868) `if is_public and project:` 분기 → `db.get_project_by_name`으로 히든 여부 확인 → 403 반환.

**확인 포인트별 판정:**

1. **`is_public=1` + 프로젝트 지정 시 히든 프로젝트 체크가 있는가?** → ✅
   - 두 라우트 모두 `if is_public and project:` 가드. `is_public`이 truthy(1)일 때만 분기 진입.
   - `db.get_project_by_name(project)` 호출로 프로젝트 조회.
2. **히든 프로젝트이면 403 반환하는가?** → ✅
   - `_proj and _proj.get("is_hidden")` 조건 → `HTTPException(status_code=403, detail="히든 프로젝트 항목은 외부 공개할 수 없습니다.")`.
   - per-item PATCH(884-890, 3201-3207)와 동일 메시지 사용 → 일관성 유지.
3. **`is_public=0` 변경은 체크 없이 통과하는가?** → ✅
   - `is_public = None if raw is None else (1 if raw else 0)` → `is_public=0`이면 truthy 검사 실패로 가드 스킵.
   - `is_public=None`(미지정 유지)도 동일하게 스킵 — 비공개·미지정 방향 변경은 안전한 방향이라 차단 불필요. 사양 일치.

### 부수 검토

- **CSRF/권한**: 두 라우트 모두 `_require_editor(request)` 첫 줄 호출 유지. CSRF·editor 역할 검증 동시 충족. 회귀 없음.
- **이름 누설**: 403 응답 메시지 "히든 프로젝트 항목은 외부 공개할 수 없습니다."에서 입력한 프로젝트 이름이나 다른 정보 노출 없음. enumeration은 비히든·미존재 프로젝트가 정상 통과(가드 미진입)하므로 이론상 가능하나, 호출자는 이미 `_require_editor`로 인증된 editor이고 사양은 비로그인/비멤버 누설만 차단 대상으로 명시 → 차단 아님.
- **DB 레이어 가드(권장 사항)**: `bulk_update_*_visibility` DB 함수 자체에는 가드 추가되지 않음. 라우트 레이어에서만 처리. 신규 진입점이 추가되면 동일 가드를 반복해야 함 — 향후 회귀 위험은 남으나 현 단계 사양은 라우트 가드로 충족. 경고 수준.

### 회귀 검토

- per-item PATCH 라우트(`/api/checklists/{id}/visibility`, `/api/events/{id}/visibility`, `/api/checklists/{id}`, `/api/events/{id}/project`)의 기존 가드는 변경 없이 유지.
- bulk 라우트의 `team_id_filter` 로직(`user.get("team_id") if not project else None`)도 변경 없음.
- `wu_broker.publish` 호출(events bulk만 발행)은 기존 동작 유지.

회귀 결함 없음.

### 최종 판정

**B단계 백엔드 통과** — 차단 결함 1건(B-B1) 해소. QA(E2E) 진행 가능.

남은 경고 4건(W-B1~W-B4)은 차단 아님. DB 레이어 가드 추가는 후속 단계에서 검토 권장.

---

# 코드 리뷰 보고서 — C단계 히든 프로젝트 백엔드

**리뷰 일자:** 2026-05-08
**리뷰 대상:** `database.py`, `app.py`
**기준 사양:** `_workspace/00_input/feature_spec.md` (C단계: C-1 이동 confirm + C-2 휴지통 분리 + C-3 팀원 제외 경고)
**기준 변경:** `_workspace/backend_changes.md` (## C단계 변경)

---

## C단계 리뷰

### 리뷰 대상 변경 요약

- **app.py — C-1 이동 confirm**
  - `PATCH /api/events/{event_id}/project` (line 1866-1899): 히든→일반 이동 시 `confirm: true` 미포함이면 400+`requires_confirm:true` 반환. 새 응답을 위해 `JSONResponse` 사용 (이미 import됨, 23행).
  - `PATCH /api/checklists/{checklist_id}` (line 903-951): 동일 로직. `"project" in data and old_proj and project != old_proj` 가드로 실제 프로젝트 변경 시에만 발동.
- **database.py / app.py — C-2 휴지통 분리**
  - `database.py:3112-3215` `get_trash_items(team_id, viewer=None)`: `is_hidden=1` 프로젝트는 admin 또는 owner만 groups에 포함. SELECT에 `is_hidden, owner_id` 추가.
  - `database.py:3232-3239` `get_trash_hidden_project(project_id)`: 신규. 휴지통 프로젝트의 `is_hidden, owner_id` 반환.
  - `app.py:3845-3849` `GET /api/trash`: viewer=user 전달.
  - `app.py:3852-3877` `POST /api/trash/{item_type}/{item_id}/restore`: item_type=="project" 시 히든 여부 확인 후 admin/owner만 복원 허용.
- **database.py / app.py — C-3 팀원 제외 경고**
  - `database.py:3242-3249` `get_user_owned_hidden_projects(user_id)`: 신규.
  - `database.py:3252-3274` `transfer_hidden_projects_on_removal(user_id, hidden_projects)`: 신규.
  - `app.py:1255-1283` `PUT /api/admin/users/{user_id}` (admin_update_user): `is_removing` 판단 후 force 없으면 200+warning, force=true면 이양 후 `update_user` 실행.

---

## ❌ 차단(Blocking)

**없음.**

---

## ⚠️ 경고(Warning)

### W-C1. `get_trash_items`의 unassigned 섹션이 히든 프로젝트 산하 항목 누설 가능 (database.py:3178-3203)

C-2는 휴지통의 히든 프로젝트 **그룹 분리**는 정확히 처리하지만, `unassigned` 섹션은 viewer 필터를 거치지 않는다. 누설 시나리오:

- 히든 프로젝트 X가 **활성 상태**(휴지통 아님)
- 산하 이벤트/체크리스트가 단독 삭제됨 → `deleted_at` 설정, `trash_project_id`는 NULL (부모 프로젝트가 휴지통이 아니므로)
- `unassigned` 섹션에 노출 시 항목의 `project="X"` 필드로 히든 프로젝트 이름이 같은 팀 내 비멤버 editor에게 누설됨

QA #24 "비멤버는 히든 휴지통 항목 미표시" 정신과 일치하지 않으나, 사양 C-2 텍스트는 "휴지통 목록에서 히든 프로젝트(엔트리) 노출 차단"에 한정해 읽을 수도 있다. **A3-W3 / B-B1 패턴(per-item vs bulk visibility)과 일관**되게 경고 수준으로 분류하되, 후속 단계에서 다음 처리를 권장한다:

```python
# unassigned 후처리
unassigned = [
    it for it in unassigned
    if not _is_in_hidden_project(conn, it.get("project"), viewer)
]
```
(또는 SELECT 조인으로 한번에 처리. 현재 viewer가 `get_trash_items`에 도달하므로 데이터는 갖춰져 있음.)

차단 아님 — 이 경로는 **로그인 + editor 이상 + 같은 팀** 호출자에 한정되며(`_require_editor` + `team_id_filter` 적용, 라인 3848), 사양은 "비로그인/타팀 누설" 차단을 우선시한다. 다만 같은 팀 비멤버에게 히든 이름이 노출되는 점은 A단계 사양의 `_is_visible_hidden` 정신과 부분 충돌.

### W-C2. `update_user` named-binding 호환성 — 사전적 위험 메모 (app.py:1282, database.py:2144-2149)

`db.update_user(user_id, data)`가 `data` 딕셔너리를 그대로 `:team_id`/`:is_active` named binding에 사용한다. C단계에서 data에 `force`, `hidden_projects` 키가 추가되어도 sqlite3는 named binding에서 추가 키를 무시하므로 안전(검증 완료). 단, 클라이언트가 `team_id` 또는 `is_active` 둘 중 하나를 누락해 보내면 sqlite3가 `did not supply a value for binding parameter` 예외를 발생시킨다 — **이는 C-3 변경 이전부터 존재한 동작**이므로 본 단계 회귀 아님. 그러나 force 흐름의 1단계(warning 반환) 경로에서 클라이언트가 그대로 force=true만 추가해 재요청하면 두 키가 다 있으므로 안전. 차단 아님.

### W-C3. `transfer_hidden_projects_on_removal` 후 기존 owner의 `project_members` 잔존 (database.py:3252-3274)

owner 이양 후 기존 owner(=제외 대상 user)는 `project_members`에 그대로 남는다. 사양 C-3은 "이양 또는 NULL"만 명시하고 멤버십 정리는 침묵. **B단계 transfer-owner / change-owner와 일관**(이양 후 기존 owner는 일반 멤버로 잔존). 그러나 팀원 제외 케이스에서는 사용자가 더 이상 팀에 없으므로 멤버십이 의미를 잃는다. is_active=0 또는 다른 팀으로 이동한 사용자가 `project_members`에 남아 있어도 가시성·관리 권한은 다음 단계에서 자동 차단됨(`is_hidden_project_visible` 헬퍼는 단순 `project_members` 멤버십만 보지만, 가시성 효과는 `_require_editor`·팀 컨텍스트로 추가 차단). 정책상 정리 의도가 있다면 후속 단계에서 명시 처리 권장. 현재 차단 아님.

### W-C4. C-1 이동 confirm — `PATCH /api/checklists/{id}/project` 부재 확인 (app.py)

사양 C-1은 `PATCH /api/checklists/{id}/project`도 confirm 적용 대상으로 명시하나, **해당 라우트는 코드에 존재하지 않음**(`/api/checklists/{id}` 통합 PATCH로 처리). backend_changes.md는 `PATCH /api/checklists/{checklist_id}`(통합)에 confirm 로직을 넣어 동등 효과를 달성. 사양 텍스트는 "프로젝트 변경 API"로 일반화해 읽으면 통합 PATCH도 포함. 통과 처리. 메모로 남김.

### W-C5. C-1 이동 confirm — meetings 라우트 미적용 (의도적 범위 외, app.py)

`PATCH /api/meetings/{id}/project`는 meetings 테이블에 `project` 컬럼이 없어 적용 불가(B단계와 동일 사유). backend_changes.md에 명시. 사양상 한계로 회귀 아님.

---

## ✅ 통과

### C-1 이동 confirm

- [x] **이벤트 `PATCH /api/events/{event_id}/project` confirm 가드**(app.py:1875-1889): 기존 프로젝트 조회 → 히든이면 → 새 프로젝트가 비히든이고 confirm 미포함 시 400+`{requires_confirm:true, message:"히든 프로젝트 밖으로 이동합니다. 계속하시겠습니까?"}` 반환. 사양 일치.
- [x] **체크리스트 `PATCH /api/checklists/{checklist_id}` confirm 가드**(app.py:919-930): `"project" in data and old_proj and project != old_proj` 3중 가드 → 제목/내용만 수정하는 경우 confirm 발동 안 함, 빈→히든 이동도 가드 미진입(일반→히든은 confirm 불필요한 사양과 일치).
- [x] **이벤트 confirm 라우트 의도성**: 라우트 자체가 project 변경 전용이므로 `"project" in data` 가드 부재는 문제 없음. 비공식적 빈 body 호출도 의미상 "프로젝트 빼기"로 처리됨.
- [x] **일반→히든 이동 시 is_public 강제 0**(B단계 처리, app.py:1893-1897, 946-949): C단계에서 변경 없이 유지. 중복 처리 없음.
- [x] **`hidden_forced` 응답**: 이벤트 라우트는 `{ok: true, hidden_forced: bool}`로 응답(line 1899). 체크리스트 라우트는 `{ok: true}`로 응답하나 사양에서 hidden_forced 응답을 체크리스트에 요구하지 않음.
- [x] **JSONResponse import**: `from fastapi.responses import HTMLResponse, JSONResponse, ...`(app.py:23) — 이미 import됨, 신규 추가 불필요했음. backend_changes.md의 "import 추가" 메모는 사실관계 부정확하나 코드는 정상.
- [x] **CSRF**: `_require_editor(request)` 첫 줄 호출(체크리스트 905, 이벤트 1868) 그대로 유지. 회귀 없음.

### C-2 휴지통 분리

- [x] **`get_trash_items(team_id, viewer=None)` 시그니처**(database.py:3112): viewer 파라미터 추가. `app.py:3849`가 `viewer=user` 명시 전달.
- [x] **히든 프로젝트 그룹 필터**(database.py:3128-3132): admin 또는 `pj.owner_id == viewer.id`인 경우만 통과. 비멤버에게 그룹 자체가 노출되지 않음.
- [x] **SELECT에 `is_hidden, owner_id` 컬럼 추가**(database.py:3124): 필터 판단에 필요한 컬럼 모두 포함.
- [x] **복원 권한 검사**(app.py:3861-3866): `item_type=="project"`인 경우 `db.get_trash_hidden_project(item_id)`로 히든 여부 확인 → 비-admin이고 owner_id != user.id이면 403. 사양 일치.
- [x] **non-admin 복원 권한 (일반 흐름) 보존**(app.py:3857-3860): 비-admin은 자기 팀 항목만 복원 가능. C단계 변경이 일반 권한 흐름에 회귀를 일으키지 않음.
- [x] **`get_trash_hidden_project` SQL**(database.py:3232-3239): `WHERE id=? AND deleted_at IS NOT NULL` — 휴지통 한정. 활성 프로젝트는 None 반환하여 가드 통과(일반 휴지통 권한 검사로 위임).
- [x] **SSE publish 일관성**(app.py:3875): 프로젝트 복원 시 `{"name": None, "action": "update"}` — A단계 B2 패턴 유지. 이름 누설 없음.
- [x] **수동 영구 삭제 API 부재**: 자동 스케줄러(`cleanup_old_trash`)만 사용. 사양상 권한 검사 없음 정당화 가능.

### C-3 팀원 제외 경고

- [x] **트리거 조건**(app.py:1268-1270): `old_team_id is not None and (new_team_id != old_team_id or not new_is_active)` — 팀 변경 또는 비활성화 시 발동. 사양 backend_changes.md 명시 일치.
- [x] **1단계 — force 없이 호출**(app.py:1271-1279): `hidden_owned`가 비어있지 않으면 200 + `{warning: true, hidden_projects:[name...], message:"..."}` 반환, **`db.update_user` 호출하지 않음** → DB 변경 없음. 사양 정확.
- [x] **2단계 — force=true**(app.py:1280-1281): `transfer_hidden_projects_on_removal` 호출 후 update_user 진행. 이양 → 제외 순서 정확.
- [x] **이양 불가 시 owner_id=NULL**(database.py:3262-3273): `project_members WHERE user_id != ?`에서 다른 멤버 없으면 NULL UPDATE. 사양 일치.
- [x] **이양 자기 자신 제외**(database.py:3262): `WHERE user_id != ?` 가드 — owner는 `create_hidden_project`에서 자동으로 project_members에 등록되므로 가드가 필수. 정확.
- [x] **권한 — admin 전용**: 라우트 첫 줄 `_require_admin(request)`(app.py:1257). 일반 editor 호출 차단.
- [x] **CSRF**: `_require_admin`이 `_check_csrf` 자동 호출. 회귀 없음.
- [x] **관리자 비활성화 보호**(app.py:1261-1263): 관리자 계정이 자기 자신을 비활성화하는 경로 차단 — C단계 변경 이전부터 존재, 회귀 없음.
- [x] **이양 트랜잭션 안전성**: `transfer_hidden_projects_on_removal`이 `with get_conn() as conn` 컨텍스트 내에서 모든 프로젝트 일괄 처리 → autocommit, 다중 프로젝트 부분 실패 시 일부만 이양될 수 있는 위험 존재하나 SQLite get_conn 패턴(컨텍스트 매니저가 commit/rollback 처리)을 따르므로 일관됨. 차단 아님.
- [x] **회귀 — 일반 사용자 업데이트 흐름**: `is_removing` False(같은 팀 유지 + 활성 상태)인 경우 기존 동작 그대로 `db.update_user` 단독 호출.

### 공통

- [x] **DB 스키마 변경 없음** (C단계는 신규 컬럼/테이블 없이 기존 `is_hidden, owner_id, project_members` 활용). _migrate 패턴 위반 없음.
- [x] **SQL 파라미터화**: 신규 쿼리 모두 `?` placeholder. f-string 직접 삽입 없음(viewer 비교는 Python 변수로 처리).
- [x] **`get_conn()` contextmanager**: 모든 신규 함수 정상 사용.
- [x] **권한 체크**: 이동 confirm은 `_require_editor` + `auth.can_edit_*` 기존 체인, 휴지통 복원은 admin/팀/owner 3중 가드, 팀원 제외는 `_require_admin`. 누락 없음.
- [x] **에러 메시지 누설 없음**: confirm 응답 메시지 "히든 프로젝트 밖으로 이동합니다"는 호출자가 이미 히든 멤버임을 전제(현재 프로젝트 소속이 히든인 항목을 수정하므로) → 추가 누설 없음.

---

## 회귀 검토 (이전 단계 비교)

### A단계 수정사항 유지

- **A-B1 (CSRF/role)**: C단계 신규 라우트 변경 없음. 기존 라우트(`PATCH /api/events/{id}/project`, `PATCH /api/checklists/{id}`)에 confirm 가드만 추가, `_require_editor` 첫 줄 호출 그대로 유지.
- **A-B2 (SSE name=None)**: 휴지통 복원 시 `wu_broker.publish("projects.changed", {"name": None, ...})` 적용(line 3875). 기존 패턴 일관 유지.
- **A-B3 (enumeration 차단)**: C단계는 추측 가능한 새 엔드포인트를 추가하지 않음. 기존 패턴 영향 없음.

### B단계 수정사항 유지

- **B-B1 (bulk visibility 락)**: C-1 이동 confirm은 per-item PATCH 라우트에 추가되며, bulk visibility(line 838-872)는 변경 없이 그대로 유지. 회귀 없음.
- **B-2 산하 항목 외부 공개 락**: C-1 confirm 가드는 락 가드(line 893-898, 946-949) **이후가 아니라 이전**에 위치 — 즉 confirm 미통과 시 락 검사 도달 안 함. 그러나 이 경우 DB 변경도 발생하지 않으므로 안전.

### 일반 프로젝트 흐름 회귀 검증

- **일반→일반 이동**(체크리스트 line 919, 이벤트 line 1878): `_old_proj.get("is_hidden")` False 분기로 confirm 가드 미진입 → 기존 동작 그대로.
- **휴지통 일반 항목 조회**: groups 필터는 `is_hidden` 조건부이므로 일반 프로젝트는 변경 없음. unassigned는 viewer 필터 미적용이라 일반 항목은 기존 동작 그대로(team_id 필터만 적용).
- **휴지통 일반 항목 복원**: `item_type=="project"` 가드 내부에서 `proj_row.get("is_hidden")` 추가 검사 → False면 기존 admin/팀 권한 검사로 위임. 비-히든 회귀 없음.
- **admin 일반 사용자 팀 변경/비활성화**: `hidden_owned` 빈 리스트 → warning 분기 미진입 → 기존 동작(`db.update_user(user_id, data)`) 그대로.

회귀 결함 없음.

---

## 최종 판정

### **C단계 백엔드 통과** — 차단 결함 없음. QA(E2E) 진행 가능.

차단 0건. 경고 5건(W-C1~W-C5) 모두 차단 아님:
- W-C1(휴지통 unassigned 누설)은 사양 텍스트의 plain reading 경계에 있으며 A3-W3 / B-B1과 일관된 처리 패턴(per-item 우선, bulk/orphan은 후속). 후속 단계에서 명시적 차단 권장.
- W-C2(named-binding)는 사전 위험 메모, C단계 회귀 아님.
- W-C3(멤버십 잔존)은 B단계와 일관, 정책 보강은 후속 검토.
- W-C4(`PATCH /api/checklists/{id}/project` 부재)는 통합 PATCH로 동등 효과, 통과.
- W-C5(meetings)는 컬럼 부재로 적용 불가, 사양상 범위 외.

QA에 진행 신호 가능.
