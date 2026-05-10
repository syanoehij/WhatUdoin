"""
M1a-5: WhatUdoin 부하 측정용 locust 시나리오

HTTPS 8443 고정, session cookie 사전 주입 (/api/login 호출 0),
단일 탭 / 다중 탭 분리, §15 가중치 적용.

-- 측정 전 필수 절차 --
1. 서버 종료
2. WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/scripts/snapshot_db.py
3. WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/fixtures/seed_users.py
4. 서버 시작
5. locust --host https://localhost:8443 -f locustfile.py ...
6. 서버 종료
7. WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/fixtures/cleanup.py

-- SSE 측정 --
SSE(/api/stream) 연결은 본 시나리오에서 완전 제외.
main locust와 혼합하면 p95 통계가 오염된다(long-lived streaming).
SSE 가중치 = 0% (§15 SSE 측정 분리 정책).
-> M1a-6 분리 스크립트(httpx/aiohttp 기반 keep-alive)가 담당.

-- cleanup.py 주의 --
events 삭제는 cleanup.py에서 title 기반으로 구현 완료 (M1a-7 보완).
  DELETE FROM events WHERE title LIKE 'test_perf_evt_%'
events.created_by는 app.py:1740에서 str(user.id)로 server-side 덮어쓰므로
LIKE 'test_perf_%' 패턴은 동작하지 않음 — title 접두어로 대체.
실 측정 후 cleanup 실패 시 snapshot_db.py로 복원 가능.
"""

import os
import random
import string
import time
import urllib3

import gevent  # locust가 gevent 패치 적용 — 명시 import
from locust import HttpUser, task, between, events

from _cookie_loader import assign_vu_cookie

# ── HTTPS 자체 서명 인증서 경고 억제 ─────────────────────────────────────────
# plan §15 명시: client.verify=False + InsecureRequestWarning 억제
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── 환경 변수 ─────────────────────────────────────────────────────────────────
# WU_PERF_RESTRICT_HEAVY=true 시 초기 단계(1~10 VU) 제한 적용.
# plan §15 "초기 단계에는 업로드 파일 크기와 AI 호출 비율 추가 제한" 충족.
_RESTRICT_HEAVY = os.environ.get("WU_PERF_RESTRICT_HEAVY", "").lower() in ("1", "true", "yes")

# 업로드 크기 한도: 제한 모드 1MB, 정상 모드 5MB
_UPLOAD_SIZE_BYTES = 512 * 1024 if _RESTRICT_HEAVY else 5 * 1024 * 1024  # 0.5MB / 5MB

# AI 호출 확률: 제한 모드 1%, 정상 모드 5% (§15 가중치 5%)
_AI_CALL_PROB = 0.01 if _RESTRICT_HEAVY else 0.05

# 기본 호스트 — locust --host 인자로 override 가능
_DEFAULT_HOST = "https://localhost:8443"

# Origin 헤더: CSRF 검증 운영 흐름 그대로 재현.
# app.py _check_csrf: POST/PUT/DELETE에서 Origin이 있으면 Host와 netloc 비교.
# 브라우저는 cross-origin POST에 자동으로 Origin을 주입하므로 locust도 동일하게 적용.
#
# _ORIGIN은 VU별 on_start에서 self._origin 으로 초기화된다(self.host 기반 동적 추출).
# 환경변수 WU_PERF_ORIGIN이 설정된 경우 그 값을 우선한다.
# 모듈 레벨 상수는 제거 — import 시점 self.host를 알 수 없으므로 VU 인스턴스에서 초기화.
_ENV_ORIGIN = os.environ.get("WU_PERF_ORIGIN", "").strip()


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _rand_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _perf_event_title() -> str:
    """fixture 식별용 이벤트 제목. cleanup.py가 test_perf_ 접두어로 회수."""
    return f"test_perf_evt_{_rand_suffix()}"


def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


# ── background polling 공통 루프 ──────────────────────────────────────────────

def _poll_loop(user: "HttpUser", path: str, interval: float, stop_flag: list) -> None:
    """
    setInterval 시뮬레이션 — gevent greenlet으로 실행.

    Args:
        user: HttpUser 인스턴스 (client 접근용)
        path: 요청 경로 (GET)
        interval: 폴링 주기(초)
        stop_flag: [False] 리스트. on_stop에서 True로 설정하면 루프 종료.
    """
    while not stop_flag[0]:
        try:
            user.client.get(path, verify=False, name=path)
        except Exception:
            pass  # 연결 오류는 locust 통계에 실패로 기록되므로 별도 처리 불요
        gevent.sleep(interval)


# ── SingleTabUser ─────────────────────────────────────────────────────────────

class SingleTabUser(HttpUser):
    """
    단일 탭 사용자 — 50 VU 중 75%(38명) 담당.

    §15 가중치 적용 (50 VU 최종 단계):
      - 페이지/목록 조회  40% → weight=40
      - 이벤트 CRUD      25% → weight=25
      - SSE              20% → 본 시나리오 제외 (weight=0, M1a-6 분리 스크립트 담당)
      - 파일 업로드      10% → weight=10
      - AI 일정 파싱      5% → weight=5 (환경변수로 비율 조정 가능)

    SSE는 분리 스크립트(M1a-6)가 담당. 본 시나리오 가중치 0.

    background polling (setInterval 시뮬레이션, gevent greenlet):
      - /api/notifications/count : 60s (base.html:1332, 모든 VU)
      - /api/notifications/pending: 60s 조건부 (base.html:1176) — 상한 모델로 항상 포함
    """

    host = _DEFAULT_HOST
    weight = 75  # 38/50 VU ≈ 75%

    # §4 다중 탭 정량 모델 기준 think time:
    # 단일 탭 task 간 0.5~2초 wait (사용자 행동 모방, polling은 greenlet이 담당)
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        """VU 시작: cookie 주입 + background polling 시작."""
        self._stop_flag = [False]

        # Origin 동적 추출: WU_PERF_ORIGIN 환경변수 → self.host 순으로 fallback.
        # app.py _check_csrf는 Origin과 Host의 netloc을 비교하므로 --host 값과 일치해야 함.
        self._origin = _ENV_ORIGIN or self.host
        self._unsafe_headers = {
            "Origin": self._origin,
            "Content-Type": "application/json",
        }

        # 1. session cookie 주입 (/api/login 호출 0)
        session_id, self._username = assign_vu_cookie()
        # domain 계산: self.host에서 호스트명 추출 (localhost or IP)
        # requests CookieJar는 localhost+port 도메인 매칭에서 cookie 누락이 발생한다
        # (httpx는 정상 — sse_keepalive 50/50 통과). 안전판으로 명시적 Cookie 헤더 사용.
        self.client.headers["Cookie"] = f"session_id={session_id}"

        # 2. cookie 주입 확인: _require_editor 보호 엔드포인트 1회 hit
        # /api/notifications/count는 익명 사용자에게도 HTTP 200을 반환하므로 사용 불가.
        # GET /project-manage는 _require_editor(request)를 직접 호출 → 미인증 시 403 확정.
        resp = self.client.get("/project-manage", verify=False, name="[warmup] cookie check")
        if resp.status_code == 403:
            # 세션 만료 또는 cookie 주입 실패 — 측정 오염 방지
            raise RuntimeError(
                f"[{self._username}] cookie warmup 실패: HTTP {resp.status_code}. "
                "seed_users.py 재실행 또는 세션 만료 확인 필요."
            )

        # 3. background polling greenlets 등록 (setInterval 재현)
        # /api/notifications/count: 60s (base.html:1332)
        gevent.spawn(_poll_loop, self, "/api/notifications/count", 60.0, self._stop_flag)
        # /api/notifications/pending: 60s 조건부 상한 모델 (base.html:1176)
        gevent.spawn(_poll_loop, self, "/api/notifications/pending", 60.0, self._stop_flag)

    def on_stop(self) -> None:
        self._stop_flag[0] = True

    # ── 페이지/목록 조회 (40%) ─────────────────────────────────────────────────

    @task(40)
    def view_pages(self) -> None:
        """
        주요 페이지/목록 API 조회.
        GET 요청이므로 CSRF 검사 통과 — Origin 헤더 불필요.
        """
        endpoint = random.choice([
            "/",
            "/check",
            "/project-manage",
            "/trash",
            "/api/events",
            "/api/kanban",
        ])
        self.client.get(endpoint, verify=False, name=endpoint)

    # ── 이벤트 CRUD (25%) ──────────────────────────────────────────────────────

    @task(25)
    def event_crud(self) -> None:
        """
        이벤트 생성 → 수정 → 삭제 순차 실행.
        fixture 접두어 test_perf_evt_ 한정.
        cleanup.py: events WHERE title LIKE 'test_perf_evt_%'로 회수
        (events.created_by는 app.py:1740에서 str(user.id)로 server-side 덮어쓰므로 LIKE 불가).
        """
        today = _today_iso()
        title = _perf_event_title()
        payload = {
            "title": title,
            "assignee": self._username,
            "start_datetime": f"{today}T09:00:00",
            "end_datetime": f"{today}T10:00:00",
            "event_type": "schedule",
            "source": "manual",
        }

        # CREATE
        resp = self.client.post(
            "/api/events",
            json=payload,
            headers=self._unsafe_headers,
            verify=False,
            name="/api/events [POST]",
        )
        if resp.status_code != 200:
            return  # 생성 실패 시 이후 단계 skip (locust 통계에 실패 기록됨)

        event_id = resp.json().get("id")
        if not event_id:
            return

        # UPDATE
        update_payload = {**payload, "title": f"{title}_updated", "project": "", "description": ""}
        self.client.put(
            f"/api/events/{event_id}",
            json=update_payload,
            headers=self._unsafe_headers,
            verify=False,
            name="/api/events/{id} [PUT]",
        )

        # DELETE
        self.client.delete(
            f"/api/events/{event_id}",
            headers={"Origin": self._origin},
            verify=False,
            name="/api/events/{id} [DELETE]",
        )

    # SSE: 본 시나리오 제외 — 가중치 0 (M1a-6 분리 스크립트 담당)

    # ── 파일 업로드 (10%) ──────────────────────────────────────────────────────

    @task(10)
    def upload_file(self) -> None:
        """
        이미지 업로드: /api/upload/image (app.py:3713).
        fixture 접두어 한정: filename test_perf_img_<rand>.png.
        업로드 크기: WU_PERF_RESTRICT_HEAVY=true 시 0.5MB, 기본 5MB.
        업로드는 디스크에만 저장 — DB row 없음(cleanup 불필요).
        """
        filename = f"test_perf_img_{_rand_suffix()}.png"
        # 최소 유효 PNG 헤더 + 패딩 (PIL.verify 통과용 최소 구조)
        # 실제 PIL.verify는 완전한 PNG를 요구하므로 서버에서 400이 반환될 수 있음.
        # 본 단계에서는 업로드 경로의 처리 부하(multipart parse, body read) 측정이 목적.
        fake_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * max(0, _UPLOAD_SIZE_BYTES - 8)
        self.client.post(
            "/api/upload/image",
            files={"file": (filename, fake_data, "image/png")},
            headers={"Origin": self._origin},
            verify=False,
            name="/api/upload/image [POST]",
        )

    # ── AI 일정 파싱 (5%) ──────────────────────────────────────────────────────

    @task(5)
    def ai_parse(self) -> None:
        """
        AI 일정 파싱: /api/ai/parse (app.py:3813).
        외부 Ollama 부담 → WU_PERF_RESTRICT_HEAVY 시 추가 확률 제한 적용.
        제한 모드: 1% 이하, 정상 모드: 5% 그대로.
        """
        if _RESTRICT_HEAVY and random.random() > _AI_CALL_PROB:
            return  # 제한 모드: 1% 확률 이하로만 실제 호출

        self.client.post(
            "/api/ai/parse",
            json={"text": f"test_perf_evt_{_rand_suffix()} 내일 오전 10시에 팀 미팅"},
            headers=self._unsafe_headers,
            verify=False,
            name="/api/ai/parse [POST]",
        )


# ── MultiTabUser ──────────────────────────────────────────────────────────────

class MultiTabUser(HttpUser):
    """
    다중 탭 사용자 — 50 VU 중 25%(12명) 담당.

    탭 구성: /check (viewer/editor 겸용) + /calendar 동시 오픈.

    §4 다중 탭 정량 모델 상한 (background_requests.md §4.2):
      - 38 VU(단일): 알림 count+pending × 1탭 = 76건/분
      - 12 VU(다중): 알림 count+pending × 2탭 + check lock poll × 1탭 + doc lock heartbeat × 1 = 96건/분
      - 합계 상한: 172건/분
        분해: 알림 48 + check_lock 24 + doc_lock 24 = 96 (다중 VU) + 76 (단일 VU) = 172

    background polling (모든 탭 동시 시뮬레이션):
      - /api/notifications/count : 60s × 2탭 (각 탭 독립 greenlet)
      - /api/notifications/pending: 60s × 2탭
      - /api/checklists/{id}/lock [GET]: 30s (check.html:1697, viewer poll)
        → id=1 고정 (fixture 생성 없이 존재하는 row; 없으면 404지만 polling 부하는 재현됨)
      - /api/doc/{id}/lock [PUT]: 30s (wu-editor.js:1485 doc editor heartbeat)
        → 항상-on 스폰(worst-case 모델링). 실제 운영은 편집 잠금 획득 후에만 발화하나
          부하 측정은 상한 기준이므로 항상 스폰하여 96건/분 × 12 VU = 24건/분 반영.
        → 실제 lock 미보유시 403/404 예상, 부하 측정 목적

    SSE: 본 시나리오 제외 (M1a-6 분리 스크립트 담당).
    background_requests.md §2.1: 12 VU 2탭 = 12개 SSE 추가 연결은 M1a-6이 담당.
    """

    host = _DEFAULT_HOST
    weight = 25  # 12/50 VU ≈ 25%

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self._stop_flag = [False]

        # Origin 동적 추출: WU_PERF_ORIGIN 환경변수 → self.host 순으로 fallback.
        self._origin = _ENV_ORIGIN or self.host
        self._unsafe_headers = {
            "Origin": self._origin,
            "Content-Type": "application/json",
        }

        # session cookie 주입
        session_id, self._username = assign_vu_cookie()
        # SingleTabUser와 동일 — requests CookieJar 우회를 위해 명시적 Cookie 헤더
        self.client.headers["Cookie"] = f"session_id={session_id}"

        # cookie 확인: _require_editor 보호 엔드포인트 사용 (익명 403 보장)
        resp = self.client.get("/project-manage", verify=False, name="[warmup] cookie check")
        if resp.status_code == 403:
            raise RuntimeError(
                f"[{self._username}] cookie warmup 실패: HTTP {resp.status_code}."
            )

        # ── background polling greenlets ───────────────────────────────────
        # [탭1: /check] 알림 count + pending
        gevent.spawn(_poll_loop, self, "/api/notifications/count", 60.0, self._stop_flag)
        gevent.spawn(_poll_loop, self, "/api/notifications/pending", 60.0, self._stop_flag)
        # [탭2: /calendar] 알림 count + pending (탭 독립 루프)
        gevent.spawn(_poll_loop, self, "/api/notifications/count", 60.0, self._stop_flag)
        gevent.spawn(_poll_loop, self, "/api/notifications/pending", 60.0, self._stop_flag)

        # check.html viewer poll: /api/checklists/{id}/lock 30s (check.html:1697)
        # id=1 고정 — 실존 여부 무관, polling 부하 측정 목적
        gevent.spawn(_poll_loop, self, "/api/checklists/1/lock", 30.0, self._stop_flag)

        # doc editor lock heartbeat: PUT /api/doc/{id}/lock 30s (wu-editor.js:1485)
        # greenlet 내 PUT 전송 (check viewer와 별도 탭 시뮬레이션)
        gevent.spawn(self._doc_lock_heartbeat, 30.0)

    def on_stop(self) -> None:
        self._stop_flag[0] = True

    def _doc_lock_heartbeat(self, interval: float) -> None:
        """
        PUT /api/doc/{id}/lock heartbeat (wu-editor.js:1485, 30s).
        실제 lock 미보유 시 403/404 응답 예상 — 부하/경로 측정이 목적.
        """
        while not self._stop_flag[0]:
            try:
                self.client.put(
                    "/api/doc/1/lock",
                    json={},
                    headers=self._unsafe_headers,
                    verify=False,
                    name="/api/doc/{id}/lock [PUT heartbeat]",
                )
            except Exception:
                pass
            gevent.sleep(interval)

    # ── 페이지/목록 조회 (40%) ─────────────────────────────────────────────────

    @task(40)
    def view_pages(self) -> None:
        """다중 탭 사용자의 페이지 조회 — /check + /calendar 중심."""
        endpoint = random.choice([
            "/check",
            "/calendar",
            "/api/events",
            "/api/kanban",
            "/api/checklists",
        ])
        self.client.get(endpoint, verify=False, name=endpoint)

    # ── 이벤트 CRUD (25%) ──────────────────────────────────────────────────────

    @task(25)
    def event_crud(self) -> None:
        """SingleTabUser와 동일 패턴 — 다중 탭 사용자도 이벤트 CRUD 수행."""
        today = _today_iso()
        title = _perf_event_title()
        payload = {
            "title": title,
            "assignee": self._username,
            "start_datetime": f"{today}T14:00:00",
            "end_datetime": f"{today}T15:00:00",
            "event_type": "schedule",
            "source": "manual",
        }
        resp = self.client.post(
            "/api/events",
            json=payload,
            headers=self._unsafe_headers,
            verify=False,
            name="/api/events [POST]",
        )
        if resp.status_code != 200:
            return
        event_id = resp.json().get("id")
        if not event_id:
            return

        self.client.put(
            f"/api/events/{event_id}",
            json={**payload, "title": f"{title}_updated", "project": ""},
            headers=self._unsafe_headers,
            verify=False,
            name="/api/events/{id} [PUT]",
        )
        self.client.delete(
            f"/api/events/{event_id}",
            headers={"Origin": self._origin},
            verify=False,
            name="/api/events/{id} [DELETE]",
        )

    # SSE: 본 시나리오 제외 — 가중치 0 (M1a-6 분리 스크립트 담당)

    # ── 파일 업로드 (10%) ──────────────────────────────────────────────────────

    @task(10)
    def upload_file(self) -> None:
        filename = f"test_perf_img_{_rand_suffix()}.png"
        fake_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * max(0, _UPLOAD_SIZE_BYTES - 8)
        self.client.post(
            "/api/upload/image",
            files={"file": (filename, fake_data, "image/png")},
            headers={"Origin": self._origin},
            verify=False,
            name="/api/upload/image [POST]",
        )

    # ── AI 일정 파싱 (5%) ──────────────────────────────────────────────────────

    @task(5)
    def ai_parse(self) -> None:
        if _RESTRICT_HEAVY and random.random() > _AI_CALL_PROB:
            return
        self.client.post(
            "/api/ai/parse",
            json={"text": f"test_perf_evt_{_rand_suffix()} 모레 오후 2시 회의"},
            headers=self._unsafe_headers,
            verify=False,
            name="/api/ai/parse [POST]",
        )


# ── §15 가중치 자가 점검 주석 ────────────────────────────────────────────────
#
# SingleTabUser task weight 합:
#   view_pages(40) + event_crud(25) + upload_file(10) + ai_parse(5) = 80
#   (SSE 제외, 가중치 0 — M1a-6 분리 스크립트)
#
# 50 VU 단계에서의 실효 비율 (SSE 20%를 제외한 80% 기준으로 재정규화):
#   조회: 40/80 = 50%  | 계획 40% → SSE 제외 시 비율 상승은 예상된 것
#   CRUD: 25/80 = 31%  | 계획 25%
#   업로드: 10/80 = 12%| 계획 10%
#   AI: 5/80 = 6%      | 계획 5%
#
# SSE를 본 시나리오에서 제외하므로 나머지 task가 상대적으로 더 많이 호출된다.
# M1a-6 SSE 스크립트를 병행 실행하면 전체 부하 조합이 §15 의도와 근사해진다.
#
# MultiTabUser는 SingleTabUser와 동일한 task weight를 사용한다.
# 다중 탭의 추가 부하는 background polling greenlet(6개 vs 2개)으로 발생한다.
#
# background_requests.md §4.2 상한 검증 (갱신):
#   - 단일 38 VU: count(1/min×38) + pending(1/min×38) = 76건/분
#   - 다중 12 VU: count(2/min×12) + pending(2/min×12)   = 48건/분 (알림)
#               + check_lock(2/min×12)                  = 24건/분
#               + doc_lock(2/min×12)                    = 24건/분  ← 신규 포함
#               소계                                     = 96건/분
#   - 합계: 172건/분 — §4.2 표 상한 (background_requests.md §4.2 갱신 완료)
#   (구 주석 148건/분은 doc_lock heartbeat 24건/분을 누락한 것이었음)
#
# ── 환경 변수 표 ────────────────────────────────────────────────────────────
#
# | 변수                    | 기본값  | 설명                                          |
# |------------------------|--------|-----------------------------------------------|
# | WU_PERF_RESTRICT_HEAVY | (없음) | true/1/yes: 업로드 0.5MB, AI 1% 이하           |
# | WU_PERF_ORIGIN         | (없음) | Origin 헤더 override. 미설정 시 --host 값 사용  |
# | LOCUST_HOST            | (없음) | locust --host 인자로도 설정 가능                |
# | (locust standard)      |        |                                               |
# | LOCUST_USERS           | (없음) | 총 VU 수 (권장: 50)                            |
# | LOCUST_SPAWN_RATE      | (없음) | 초당 VU 증가 수                                |
# | LOCUST_RUN_TIME        | (없음) | 측정 시간 (예: 5m, 1h)                         |
#
# 사용 예 (50 VU 최종 단계):
#   locust --host https://localhost:8443 \
#          --users 50 --spawn-rate 5 --run-time 5m \
#          -f locustfile.py --headless
#
# 초기 단계 (1~10 VU, Ollama 부담 최소화):
#   WU_PERF_RESTRICT_HEAVY=true locust --host https://localhost:8443 \
#          --users 10 --spawn-rate 1 --run-time 5m \
#          -f locustfile.py --headless
