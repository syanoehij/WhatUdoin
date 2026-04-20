import hashlib
import json
import logging
import re
import unicodedata
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger("whatudoin.llm")

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_URL = OLLAMA_BASE_URL + "/api/generate"
DEFAULT_MODEL = "gemma4:e4b"

# 회사 프록시 환경에서 localhost(Ollama) 요청이 프록시를 경유하지 않도록
# trust_env=False 로 시스템/환경변수 프록시 설정을 무시하는 전용 세션 사용
_session = requests.Session()
_session.trust_env = False


def set_ollama_base_url(base_url: str):
    """Ollama 서버 주소를 런타임에 변경한다."""
    global OLLAMA_BASE_URL, OLLAMA_URL
    OLLAMA_BASE_URL = base_url.rstrip("/")
    OLLAMA_URL = OLLAMA_BASE_URL + "/api/generate"


def get_available_models() -> list[str]:
    """Ollama에서 사용 가능한 모델 목록 반환. DEFAULT_MODEL을 맨 앞에 배치. 실패 시 기본 모델만 반환."""
    models, _ = get_available_models_with_status()
    return models


def get_available_models_with_status() -> tuple[list[str], bool]:
    """(모델 목록, 연결 성공 여부) 반환."""
    try:
        response = _session.get(
            OLLAMA_BASE_URL + "/api/tags",
            timeout=5,
        )
        response.raise_for_status()
        models = [m["name"] for m in response.json().get("models", [])]
        if not models:
            return [DEFAULT_MODEL], True
        # DEFAULT_MODEL을 맨 앞으로, 나머지는 알파벳 순
        rest = sorted(m for m in models if m != DEFAULT_MODEL)
        if DEFAULT_MODEL in models:
            return [DEFAULT_MODEL] + rest, True
        return rest, True
    except Exception:
        return [DEFAULT_MODEL], False


def parse_schedule(text: str, model: str = DEFAULT_MODEL) -> list[dict]:
    today = date.today().isoformat()

    prompt = f"""아래 텍스트에서 일정을 추출해서 JSON 배열로만 답하세요. 설명 없이 JSON만 출력하세요.

오늘 날짜: {today}

출력 형식:
[{{"title":"제목","project":"프로젝트명","date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM","all_day":false,"location":"장소","assignee":"담당자","description":"설명","event_type":"schedule"}}]

규칙:
- 날짜가 "이번 주", "다음 주 화요일", "4월 말" 처럼 상대적이면 오늘 날짜 기준으로 계산하세요
- "일주일 동안" 이면 오늘부터 7일
- 날짜가 전혀 언급되지 않거나 "언제까지인지 모른다", "마감 미정", "가능한 빨리" 같은 표현이면 date를 null로 쓰세요 (임의로 오늘 날짜를 넣지 마세요)
- 모르는 값은 null로 쓰세요
- 담당자는 assignee 필드에, 프로젝트명은 project 필드에 넣으세요
- event_type 분류: "meeting" = 회의·미팅·리뷰·스탠드업·데일리·킥오프처럼 여러 사람이 동시에 참석하는 모임(회의실·화상 등 특정 장소/링크 또는 참석자 다수). "schedule" = 1인 담당자가 기한까지 수행하는 업무·태스크·마감·제출·출장·이동·교육. 모호하면 "schedule"로 하세요
- 회의 참석자 전원이 담당자인 경우 가장 대표 담당자 1명만 쓰세요
- 시간 정보가 없으면 all_day를 true로 설정하세요
- 종료 날짜가 시작 날짜와 같으면 end_date는 null로 쓰세요
- 여러 날에 걸치는 일정이면 end_date에 종료 날짜를 쓰세요
- 반드시 JSON 배열만 출력하세요

예시:
입력: 다음주 월요일 오후 3시에 회의실A에서 팀 회의, 담당 홍길동
출력: [{{"title":"팀 회의","project":null,"date":"2026-04-13","end_date":null,"start_time":"15:00","end_time":null,"all_day":false,"location":"회의실A","assignee":"홍길동","description":null,"event_type":"meeting"}}]

입력: 이번달 말에 워크샵 예정, 담당 홍길동
출력: [{{"title":"워크샵","project":null,"date":"2026-04-30","end_date":null,"start_time":null,"end_time":null,"all_day":true,"location":null,"assignee":"홍길동","description":null,"event_type":"schedule"}}]

입력: 4월 21일부터 23일까지 출장
출력: [{{"title":"출장","project":null,"date":"2026-04-21","end_date":"2026-04-23","start_time":null,"end_time":null,"all_day":true,"location":null,"assignee":null,"description":null,"event_type":"schedule"}}]

입력: 김민준이 결제 모듈 테스트를 맡기로 했습니다. 언제까지라는 말은 없었고 가능한 빨리 해달라고 했습니다.
출력: [{{"title":"결제 모듈 테스트","project":null,"date":null,"end_date":null,"start_time":null,"end_time":null,"all_day":true,"location":null,"assignee":"김민준","description":"마감일 미정, 가능한 빨리 완료","event_type":"schedule"}}]

이제 아래 텍스트를 분석하세요:
{text}

JSON:"""

    def _call(opts: dict) -> list[dict]:
        resp = _session.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False, **opts},
            timeout=120,
        )
        resp.raise_for_status()
        return validate_and_normalize(_extract_json(resp.json().get("response", "")))

    result = _call({"options": {"temperature": 0.1}})
    if not result and len(text) >= 30:
        retry_prompt = "이전 출력이 JSON 배열이 아니었습니다. 반드시 [ 로 시작해 ] 로 끝나는 JSON 배열만 출력하세요.\n\n" + prompt
        result = _call({"options": {"temperature": 0.0}, "prompt": retry_prompt})
    return result


def refine_schedule(text: str, first_pass: list[dict], model: str = DEFAULT_MODEL) -> list[dict]:
    """2차 AI: 검토자 역할 — 1차 추출 결과를 원본 텍스트와 함께 검토해 누락·오류를 수정."""
    today = date.today().isoformat()

    first_pass_json = json.dumps(first_pass, ensure_ascii=False, indent=2)

    prompt = f"""당신은 꼼꼼한 일정 데이터 검토자입니다.
아래에 원본 회의록과, 1차 AI가 추출한 일정 JSON이 있습니다.
원본을 다시 읽고 1차 결과를 검토해서 최종 JSON 배열을 출력하세요.

오늘 날짜: {today}

검토 항목:
1. 누락된 일정이 있으면 추가하세요
2. 날짜 계산이 틀렸으면 수정하세요 (상대적 날짜 기준: 오늘 {today})
3. 담당자·장소·프로젝트가 잘못 됐거나 누락됐으면 수정하세요
4. 제목이 어색하면 자연스럽게 다듬으세요
5. 원본에 없는 내용을 추가하지 마세요
6. 날짜가 언급되지 않은 일정은 date를 null로 유지하세요
7. 1차 결과가 맞으면 그대로 유지하세요 — 멀쩡한 항목을 굳이 바꾸지 마세요
8. event_type이 적절한지 재확인하세요 — 여러 사람이 모이는 회의·미팅·리뷰는 "meeting", 개인 업무·마감·태스크·출장은 "schedule"

출력 형식: JSON 배열만. 설명 없이.
필드: title, project, date(YYYY-MM-DD|null), end_date(YYYY-MM-DD|null), start_time(HH:MM|null), end_time(HH:MM|null), all_day(bool), location, assignee, description, event_type(meeting|schedule)

--- 원본 회의록 ---
{text}

--- 1차 추출 결과 ---
{first_pass_json}

--- 최종 검토 결과 JSON ---"""

    response = _session.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=180,
    )
    response.raise_for_status()
    raw = response.json().get("response", "")
    result = validate_and_normalize(_extract_json(raw))
    # 2차가 빈 배열을 돌려주면 1차 결과 사용 (안전망)
    return result if result else first_pass


def _extract_json(raw: str) -> list[dict]:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().replace("```", "").strip()

    def _find_last_array(s: str) -> Optional[str]:
        end = s.rfind("]")
        if end == -1:
            return None
        depth = 0
        for i in range(end, -1, -1):
            if s[i] == "]":
                depth += 1
            elif s[i] == "[":
                depth -= 1
                if depth == 0:
                    return s[i:end + 1]
        return None

    candidate = _find_last_array(cleaned)
    if candidate:
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                data = json.loads(fixed)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                return [obj]
        except json.JSONDecodeError:
            pass

    sha8 = hashlib.sha256(raw.encode()).hexdigest()[:8]
    logger.warning("llm_parser: JSON extract failed len=%d sha8=%s head=%s", len(raw), sha8, raw[:200])
    return []


def validate_and_normalize(items: list) -> list[dict]:
    """추출된 일정 항목을 보수적으로 검증·정규화한다.

    형식 오류 필드는 None으로 내린다. 애매한 값을 추측 보정하지 않는다.
    """
    from text_utils import canon_assignee, canon_project, canon_location

    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def _fix_time(s) -> Optional[str]:
        if s is None:
            return None
        s = str(s).strip()
        m = re.match(r"^(\d{1,2})시(?:(\d{1,2})분)?$", s)
        if m:
            return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"
        m = re.match(r"^(\d{1,2})\.(\d{2})$", s)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        m = re.match(r"^(\d{1,2}):(\d{1,2})$", s)
        if m:
            return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
        return None

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue

        date_val = item.get("date")
        if date_val is not None and not _DATE_RE.match(str(date_val)):
            date_val = None

        end_date_val = item.get("end_date")
        if end_date_val is not None and not _DATE_RE.match(str(end_date_val)):
            end_date_val = None

        start_time = _fix_time(item.get("start_time"))
        end_time = _fix_time(item.get("end_time"))

        all_day = bool(item.get("all_day"))
        if start_time:
            all_day = False

        et_raw = str(item.get("event_type") or "").strip().lower()
        event_type = "meeting" if et_raw == "meeting" else "schedule"

        result.append({
            "title":       title,
            "project":     canon_project(item.get("project")),
            "date":        date_val,
            "end_date":    end_date_val,
            "start_time":  start_time,
            "end_time":    end_time,
            "all_day":     all_day,
            "location":    canon_location(item.get("location")),
            "assignee":    canon_assignee(item.get("assignee")),
            "description": item.get("description") or None,
            "event_type":  event_type,
        })
    return result


def time_overlap(cand: dict, ex: dict) -> Optional[bool]:
    """시간 겹침 판정.

    반환: True=겹침, False=명확히 분리, None=불확정(추정 포함 시 페널티 금지).
    """
    from datetime import datetime as _dt

    if cand.get("all_day") or ex.get("all_day"):
        return None

    cand_date = (cand.get("date") or "")[:10]
    cand_start_t = cand.get("start_time")
    cand_end_t = cand.get("end_time")

    ex_start_str = ex.get("start_datetime") or ""
    ex_end_str = ex.get("end_datetime") or ""

    if not cand_date or not cand_start_t or not ex_start_str:
        return None

    try:
        cand_start = _dt.strptime(f"{cand_date}T{cand_start_t}", "%Y-%m-%dT%H:%M")
    except ValueError:
        return None

    try:
        ex_start = _dt.fromisoformat(ex_start_str[:16])
    except ValueError:
        return None

    cand_end_estimated = cand_end_t is None
    ex_end_estimated = not ex_end_str

    if cand_end_estimated or ex_end_estimated:
        return None

    try:
        cand_end = _dt.strptime(f"{cand_date}T{cand_end_t}", "%Y-%m-%dT%H:%M")
        ex_end = _dt.fromisoformat(ex_end_str[:16])
    except ValueError:
        return None

    if cand_end <= ex_start or ex_end <= cand_start:
        return False
    return True


def score_conflict(cand: dict, ex: dict) -> dict:
    """후보와 기존 일정의 충돌 점수를 계산한다 (순수 함수).

    반환: {"total": int, "breakdown": dict, "fields_matched": list,
            "title_ratio": int, "time_overlap": Optional[bool]}
    판정 타입(exact/similar/pass) 변환은 호출부(app.py)에서 수행.
    """
    from text_utils import canon_title, canon_assignee, canon_project, canon_location
    from datetime import datetime as _dt

    try:
        from rapidfuzz import fuzz as _fuzz
        def _ratio(a, b): return _fuzz.token_set_ratio(a, b) if a and b else 0
    except ImportError:
        import difflib
        def _ratio(a, b): return int(difflib.SequenceMatcher(None, a, b).ratio() * 100) if a and b else 0

    breakdown: dict = {}
    fields_matched: list = []

    # ── title ──
    ct = canon_title(cand.get("title") or "")
    et = canon_title(ex.get("title") or "")
    ratio = _ratio(ct, et)

    if ratio >= 95:
        title_score = 40
    elif ratio >= 85:
        title_score = 30
    elif ratio >= 70:
        title_score = 15
    else:
        title_score = 0

    if len(ct) < 3 and title_score > 0:
        title_score -= 5

    breakdown["title"] = title_score
    if title_score > 0:
        fields_matched.append("제목")

    title_strong = ratio >= 85

    # ── date ──
    cand_date = (cand.get("date") or "")[:10]
    ex_date = (ex.get("start_datetime") or "")[:10]
    date_score = 0

    if cand_date and ex_date:
        try:
            diff = abs((_dt.strptime(cand_date, "%Y-%m-%d") - _dt.strptime(ex_date, "%Y-%m-%d")).days)
            if diff == 0:
                date_score = 30
                fields_matched.append("날짜")
            elif diff <= 1:
                date_score = 15
        except ValueError:
            pass
    else:
        date_score = 3 if title_strong else 0

    breakdown["date"] = date_score

    # ── assignee ──
    ca = canon_assignee(cand.get("assignee"))
    ea = canon_assignee(ex.get("assignee"))

    if ca is not None and ea is not None:
        if ca == ea:
            assignee_score = 15
            fields_matched.append("담당자")
        else:
            assignee_score = -20
    else:
        assignee_score = 3 if title_strong else 0

    breakdown["assignee"] = assignee_score

    # ── project ──
    cp = canon_project(cand.get("project"))
    ep = canon_project(ex.get("project"))
    project_score = 0
    if cp and ep and cp == ep:
        project_score = 8
        fields_matched.append("프로젝트")
    breakdown["project"] = project_score

    # ── time_overlap ──
    to = time_overlap(cand, ex)
    if to is True:
        time_score = 5
    elif to is False:
        time_score = -10
    else:
        time_score = 0
    breakdown["time_overlap"] = time_score

    # ── location ──
    cl = canon_location(cand.get("location"))
    el = canon_location(ex.get("location"))
    location_score = 0
    if cl and el and cl == el:
        location_score = 2
        fields_matched.append("장소")
    breakdown["location"] = location_score

    total = title_score + date_score + assignee_score + project_score + time_score + location_score

    return {
        "total":         total,
        "breakdown":     breakdown,
        "fields_matched": fields_matched,
        "title_ratio":   ratio,
        "time_overlap":  to,
    }


CONTEXT_BUDGET = 4000

_FEW_SHOT_WEEKLY_REPORT = """[작성 예시 — 반드시 이 형식을 따르세요]
<입력 예>
## 완료 일정 (지난 주 — 실제 완료된 항목만)
- [백엔드] API 인증 수정 (2026-04-10) [상태:완료]
  내용: JWT 만료 처리 보강. QA 검증 완료.
- [프론트] 로그인 UI 개편 (2026-04-11) [상태:완료]
  내용: 디자인 시스템 v2 반영. 반응형 레이아웃 개선.

## 지난 주 예정이었으나 아직 미완료 (지연/진행 중)
- [통신] 마스터-슬레이브 통신 (2026-04-14) [상태:미완료(지연)]
  내용: 송신부 구현 완료. 수신 후 처리 로직 미흡 확인.

## 예정 일정 (이번 주)
- [백엔드] 알림 서버 안정화 (2026-04-18) [상태:예정]

<출력 예>
## **백엔드**
- API 인증 수정 (완료)
  : JWT 만료 처리 보강
  : QA 검증 완료
- 알림 서버 안정화 (예정: 4/18)

## **프론트**
- 로그인 UI 개편 (완료)
  : 디자인 시스템 v2 반영
  : 반응형 레이아웃 개선

## **통신**
- 마스터-슬레이브 통신 (지연)
  : 마스터, 슬레이브간 통신 구현 완료
  : 수신 후 처리 로직 미흡 확인
"""


def _fmt_events_section(events, max_desc=300, default_status=None):
    if not events:
        return "- (없음)"
    lines = []
    for e in sorted(events, key=lambda x: x.get("start_datetime") or ""):
        date_str = (e.get("start_datetime") or "")[:10]
        title    = e.get("title") or "제목없음"
        project  = e.get("project") or "기타"
        desc     = (e.get("description") or "").strip()
        short    = (desc[:max_desc] + "…") if len(desc) > max_desc else desc

        is_done = e.get("is_active") == 0 or (e.get("kanban_status") or "") == "done"
        if is_done:
            status = "완료"
        elif default_status == "today":
            status = "진행 중"
        elif default_status == "future":
            status = "예정"
        elif default_status == "done":
            status = "완료"
        else:
            status = "미완료(지연)"

        line = f"- [{project}] {title} ({date_str}) [상태:{status}]"
        if short:
            line += f"\n  내용: {short}"
        lines.append(line)
    return "\n".join(lines)


def _fmt_meetings(meetings, max_items=8, body_snippet=400):
    if not meetings:
        return "- (없음)"
    lines = []
    for m in meetings[:max_items]:
        title   = m.get("title") or "제목없음"
        date_s  = m.get("meeting_date") or ""
        content = (m.get("content") or "").strip()
        snippet = (content[:body_snippet] + "…") if len(content) > body_snippet else content
        lines.append(f"- {title} ({date_s})")
        if snippet:
            lines.append(f"  {snippet}")
    return "\n".join(lines)


def _fmt_checklists(checklists, max_items=10):
    if not checklists:
        return "- (없음)"
    lines = []
    for c in checklists[:max_items]:
        title   = c.get("title") or "제목없음"
        project = c.get("project") or "기타"
        content = c.get("content") or ""
        done    = len(re.findall(r"- \[x\]", content, re.IGNORECASE))
        total   = len(re.findall(r"- \[[ x]\]", content, re.IGNORECASE))
        recent  = re.findall(r"- \[x\] (.+)", content, re.IGNORECASE)[-5:]
        summary = f"{done}/{total} 완료" if total else "항목 없음"
        lines.append(f"- [{project}] {title}: {summary}")
        if recent:
            lines.append("  최근 완료: " + ", ".join(recent))
    return "\n".join(lines)


def _truncate_report(content, max_chars=1500):
    if len(content) <= max_chars:
        return content
    head = max(max_chars - 500, 500)
    return content[:head] + "\n…(중략)…\n" + content[-500:]


def _is_bad_report(text):
    t = text.strip()
    if len(t) < 50:
        return True
    bold_sections = re.findall(r"^## \*\*(.+?)\*\*", t, re.MULTILINE)
    if len(bold_sections) < 1:
        return True
    if len(bold_sections) != len(set(bold_sections)):
        return True
    if len(re.findall(r"^- ", t, re.MULTILINE)) >= 2:
        return False
    return True


def _post_generate(model, prompt, timeout=180):
    import logging
    _sampling = [
        {"temperature": 0.2, "top_p": 0.8},
        {"temperature": 0.1, "top_p": 0.6},
        {"temperature": 0.05, "top_p": 0.4},
    ]
    for attempt in range(3):
        try:
            resp = _session.post(
                OLLAMA_URL,
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": _sampling[attempt]},
                timeout=timeout,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            if not _is_bad_report(text):
                return text
            logging.warning("generate_weekly_report: 불량 응답 (시도 %d), 재시도", attempt + 1)
        except requests.Timeout:
            logging.warning("generate_weekly_report: Timeout (시도 %d)", attempt + 1)
        except requests.RequestException as exc:
            logging.warning("generate_weekly_report: 요청 오류 (시도 %d): %s", attempt + 1, exc)
    raise RuntimeError("AI 보고서 생성 실패 (3회 재시도 후에도 유효한 응답 없음)")


def generate_weekly_report(
    past_events: list[dict],
    future_events: list[dict],
    base_date: str,
    model: str = DEFAULT_MODEL,
    *,
    today_events: list[dict] | None = None,
    past_pending: list[dict] | None = None,
    meetings: list[dict] | None = None,
    checklists: list[dict] | None = None,
    previous_report: dict | None = None,
) -> str:
    past_done    = past_events   # app.py 가 이미 완료 항목만 전달
    past_pending = past_pending or []

    meetings_text   = _fmt_meetings(meetings or [])
    checklists_text = _fmt_checklists(checklists or [])

    budget = CONTEXT_BUDGET
    m_len  = len(meetings_text)
    c_len  = len(checklists_text)
    if m_len + c_len > budget:
        checklists_text = checklists_text[:max(0, budget - m_len)] + "…(생략)"

    prev_section = ""
    if previous_report:
        remaining    = budget - len(meetings_text) - len(checklists_text)
        prev_content = _truncate_report(previous_report.get("content") or "",
                                        max_chars=min(1500, max(500, remaining)))
        prev_date    = previous_report.get("meeting_date", "")
        prev_section = (
            f"## 이전 주 보고서 ({prev_date}, 참고용 — 연속성 표현에만 활용)\n"
            f"{prev_content}\n\n"
        )

    prompt = f"""{_FEW_SHOT_WEEKLY_REPORT}
---
아래 데이터를 바탕으로 위 예시와 같은 형식의 주간 업무 보고서를 작성하세요.

{prev_section}## 완료 일정 (지난 주 — 실제 완료된 항목만)
{_fmt_events_section(past_done, default_status="done")}

## 지난 주 예정이었으나 아직 미완료 (지연/진행 중)
{_fmt_events_section(past_pending, default_status="pending")}

## 오늘 진행 중 ({base_date})
{_fmt_events_section(today_events or [], default_status="today")}

## 예정 일정 (이번 주)
{_fmt_events_section(future_events, default_status="future")}

## 이번 주 회의록 (액션 아이템 추출용 — 회의 참석 자체는 업무가 아님)
{meetings_text}

## 체크리스트 현황 (참고용 — 조직 전체, 팀 경계 없이 참고, team_id 미분류)
{checklists_text}

[반드시 지킬 것]
- 각 프로젝트는 ## **프로젝트명** 형식으로 시작 (굵게 강조 필수).
- 각 항목 옆 라벨은 입력 데이터의 [상태:...] 를 그대로 따를 것:
    상태:완료         → (완료)
    상태:미완료(지연) → (지연)
    상태:진행 중      → (진행 중)
    상태:예정         → (예정: M/D)
- 섹션 이름(지난 주/이번 주)을 보고 상태를 추정하지 말 것. 오직 각 항목의 [상태:…] 태그만 신뢰.
- [상태:미완료(지연)] 항목은 반드시 (지연)으로 표기. (완료)로 표기 금지.
- 이전 보고서가 있으면 "지난 주 예정이었던 X → 이번 주 완료" 형태로 연속성 표현.

[절대 하지 말 것]
- # 으로 시작하는 제목 줄 금지.
- "오늘 진행 중", "예정 일정" 같은 별도 섹션 생성 금지.
- 회의 참석 자체를 업무로 기재 금지 (회의록에서 결정 사항·완료 내용만 관련 프로젝트 항목 설명에 녹일 것).
- 체크리스트 항목 직접 나열 금지 (관련 프로젝트 항목 설명에 녹일 것).
- 입력에 없는 내용 추가 금지. 담당자 이름 포함 금지.
- 여러 사실을 `~했으나`, `~하였고`, `~했습니다`, `~이며` 등 접속사로 한 문장에 합치지 말 것.

[분량 가이드]
- 세부 내용은 사실 1개당 한 줄, 두 칸 들여쓰기 후 `:` 로 시작.
  사실이 2개 이상이면 반드시 줄을 나눌 것 (`  : 사실1` 다음 줄에 `  : 사실2`).
- 서두·결론 없이 ## **프로젝트명** 섹션 본문만 출력."""

    return _post_generate(model, prompt)


def review_all_conflicts(candidates: list[dict], existing: list[dict], model: str = DEFAULT_MODEL) -> list[dict]:
    """신규 일정 전체를 기존 일정과 비교해 중복 여부를 AI가 판단.

    candidates: [{title, date, assignee}]   — 새로 만들 일정 목록
    existing:   [{title, start_datetime, assignee}]  — DB 기존 일정
    반환: [{"is_duplicate": bool, "reason": str, "existing_title": str|None}]  (candidates 순서)
    """
    if not candidates:
        return []

    today = date.today().isoformat()

    if existing:
        existing_text = "\n".join(
            f'- "{e.get("title","")}" ({(e.get("start_datetime") or "")[:10]},'
            f' 담당:{e.get("assignee") or "미지정"})'
            for e in existing
        )
    else:
        existing_text = "- (없음)"

    candidates_text = "\n".join(
        f'{i+1}. "{c.get("title","")}" ({c.get("date","")}, 담당:{c.get("assignee") or "미지정"})'
        for i, c in enumerate(candidates)
    )

    prompt = f"""아래 신규 일정들이 기존 일정과 중복인지 판단하세요. JSON만 출력하세요.

오늘: {today}

[기존 일정]
{existing_text}

[신규 일정 — 번호는 1부터]
{candidates_text}

출력 형식:
[{{"index":1,"is_duplicate":false,"reason":"기존에 없는 새 일정","existing_title":null}},{{"index":2,"is_duplicate":true,"reason":"'xxx'와 동일 날짜·동일 업무","existing_title":"xxx"}}]

판단 기준:
- 제목·날짜·담당자가 모두 같으면 중복
- 제목·날짜가 같아도 담당자가 서로 다르면(둘 다 지정된 경우) 중복 아님 — 다른 사람의 별개 일정
- 담당자 중 하나가 "미지정"이면 담당자 무시하고 제목·날짜로만 판단
- 제목이 유사해도 회차(1차/2차)·단계가 다르면 중복 아님
- 기존 일정에 없으면 반드시 중복 아님
- 애매하면 is_duplicate:false
- JSON 배열만 출력, 신규 일정 개수({len(candidates)}개)만큼 항목 필수

JSON:"""

    response = _session.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=90,
    )
    response.raise_for_status()
    raw = response.json().get("response", "")

    default = [{"is_duplicate": False, "reason": "", "existing_title": None}] * len(candidates)
    try:
        result_list = _extract_json(raw)
        if not result_list:
            return default

        out = [{"is_duplicate": False, "reason": "", "existing_title": None} for _ in candidates]
        has_index = any(isinstance(item, dict) and "index" in item for item in result_list)

        if has_index:
            for item in result_list:
                if not isinstance(item, dict):
                    continue
                idx = int(item.get("index", 0)) - 1
                if 0 <= idx < len(candidates):
                    out[idx] = {
                        "is_duplicate":  bool(item.get("is_duplicate")),
                        "reason":        str(item.get("reason", "")),
                        "existing_title": item.get("existing_title") or None,
                    }
        else:
            # index 누락 시 순서 기반 fallback
            logger.info("llm_parser: ai conflict review index missing, using positional fallback")
            for i, item in enumerate(result_list):
                if i >= len(candidates) or not isinstance(item, dict):
                    break
                out[i] = {
                    "is_duplicate":  bool(item.get("is_duplicate")),
                    "reason":        str(item.get("reason", "")),
                    "existing_title": item.get("existing_title") or None,
                }
        return out
    except Exception:
        return default


def review_all_conflicts_with_funnel(
    candidates: list[dict],
    funnel_map: dict[int, list[dict]],
    model: str = DEFAULT_MODEL,
) -> list[dict]:
    """similar 후보마다 서버가 좁힌 top-K existing만 보고 AI가 중복 여부 판단.

    funnel_map: {candidate_index: [top-K existing dicts]}
    """
    if not candidates:
        return []

    today = date.today().isoformat()
    default = [{"is_duplicate": False, "reason": "", "existing_title": None}] * len(candidates)

    def _fmt_existing(items: list[dict]) -> str:
        if not items:
            return "- (없음)"
        return "\n".join(
            f'- "{e.get("title","")}" ({e.get("date","")}, '
            f'시작:{e.get("start_time") or "미지정"}, 담당:{e.get("assignee") or "미지정"})'
            for e in items
        )

    candidates_text = "\n".join(
        f'{i+1}. "{c.get("title","")}" ({c.get("date","")}, '
        f'시작:{c.get("start_time") or "미지정"}, 끝:{c.get("end_time") or "미지정"}, '
        f'담당:{c.get("assignee") or "미지정"}, 프로젝트:{c.get("project") or "미지정"})'
        for i, c in enumerate(candidates)
    )

    existing_sections = "\n".join(
        f"[신규 {i+1}번 관련 기존 일정]\n{_fmt_existing(funnel_map.get(i, []))}"
        for i in range(len(candidates))
    )

    prompt = f"""아래 신규 일정들이 관련 기존 일정과 중복인지 판단하세요. JSON만 출력하세요.

오늘: {today}

[신규 일정]
{candidates_text}

{existing_sections}

출력 형식:
[{{"index":1,"is_duplicate":false,"reason":"기존에 없는 새 일정","existing_title":null}}]

판단 기준:
- 제목·날짜·담당자가 모두 같으면 중복
- 시간대가 다르면(명시된 경우) 중복 아님
- 담당자가 서로 다르면(둘 다 지정된 경우) 중복 아님
- 회차(1차/2차)·단계가 다르면 중복 아님
- 애매하면 is_duplicate:false
- JSON 배열만 출력, 신규 일정 개수({len(candidates)}개)만큼 항목 필수

JSON:"""

    try:
        response = _session.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.0}},
            timeout=90,
        )
        response.raise_for_status()
        raw = response.json().get("response", "")
    except Exception:
        return default

    try:
        result_list = _extract_json(raw)
        if not result_list:
            return default

        out = [{"is_duplicate": False, "reason": "", "existing_title": None} for _ in candidates]
        has_index = any(isinstance(item, dict) and "index" in item for item in result_list)

        if has_index:
            for item in result_list:
                if not isinstance(item, dict):
                    continue
                idx = int(item.get("index", 0)) - 1
                if 0 <= idx < len(candidates):
                    out[idx] = {
                        "is_duplicate":  bool(item.get("is_duplicate")),
                        "reason":        str(item.get("reason", "")),
                        "existing_title": item.get("existing_title") or None,
                    }
        else:
            logger.info("llm_parser: funnel conflict review index missing, using positional fallback")
            for i, item in enumerate(result_list):
                if i >= len(candidates) or not isinstance(item, dict):
                    break
                out[i] = {
                    "is_duplicate":  bool(item.get("is_duplicate")),
                    "reason":        str(item.get("reason", "")),
                    "existing_title": item.get("existing_title") or None,
                }
        return out
    except Exception:
        return default


def to_event_payload(parsed: dict) -> dict:
    date_str   = parsed.get("date") or None          # null → 날짜 미입력, 강제 오늘 대입 없음
    end_date   = parsed.get("end_date") or date_str
    all_day    = parsed.get("all_day") or not parsed.get("start_time")
    start_time = parsed.get("start_time") or "00:00"
    end_time   = parsed.get("end_time") or ("00:00" if all_day else None)

    start_datetime = f"{date_str}T{start_time}" if date_str else None
    end_datetime   = f"{end_date}T{end_time}" if (end_date and end_time) else None

    event_type = parsed.get("event_type") or "schedule"
    kanban_status = parsed.get("kanban_status") or ("backlog" if event_type == "schedule" else None)

    return {
        "title":            parsed.get("title") or None,
        "project":          parsed.get("project") or None,
        "description":      parsed.get("description") or "",
        "location":         parsed.get("location") or "",
        "assignee":         parsed.get("assignee") or None,
        "all_day":          1 if all_day else 0,
        "start_datetime":   start_datetime,
        "end_datetime":     end_datetime,
        "priority":         parsed.get("priority") or "normal",
        "kanban_status":    kanban_status,
        "event_type":       event_type,
        "recurrence_rule":  parsed.get("recurrence_rule") or None,
        "recurrence_end":   parsed.get("recurrence_end") or None,
        "created_by":       "editor",
        "source":           "ai_parsed",
    }


def generate_checklist(text: str, model: str = DEFAULT_MODEL) -> str:
    """자연어 요청 → 마크다운 체크리스트 생성. 첫 줄은 반드시 # 제목."""
    prompt = f"""아래 요청에 대해 실행 가능한 마크다운 체크리스트를 작성하라.
형식 규칙(반드시 준수):
- 첫 줄은 `# 제목` 한 줄 (H1, 요청 내용을 압축한 짧은 제목)
- 빈 줄 1개
- 이후 각 항목은 `- [ ] 내용` 형태 (대괄호 안은 반드시 빈칸, 체크된 상태로 쓰지 마라)
- 필요시 소제목은 `## 섹션명` 으로만 추가 (H3 이상 금지)
- 코드펜스(```) 금지. 부연 설명 금지. 마크다운 본문만 출력.

요청:
{text}

출력:"""

    _sampling = [
        {"temperature": 0.3, "top_p": 0.9},
        {"temperature": 0.15, "top_p": 0.7},
        {"temperature": 0.05, "top_p": 0.5},
    ]
    for attempt in range(3):
        try:
            resp = _session.post(
                OLLAMA_URL,
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": _sampling[attempt]},
                timeout=120,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            # 코드펜스 strip
            raw = re.sub(r"^```(?:markdown)?\n?", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\n?```\s*$", "", raw)
            raw = raw.strip()
            # 첫 줄이 # 제목이 아니면 prefix 추가
            if raw and not re.match(r"^\s*#\s", raw):
                raw = "# 할 일\n\n" + raw
            if raw:
                return raw
            logger.warning("generate_checklist: 빈 응답 (시도 %d), 재시도", attempt + 1)
        except requests.Timeout:
            logger.warning("generate_checklist: Timeout (시도 %d)", attempt + 1)
        except requests.RequestException as exc:
            logger.warning("generate_checklist: 요청 오류 (시도 %d): %s", attempt + 1, exc)
    raise RuntimeError("AI 체크리스트 생성 실패 (3회 재시도 후에도 유효한 응답 없음)")


def generate_event_checklist_items(events: list[dict], model: str = DEFAULT_MODEL) -> list[dict]:
    """이벤트 목록 → 체크리스트 항목 생성. 내용 있으면 AI가 세부 항목 분류."""
    results = []
    for event in events:
        title = (event.get("title") or "").strip()
        description = (event.get("description") or "").strip()

        if not description:
            results.append({"event_id": event["id"], "title": title, "sub_items": []})
            continue

        prompt = f"""다음 일정의 내용을 읽고 실행 가능한 세부 할 일 목록을 추출하라.
일정: {title}
내용:
{description}

규칙:
- 한 줄에 하나씩, 순수 텍스트만 출력 (기호/번호 없이)
- 최대 5개
- 구체적 행동 중심으로 표현
- 분류 불가하거나 내용이 단순하면 빈 줄만 출력

출력:"""

        sub_items = []
        try:
            resp = _session.post(
                OLLAMA_URL,
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.2, "top_p": 0.8}},
                timeout=60,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            lines = [
                l.strip().lstrip("-•*").lstrip("0123456789.").strip()
                for l in raw.split("\n") if l.strip()
            ]
            sub_items = [l for l in lines if l][:5]
        except Exception as exc:
            logger.warning("generate_event_checklist_items event=%s: %s", event["id"], exc)

        results.append({"event_id": event["id"], "title": title, "sub_items": sub_items})

    return results
