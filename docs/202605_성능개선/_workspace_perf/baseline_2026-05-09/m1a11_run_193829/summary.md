# M1a-11 baseline 측정 요약

생성 일시: 2026-05-09 19:40:00
run 디렉터리: D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\m1a11_run_193829
M1a-7 baseline: D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09/run_181951/ (비교 기준)

## 가드 검증 결과

| 항목 | 결과 |
|------|------|
| snapshot SHA256 | | snapshot SHA256 | d7fda5950daadf0d0372235ac13e0cdf1fb1788ec9577249922250dca0fd6cbe | |
| seed users/sessions/cookies | 50 / 50 / 50 |
| cleanup 검증 | 통과 (모두 0) |

## §5-1 4단계 측정 표 (M1a-11)

| 페이지 | Stage1 다운로드(ms) | Stage2 WuAssets ready | Stage3 create(ms) | Stage4 ProseMirror(ms) |
|--------|--------------------|-----------------------|-------------------|------------------------|
| check-detail-first | 6 | ready | 101 | 2856 |
| project-manage | 5 | ready | 247 | 5019 |
| trash | 6 | ready | 467 | 5460 |

> Stage1: 자산 다운로드 시간(ms) / Stage2: WuAssets.isReady() / Stage3: WUEditor.create() 소요(ms) / Stage4: ProseMirror 등장(ms from nav start)

## 자산 다운로드 표 (페이지별 다운로드 횟수)

| 페이지 | wu-editor | mermaid | tiptap | highlight |
|--------|-----------|---------|--------|----------|
| check-list (list) | 0 | 0 | 0 | 0 |
| check-detail-first () | 1 | 1 | 1 | 1 |
| check-detail-second-delta () | 0 | 0 | 0 | 0 |
| check-detail-second-total () | 1 | 1 | 1 | 1 |
| project-manage (viewer-activated) | 1 | 1 | 1 | 1 |
| trash (item-activated) | 1 | 1 | 1 | 1 |

> 목록 모드 0건 / 상세 진입 1건 (두 번째 진입 delta=0) 검증

## viewer 회귀 결과 (M1a-11)

| 항목 | 결과 |
|------|------|
| prosemirror-visible | OK |
| mermaid | SKIP(no-mermaid-data) |
| katex | SKIP(no-katex-data) |
| highlight | SKIP(no-code-data) |
| image | SKIP(no-image-data) |

## M1a-11 Playwright spec 결과

| 항목 | 값 |
|------|-----|
| pass | 10 |
| fail | 1 |
| skip | 0 |
| 종합 | FAIL |

## M1a-12 메인 Playwright 회귀

회귀 대상 (11개 spec — lazy-load 및 viewer 관련 phase 한정):
- tests/phase33_doc_linebreak.spec.js
- tests/phase33_toc_resizer.spec.js
- tests/phase33_dark_theme_codeblock.spec.js
- tests/phase33_pinpoint_all.spec.js
- tests/phase34_tiptap_migration.spec.js
- tests/phase37_asset_cache.spec.js
- tests/phase37_stage2_static_cache.spec.js
- tests/phase37_stage3_static_cleanup.spec.js
- tests/phase38_doc_image_resize.spec.js
- tests/phase52_check_load_perf.spec.js
- tests/phase53_paste_table_check_hit.spec.js

| 항목 | 값 |
|------|-----|
| pass | 17 |
| fail | 26 |
| skip | 7 |

실패 목록:
  - [failed] phase33_dark_theme_codeblock.spec.js :: 1. 코드블록 pre 배경이 다크에서 #161b22 (vendor #f4f7f8 누수 없음)
  - [failed] phase33_doc_linebreak.spec.js :: 1. saveDoc() — 연속 빈줄 N개 → <br> 블록 N개로 직렬화
  - [failed] phase33_doc_linebreak.spec.js :: 2. 라운드트립 — 저장 → 새로고침 → 재저장 시 <br> 개수 누적·축소 없음
  - [failed] phase33_doc_linebreak.spec.js :: 3. <br> 태그 직접 입력 → 저장 + 뷰어 렌더
  - [failed] phase33_doc_linebreak.spec.js :: 4. 휴지통 뷰어 — <br> 포함 문서 sanitizer 통과
  - [failed] phase33_doc_linebreak.spec.js :: 5. 빈 단락 가시성 — .markdown-body p / .toastui-editor-contents p min-height
  - [failed] phase33_pinpoint_all.spec.js :: 비로그인 GET "/" → 303, Location: /kanban
  - [failed] phase33_toc_resizer.spec.js :: 1. 목차 패널 기본 폭 320px 이상 (CSS 기본값)
  - [failed] phase34_tiptap_migration.spec.js :: 1) /doc/{personal_id} — #editor-container 에 .ProseMirror 렌더, toastui-editor-* DOM 부재
  - [failed] phase34_tiptap_migration.spec.js :: 2) eid: 링크가 든 doc 저장 후 재로드 시 .ProseMirror 안에 a[href^="eid:"] 렌더
  - [failed] phase34_tiptap_migration.spec.js :: 3) /check/{id}/edit — Tiptap(.ProseMirror) 렌더, toastui DOM 부재
  - [failed] phase34_tiptap_migration.spec.js :: 4) /check, /notice, / 에서 toastui.Editor 라이브러리 여전히 로드 (회귀 방지)
  - [failed] phase34_tiptap_migration.spec.js :: 5) doc 편집 후 60초 내 자동저장 PUT 요청 발생
  - [failed] phase37_asset_cache.spec.js :: 1) /calendar, /kanban — tiptap-bundle.min.js / wu-editor.js 네트워크 요청 부재
  - [failed] phase37_asset_cache.spec.js :: 2) /check — tiptap-bundle.min.js 로드 + window.WUEditor 실행 가능
  - [failed] phase37_asset_cache.spec.js :: 3) style.css ?v= 값이 두 번 방문에서 동일 + hex 형식
  - [failed] phase37_asset_cache.spec.js :: 4) /kanban 에서 체크 바인딩 이벤트 상세 → ensureWUEditorAssets() 동적 로드 + .ProseMirror 렌더
  - [failed] phase37_stage2_static_cache.spec.js :: 1) /static/ 응답에 Cache-Control — ?v= 있으면 immutable, 없으면 max-age=3600
  - [failed] phase37_stage2_static_cache.spec.js :: 2) /, /check, /kanban — fullcalendar.min.js 네트워크 요청 부재
  - [failed] phase37_stage2_static_cache.spec.js :: 3) /calendar — fullcalendar.min.js + asset_v 쿼리 + FullCalendar.Calendar 초기화 정상
  - [failed] phase37_stage2_static_cache.spec.js :: 4) /check — 6개 자산 모두 ?v= 쿼리 포함 로드 + WUEditor 정상 동작
  - [failed] phase37_stage3_static_cleanup.spec.js :: T1) toastui 잔여 자산 직접 GET → 404, 페이지 로드 시 요청도 없음
  - [failed] phase37_stage3_static_cleanup.spec.js :: T2) /, /calendar, /check 정상 로드 + 핵심 런타임 객체 존재
  - [failed] phase37_stage3_static_cleanup.spec.js :: T3) 서버 부팅 정상 — health 라우트 또는 home 응답 < 400
  - [failed] phase38_doc_image_resize.spec.js :: viewer applies Obsidian image widths and keeps table cell images rendered
  - [failed] phase52_check_load_perf.spec.js :: 4) 검색 — title 토큰 입력 시 목록이 필터링됨

> 전체 회귀 제외 사유: 변경 영향 범위(base.html, check.html, home.html,
> project_manage.html, trash.html, event-modal.js, notice_history.html)에
> 해당하는 viewer/doc/asset-cache phase spec 11개로 한정.

## M1a 종료 게이트 평가

| 게이트 | 상태 |
|--------|------|
| 단계별 baseline (M1a-7 대비) | N/A (M1a-7 수치와 수동 비교 필요) |
| SSE 분리 측정 | N/A (M1a-7에서 측정됨 — 본 run 제외) |
| §5-1 4단계 측정 완료 | PASS |
| viewer 회귀 0건 | FAIL |

## 연결 파일

- 환경 메타데이터: [environment_metadata.md](environment_metadata.md)
- 서버 로그: [server_stderr.log](server_stderr.log)
- M1a-11 Playwright JSON: [m1a11_playwright.json](m1a11_playwright.json)
- M1a-12 Playwright JSON: [m1a12_playwright.json](m1a12_playwright.json)
- M1a-11 spec 결과 복사본: [m1a11_results_copy/](m1a11_results_copy/)
- Playwright HTML report: playwright-report/ (repo root)
