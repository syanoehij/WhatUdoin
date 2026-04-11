import requests
import json
import re
from datetime import date

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e2b"


def get_available_models() -> list[str]:
    """Ollama에서 사용 가능한 모델 목록 반환. DEFAULT_MODEL을 맨 앞에 배치. 실패 시 기본 모델만 반환."""
    try:
        response = requests.get(
            "http://localhost:11434/api/tags",
            timeout=5,
        )
        response.raise_for_status()
        models = [m["name"] for m in response.json().get("models", [])]
        if not models:
            return [DEFAULT_MODEL]
        # DEFAULT_MODEL을 맨 앞으로, 나머지는 알파벳 순
        rest = sorted(m for m in models if m != DEFAULT_MODEL)
        if DEFAULT_MODEL in models:
            return [DEFAULT_MODEL] + rest
        return rest
    except Exception:
        return [DEFAULT_MODEL]


def parse_schedule(text: str, model: str = DEFAULT_MODEL) -> list[dict]:
    today = date.today().isoformat()

    prompt = f"""아래 텍스트에서 일정을 추출해서 JSON 배열로만 답하세요. 설명 없이 JSON만 출력하세요.

오늘 날짜: {today}

출력 형식:
[{{"title":"제목","project":"프로젝트명","date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM","all_day":false,"location":"장소","assignee":"담당자","description":"설명"}}]

규칙:
- 날짜가 "이번 주", "다음 주 화요일", "4월 말" 처럼 상대적이면 오늘 날짜 기준으로 계산하세요
- "일주일 동안" 이면 오늘부터 7일
- 모르는 값은 null로 쓰세요
- 담당자는 assignee 필드에, 프로젝트명은 project 필드에 넣으세요
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

이제 아래 텍스트를 분석하세요:
{text}

JSON:"""

    response = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    response.raise_for_status()

    raw = response.json().get("response", "")
    return _extract_json(raw)


def _extract_json(raw: str) -> list[dict]:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().replace("```", "").strip()

    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
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

    return []


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
                period   = e.get("_period")
                if period == "past":
                    status = "완료"
                else:
                    m2, d2 = date_str[5:7].lstrip("0") or "0", date_str[8:10].lstrip("0") or "0"
                    status = f"예정: {m2}/{d2}"
                lines.append(f"  - {title} ({status})")
        return "\n".join(lines)

    prompt = f"""아래는 {base_date} 기준 ±1주 일정 데이터입니다.
프로젝트별로 묶어 보고서를 작성하세요. 규칙:
1. 제목 줄(# 혹은 #으로 시작하는 줄)은 절대 출력하지 마세요.
2. 각 프로젝트는 ## **프로젝트명** (~MM/DD) 형식으로 시작하세요.
3. 항목은 - 일정명 (완료) 또는 - 일정명 (예정: M/D) 형식으로 나열하세요.
4. 담당자 이름은 포함하지 마세요.
5. 설명, 서두, 결론 없이 보고서 본문만 출력하세요.

일정 데이터:
{fmt_projects()}

출력 형식 예시:
## **FE개편** (~4/18)
- 메인 페이지 리디자인 (완료)
- 로그인 버그 수정 (예정: 4/15)

## **백엔드** (~5/8)
- 알림 시스템 구축 (예정: 4/21)
- 결제 모듈 연동 (예정: 4/29)"""

    response = requests.post(
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
- 제목·날짜가 모두 같으면 중복
- 제목이 유사해도 회차(1차/2차)·단계가 다르면 중복 아님
- 기존 일정에 없으면 반드시 중복 아님
- 애매하면 is_duplicate:false
- JSON 배열만 출력, 신규 일정 개수({len(candidates)}개)만큼 항목 필수

JSON:"""

    response = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=90,
    )
    response.raise_for_status()
    raw = response.json().get("response", "")

    default = [{"is_duplicate": False, "reason": "", "existing_title": None}] * len(candidates)
    try:
        result_list = _extract_json(raw)
        out = [{"is_duplicate": False, "reason": "", "existing_title": None} for _ in candidates]
        for item in result_list:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0)) - 1  # 1-based → 0-based
            if 0 <= idx < len(candidates):
                out[idx] = {
                    "is_duplicate": bool(item.get("is_duplicate")),
                    "reason":        str(item.get("reason", "")),
                    "existing_title": item.get("existing_title") or None,
                }
        return out
    except Exception:
        return default


def to_event_payload(parsed: dict) -> dict:
    date_str   = parsed.get("date") or date.today().isoformat()
    end_date   = parsed.get("end_date") or date_str
    all_day    = parsed.get("all_day") or not parsed.get("start_time")
    start_time = parsed.get("start_time") or "00:00"
    end_time   = parsed.get("end_time") or ("00:00" if all_day else None)

    start_datetime = f"{date_str}T{start_time}"
    end_datetime   = f"{end_date}T{end_time}" if end_time else None

    return {
        "title":          parsed.get("title") or "제목 없음",
        "project":        parsed.get("project") or None,
        "description":    parsed.get("description") or "",
        "location":       parsed.get("location") or "",
        "assignee":       parsed.get("assignee") or None,
        "all_day":        1 if all_day else 0,
        "start_datetime": start_datetime,
        "end_datetime":   end_datetime,
        "created_by":     "editor",
        "source":         "ai_parsed",
    }
