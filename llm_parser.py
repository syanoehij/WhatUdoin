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
DEFAULT_MODEL = "gemma4:e2b"

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
[{{"title":"제목","project":"프로젝트명","date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM","all_day":false,"location":"장소","assignee":"담당자","description":"설명"}}]

규칙:
- 날짜가 "이번 주", "다음 주 화요일", "4월 말" 처럼 상대적이면 오늘 날짜 기준으로 계산하세요
- "일주일 동안" 이면 오늘부터 7일
- 날짜가 전혀 언급되지 않거나 "언제까지인지 모른다", "마감 미정", "가능한 빨리" 같은 표현이면 date를 null로 쓰세요 (임의로 오늘 날짜를 넣지 마세요)
- 모르는 값은 null로 쓰세요
- 담당자는 assignee 필드에, 프로젝트명은 project 필드에 넣으세요
- 회의·미팅 자체도 일정으로 추출하되, 회의 참석자 전원이 담당자인 경우 가장 대표 담당자 1명만 쓰세요
- 시간 정보가 없으면 all_day를 true로 설정하세요
- 종료 날짜가 시작 날짜와 같으면 end_date는 null로 쓰세요
- 여러 날에 걸치는 일정이면 end_date에 종료 날짜를 쓰세요
- 반드시 JSON 배열만 출력하세요

예시:
입력: 다음주 월요일 오후 3시에 회의실A에서 팀 회의, 담당 홍길동
출력: [{{"title":"팀 회의","project":null,"date":"2026-04-13","end_date":null,"start_time":"15:00","end_time":null,"all_day":false,"location":"회의실A","assignee":"홍길동","description":null}}]

입력: 이번달 말에 워크샵 예정, 담당 홍길동
출력: [{{"title":"워크샵","project":null,"date":"2026-04-30","end_date":null,"start_time":null,"end_time":null,"all_day":true,"location":null,"assignee":"홍길동","description":null}}]

입력: 4월 21일부터 23일까지 출장
출력: [{{"title":"출장","project":null,"date":"2026-04-21","end_date":"2026-04-23","start_time":null,"end_time":null,"all_day":true,"location":null,"assignee":null,"description":null}}]

입력: 김민준이 결제 모듈 테스트를 맡기로 했습니다. 언제까지라는 말은 없었고 가능한 빨리 해달라고 했습니다.
출력: [{{"title":"결제 모듈 테스트","project":null,"date":null,"end_date":null,"start_time":null,"end_time":null,"all_day":true,"location":null,"assignee":"김민준","description":"마감일 미정, 가능한 빨리 완료"}}]

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

출력 형식: JSON 배열만. 설명 없이.
필드: title, project, date(YYYY-MM-DD|null), end_date(YYYY-MM-DD|null), start_time(HH:MM|null), end_time(HH:MM|null), all_day(bool), location, assignee, description

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


def generate_weekly_report(past_events: list[dict], future_events: list[dict], base_date: str, model: str = DEFAULT_MODEL) -> str:
    # 프로젝트별로 그룹화
    from collections import defaultdict
    projects: dict[str, list[dict]] = defaultdict(list)

    for e in past_events:
        key = e.get("project") or "기타"
        projects[key].append({**e, "_period": "past"})
    for e in future_events:
        key = e.get("project") or "기타"
        projects[key].append({**e, "_period": "future"})

    def fmt_projects():
        if not projects:
            return "- (일정 없음)"
        lines = []
        for proj_name, events in sorted(projects.items()):
            end_dates = [(e.get("end_datetime") or e.get("start_datetime") or "")[:10]
                         for e in events if (e.get("end_datetime") or e.get("start_datetime"))]
            last_date = max(end_dates) if end_dates else ""
            # MM/DD 형식으로 변환
            if last_date:
                m, d = last_date[5:7].lstrip("0") or "0", last_date[8:10].lstrip("0") or "0"
                deadline = f"~{m}/{d}"
            else:
                deadline = ""
            lines.append(f"프로젝트: {proj_name} ({deadline})")
            for e in sorted(events, key=lambda x: x.get("start_datetime") or ""):
                date_str = (e.get("start_datetime") or "")[:10]
                title    = e.get("title") or "제목없음"
                desc     = (e.get("description") or "").strip()
                period   = e.get("_period")
                if period == "past":
                    status = "완료"
                else:
                    m2, d2 = date_str[5:7].lstrip("0") or "0", date_str[8:10].lstrip("0") or "0"
                    status = f"예정: {m2}/{d2}"
                line = f"  - {title} ({status})"
                if desc:
                    # 내용이 너무 길면 100자로 자름
                    short_desc = desc[:100] + ("…" if len(desc) > 100 else "")
                    line += f" — {short_desc}"
                lines.append(line)
        return "\n".join(lines)

    prompt = f"""아래는 {base_date} 기준 ±1주 일정 데이터입니다.
프로젝트별로 묶어 보고서를 작성하세요. 규칙:
1. 제목 줄(# 혹은 #으로 시작하는 줄)은 절대 출력하지 마세요.
2. 각 프로젝트는 ## **프로젝트명** (~MM/DD) 형식으로 시작하세요.
3. 항목은 - 일정명 (완료) 또는 - 일정명 (예정: M/D) 형식으로 나열하세요.
4. 일정에 설명(— 이후 내용)이 있으면, 해당 항목 바로 아래 줄에 두 칸 들여쓰기와 콜론(:)으로 시작해서 무엇을 위해 어떤 작업을 했는지 1~2문장으로 요약해서 작성하세요. 절대 em dash(—)나 하이픈(-)을 사용하지 마세요.
5. 담당자 이름은 포함하지 마세요.
6. 설명, 서두, 결론 없이 보고서 본문만 출력하세요.

일정 데이터:
{fmt_projects()}

출력 형식 예시:
## **FE개편** (~4/18)
- 메인 페이지 리디자인 (완료)
  : 사용자 경험 개선을 위해 레이아웃 전면 재구성 및 반응형 처리 완료.
- 로그인 버그 수정 (예정: 4/15)

## **백엔드** (~5/8)
- 알림 시스템 구축 (예정: 4/21)
  : 실시간 알림 전송을 위한 WebSocket 기반 서버 설계 및 구현 예정.
- 결제 모듈 연동 (예정: 4/29)"""

    response = _session.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=180,
    )
    response.raise_for_status()
    return response.json().get("response", "보고서 생성에 실패했습니다.")


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

    return {
        "title":          parsed.get("title") or "제목 없음",
        "project":        parsed.get("project") or None,
        "description":    parsed.get("description") or "",
        "location":       parsed.get("location") or "",
        "assignee":       parsed.get("assignee") or None,
        "all_day":        1 if all_day else 0,
        "start_datetime": start_datetime,
        "end_datetime":   end_datetime,
        "priority":       parsed.get("priority") or "normal",
        "kanban_status":  parsed.get("kanban_status") or None,
        "created_by":     "editor",
        "source":         "ai_parsed",
    }
