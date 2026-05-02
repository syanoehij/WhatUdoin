# Feature Spec: TUI Editor → Tiptap 교체

## 작업 분류
- **타입:** UI 개선 (프론트엔드 전용, 백엔드 변경 없음)
- **담당:** frontend-dev 단독

## 목표
TUI Editor(toastui-editor-all.min.js)를 Tiptap으로 교체하여 테이블 셀 드래그 선택 문제(A·B) 해결.

## 핵심 제약
1. **인트라넷 환경** — CDN 사용 불가. 모든 JS는 `static/lib/`에서 로컬 서빙
2. **Tiptap은 단일 파일 UMD 번들 미제공** → rollup으로 직접 빌드하여 `static/lib/tiptap-bundle.min.js` 생성
3. **Markdown 입출력 포맷 보존 필수** — DB 스키마 변경 없음, 기존 문서 호환
4. **eid 링크 round-trip 필수** — `[x] 제목 [🔗](eid:123)` 형태가 저장/불러오기 후 동일해야 함

## 변경 파일
| 파일 | 변경 내용 |
|------|----------|
| `static/lib/tiptap-bundle.min.js` | 신규 생성 (rollup 빌드) |
| `static/lib/tiptap-bundle.min.css` | 신규 생성 (Tiptap 스타일) |
| `static/js/wu-editor.js` | Tiptap API로 전면 재작성 (711줄) |
| `static/css/wu-editor.css` | Tiptap용 스타일 업데이트 |
| `templates/doc_editor.html` | 라이브러리 로드 태그 + 초기화 코드 교체 |
| `templates/check_editor.html` | 라이브러리 로드 태그 + 초기화 코드 교체 |

## 번들 빌드 계획
npm으로 Tiptap 패키지 설치 후 rollup으로 단일 파일 생성:

```bash
npm install @tiptap/core @tiptap/starter-kit \
  @tiptap/extension-table @tiptap/extension-table-row \
  @tiptap/extension-table-header @tiptap/extension-table-cell \
  @tiptap/extension-task-list @tiptap/extension-task-item \
  @tiptap/extension-link @tiptap/extension-image \
  tiptap-markdown

npm install --save-dev rollup @rollup/plugin-node-resolve \
  @rollup/plugin-commonjs @rollup/plugin-terser
```

## 유지해야 할 기존 기능 (wu-editor.js)
| 기능 | 현재 구현 | Tiptap 대응 |
|------|----------|-------------|
| Markdown 읽기·쓰기 | `editor.getMarkdown()` / `editor.setMarkdown()` | `tiptap-markdown` extension |
| 자동 저장 | `_scheduleAutosave` | 그대로 재사용 (editor-agnostic) |
| Edit lock + heartbeat | `_acquireLock` / `_lockHeartbeat` | 그대로 재사용 |
| Idle timeout | `_scheduleIdleTimer` | 그대로 재사용 |
| 이탈 확인 모달 | `_initLeaveConfirm` | 그대로 재사용 |
| TOC | `_buildToc` / `_tocJumpTo` | DOM 기반, 그대로 재사용 |
| Dirty 상태 | `_setDirty` | Tiptap `onUpdate` 훅 |
| Ctrl+S | keydown 이벤트 | 그대로 재사용 |
| 이미지 업로드 | `addImageBlobHook` | Tiptap Image extension `uploadFile` |
| 이미지 리사이즈 | `.toastui-editor-ww-container img` | Tiptap `.ProseMirror img` 로 변경 |
| Autocomplete | `_showAc` | 그대로 재사용 |
| Syntax highlight | `hljs.highlightElement` | 그대로 재사용 |
| Viewer 모드 | `toastui.Editor.factory({ viewer: true })` | Tiptap `editable: false` |

## WUEditor 공개 API (유지 필요)
```javascript
wuInst.editor        // 내부 Tiptap Editor 인스턴스
wuInst.dirty         // 수정 여부
wuInst.cooldown      // 저장 쿨다운 중인지
wuInst.setDirty(v)
wuInst.save()
wuInst.afterSave()
wuInst.getMarkdown()
wuInst.setContent(md)
wuInst.toggleToc()
wuInst.releaseLock()
wuInst.destroy()
wuInst.ac            // autocomplete 헬퍼
```

## eid round-trip 검증 항목
- `[x] 제목 [🔗](eid:123)` → Tiptap 로드 → getMarkdown() → 동일 형태 확인
- `[ ] 미완료 [🔗](eid:456)` 동일 검증
- 일반 링크 `[텍스트](https://...)` 보존 확인
- 테이블 파이프 형식 `| col | col |` 보존 확인

## 완료 기준
1. `static/lib/tiptap-bundle.min.js` 파일 존재
2. `wu-editor.js`가 Tiptap API만 사용 (toastui 참조 없음)
3. 기존 WUEditor 공개 API 100% 동작
4. eid round-trip 수동 확인 가능
5. 기존 doc_editor, check_editor 페이지 정상 로드
