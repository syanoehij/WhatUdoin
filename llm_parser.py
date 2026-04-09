import requests
import json
import re
from datetime import date

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e2b"


def parse_schedule(text: str, model: str = DEFAULT_MODEL) -> list[dict]:
    today = date.today().isoformat()

    prompt = f"""아래 텍스트에서 일정을 추출해서 JSON 배열로만 답하세요. 설명 없이 JSON만 출력하세요.

오늘 날짜: {today}

출력 형식:
[{{"title":"제목","project":"프로젝트명","date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM","all_day":false,"location":"장소","assignee":"담당자","description":"설명"}}]

규칙:
- 날짜가 "이번 주", "다음 주 화요일", "4월 말" 처럼 상대적이면 오늘 날짜 기준으로 계산하세요
- "일주일 동안" 이면 오늘부터 7일
- 모르는 값은 null로 쓰세요
- 담당자는 assignee 필드에, 프로젝트명은 project 필드에 넣으세요
- 시간 정보가 없으면 all_day를 true로 설정하세요
- 반드시 JSON 배열만 출력하세요

예시:
입력: 다음주 월요일 오후 3시에 회의실A에서 팀 회의, 담당 홍길동
출력: [{{"title":"팀 회의","project":null,"date":"2026-04-13","start_time":"15:00","end_time":null,"all_day":false,"location":"회의실A","assignee":"홍길동","description":null}}]

입력: 이번달 말에 워크샵 예정, 담당 홍길동
출력: [{{"title":"워크샵","project":null,"date":"2026-04-30","start_time":null,"end_time":null,"all_day":true,"location":null,"assignee":"홍길동","description":null}}]

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


def to_event_payload(parsed: dict) -> dict:
    date_str   = parsed.get("date") or date.today().isoformat()
    all_day    = parsed.get("all_day") or not parsed.get("start_time")
    start_time = parsed.get("start_time") or "00:00"
    end_time   = parsed.get("end_time")

    start_datetime = f"{date_str}T{start_time}"
    end_datetime   = f"{date_str}T{end_time}" if end_time else None

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
