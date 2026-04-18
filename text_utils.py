import re
import unicodedata
from typing import Optional

_EMPTY_MARKERS = {"미지정", "-", "tbd", "none", "없음", "미정", "n/a", ""}


def canon_title(s: str) -> str:
    """NFKC 정규화 + 공백 squeeze + 소문자화 + 구두점 제거."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canon_assignee(s) -> Optional[str]:
    """미지정/공백/특수값 → None, 그 외는 NFKC + 공백 전체 제거."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).strip()
    if s.lower() in _EMPTY_MARKERS:
        return None
    return re.sub(r"\s+", "", s)


def canon_project(s) -> Optional[str]:
    """미지정/공백 → None, 그 외는 NFKC + 공백 전체 제거 + trim."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).strip()
    if s.lower() in _EMPTY_MARKERS:
        return None
    return re.sub(r"\s+", "", s)


def canon_location(s) -> Optional[str]:
    """공백·구두점 정리, 빈 값은 None."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).strip()
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE).strip()
    if s.lower() in _EMPTY_MARKERS:
        return None
    return s if s else None
