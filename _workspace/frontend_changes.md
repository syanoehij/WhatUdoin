# 프론트엔드 변경 이력

## 2026-04-30 — 간트 차트 버그 2건 수정

### 수정 파일
- `templates/project.html`

---

### 버그 1: 칸반 미연결 하위 업무 overdue 표시 제거

**변경 위치**: `templates/project.html` 약 1012~1031줄 (`renderEventRow` 내 bar 생성 로직)

**변경 내용**:
- `isUnlinked = !ev.kanban_status` 변수 추가: `kanban_status`가 `null`이면 칸반 미연결로 판단
- `evDone` 계산에 `!isUnlinked` 조건 추가 — 칸반 미연결 일정은 완료 스타일 적용 안 함
- `evOverdue` IIFE에 `isUnlinked` 단락 조건 추가 — 칸반 미연결이면 overdue 판정 건너뜀
- 결과: 하위 업무(`parent_event_id` 있음)처럼 `kanban_status: null`인 일정은 기한 초과 빗살·아이콘 없이 일반 바로 표시

**근거**: `database.py:432` — 하위 이벤트 생성 시 `kanban_status: None` 하드코딩. `llm_parser.py:887` — 최상위 schedule은 항상 `"backlog"` 이상의 값으로 초기화.

---

### 버그 2: 주말(토/일) 간트 열 폭 축소 (평일의 55%)

**변경 위치**: `templates/project.html` — 헬퍼 함수, `render()`, 관련 이벤트 핸들러

#### 추가된 헬퍼 (약 525~565줄)

| 이름 | 역할 |
|------|------|
| `WEEKEND_RATIO = 0.55` | 주말 열 폭 비율 상수 |
| `getDayPx()` | 평일 기준 1일 폭 반환. 가중 합계(`weekday*1 + weekend*0.55`)로 컨테이너 폭 배분 |
| `dayWidthAt(i)` | i번째 날의 실제 픽셀 폭 (주말이면 `dayPx * 0.55`) |
| `_buildDayLeftCache(dayPx)` | render() 진입 시 누적 left 오프셋 배열 초기화 |
| `dayLeft(i)` | i번째 날의 누적 left 픽셀 오프셋 (prefix sum 조회) |
| `calcTodayLineLeft(todayOff)` | `dayPx` 파라미터 제거 — `dayLeft()` + `dayWidthAt()` 사용으로 변경 |

#### render() 진입부 (약 715~719줄)
- `_buildDayLeftCache(dayPx)` 호출 추가 (주말 폭 캐시 초기화)
- `totalW = dayLeft(daysShown)` — 균등 폭 대신 누적 폭 사용

#### 월 레이블 루프 (약 739~756줄)
- `mStart`, `mStartIdx` 분리 추적 — `dayLeft(i)` 픽셀과 인덱스를 별도 변수로 관리
- `addMonthLabel` 호출 시 `mStartIdx`를 day index로 전달 (이전 `Math.round(mStart/dayPx)` 오차 제거)

#### 주차 레이블 루프 (약 761~778줄)
- `wStart`, `wStartIdx` 분리 추적 — 월 레이블과 동일한 방식
- `addWeekLabel` 호출 시 정확한 day index 전달

#### 일(day) 레이블 루프 — 14~60일 뷰 (약 781~800줄)
- `el.style.cssText` 에서 `left:${i * dayPx}px;width:${dayPx}px` → `left:${dayLeft(i)}px;width:${cellW}px`
- `isMon && dayPx >= 28` 조건을 `isMon && cellW >= 28`로 수정

#### 일(day) 레이블 루프 — 7~13일 뷰 (약 802~812줄)
- 동일하게 `dayLeft(i)`, `dayWidthAt(i)` 사용

#### 프로젝트 스팬 바 (약 890줄)
- `left:${s * dayPx}px;width:${(e-s)*dayPx}px` → `left:${dayLeft(s)}px;width:${dayLeft(e)-dayLeft(s)}px`

#### 이벤트 바 위치 계산 (약 1008~1009줄)
- `barW = (clampedE - clampedS) * dayPx - 3` → `dayLeft(clampedE) - dayLeft(clampedS) - 3`
- `barL = clampedS * dayPx + 1` → `dayLeft(clampedS) + 1`

#### 오늘 선 (약 1073줄, 1084줄)
- `calcTodayLineLeft(todayOff, dayPx)` → `calcTodayLineLeft(todayOff)` (dayPx 파라미터 제거)

#### 휠 스크롤 (약 1102~1110줄)
- `dayPx` 대신 `avgPx = tl.clientWidth / daysShown` (평균 폭) 사용 — 휠은 거친 조작이므로 평균으로 충분

#### 드래그 이동 (약 1213~1216줄)
- `getDayPx()` 대신 `avgPx2 = tl.clientWidth / daysShown` 사용 — 드래그 픽셀→일수 변환에 평균 폭 사용

#### scrollToToday (약 1293줄)
- `daysBetween(viewStart, today()) * getDayPx()` → `dayLeft(daysBetween(viewStart, today()))` 사용

---

### UI 동작 설명

- **버그 1**: 하위 업무(칸반 카드 없음)는 기간이 지나도 빨간 빗살·⚠ 아이콘 없이 일반 색상 바로 표시됨
- **버그 2**: 주말 열이 평일 대비 55% 폭으로 표시되어 달력처럼 주말이 시각적으로 좁게 렌더링됨. 헤더·바·오늘선이 모두 동일한 누적 오프셋을 사용하므로 정렬이 맞음
