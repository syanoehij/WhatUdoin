# Frontend Changes: TUI Editor → Tiptap 교체

## 변경된 파일 목록

| 파일 | 종류 | 주요 변경 내용 |
|------|------|---------------|
| `static/lib/tiptap-bundle.min.js` | 신규 (538KB) | rollup으로 빌드한 Tiptap IIFE 번들. 글로벌 `TiptapBundle` 노출 |
| `static/js/wu-editor.js` | 전면 재작성 | Tiptap 기반으로 전환, 기존 editor-agnostic 코드(자동저장·잠금·TOC 등) 보존 |
| `static/css/wu-editor.css` | 전면 재작성 | `.toastui-editor-*` → `.ProseMirror`/`.wu-toolbar` 계열 셀렉터로 교체 |
| `templates/doc_editor.html` | 수정 | `toastui-editor-all.min.js` → `tiptap-bundle.min.js` 교체, CSS 링크 정리 |
| `templates/check_editor.html` | 수정 | 라이브러리 교체 + 인라인 CSS `.ProseMirror` double-scroll 제거 |
| `tiptap-entry.js` | 신규 | rollup 진입점. 모든 Tiptap extension 명명 재수출 |
| `rollup.config.mjs` | 신규 | IIFE 빌드 설정 (`node-resolve`, `commonjs`, `terser`) |

## 번들 재빌드 명령

```bash
npx rollup --config rollup.config.mjs
```

`static/lib/tiptap-bundle.min.js`가 갱신됩니다.

## 주요 구현 사항

### 1. TiptapBundle 글로벌 구조
rollup이 `TiptapBundle = { Editor, StarterKit, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Link, Image, Markdown }` 형태의 IIFE를 생성합니다. `wu-editor.js`는 `typeof TiptapBundle === 'undefined'` 가드 후 구조분해 접근합니다.

### 2. 커스텀 툴바
Tiptap은 headless이므로 DOM 툴바를 직접 생성합니다 (`_buildToolbar`, `_bindToolbarEvents`). 헤딩 버튼은 H1 → H2 → H3 → 단락을 순환하고, `onSelectionUpdate`/`onTransaction` 콜백에서 `.is-active` 상태를 갱신합니다.

### 3. Markdown 입출력
`tiptap-markdown` extension (`Markdown.configure({ html: true, tightLists: true })`)을 사용합니다.
- 읽기: `editor.storage.markdown.getMarkdown()`
- 쓰기: `editor.commands.setContent(md)` — markdown 파싱 포함

### 4. eid: 링크 round-trip
`Link.configure({ openOnClick: false, autolink: true, validate: () => true })` 적용으로 비표준 프로토콜(`eid:123`) 링크를 허용합니다. wu-editor.css에 `pointer-events: none` 스타일이 있어 클릭 무효화도 동작합니다.

### 5. 이미지 업로드
TUI의 `addImageBlobHook` 대신 `.ProseMirror` 요소의 `paste`/`drop` 이벤트 리스너로 구현합니다. 이미지 파일을 감지하면 `feat.imageUpload.endpoint`로 FormData POST 후 `editor.chain().focus().setImage({ src: url }).run()` 삽입합니다.

### 6. 이미지 리사이즈
`tiptap-markdown`의 `html: true` 설정으로 `<img style="width:Npx">` raw HTML을 마크다운에 포함·복원합니다. `_injectImgStyles(md)`가 저장 전 주입하고, `_parseInitialWidths(md)`가 로드 시 `_imgWidthMap`을 복원합니다.

### 7. 하위 호환
- `global.WUEditor = { create, renderer: undefined }`: `home.html`의 `customHTMLRenderer: window.WUEditor?.renderer` 참조를 위한 스텁. `undefined`를 넘기면 TUI가 기본 렌더러를 사용하므로 안전합니다.
- 뷰어 모드: `editable: false`로 생성. `wu-editor-wrap`이 없으므로 `.wu-editor-wrap .ProseMirror { min-height: 200px }` 규칙이 적용되지 않습니다.

## CSS 변경 요점

| 이전 | 이후 |
|------|------|
| `.toastui-editor-defaultUI` | `.wu-editor-wrap` |
| `.toastui-editor-main` | `.wu-editor-content` |
| `.toastui-editor-ww-container` | `.ProseMirror` |
| `.toastui-editor-md-container` | (없음, WYSIWYG only) |

스크롤은 `.wu-editor-content` 하나만 담당합니다. `.ProseMirror`에는 `overflow-y` 없음.

## eid round-trip 수동 검증 방법

1. `/check/<id>/edit` 접속
2. 에디터에 `[x] 태스크 제목 [🔗](eid:123)` 입력 후 저장
3. 재접속 후 에디터 내용이 동일 형태로 복원되는지 확인
4. 에디터 내 `eid:` 링크는 `pointer-events: none`으로 클릭 불가 (정상)

## 알려진 제한 사항

- **md ↔ WYSIWYG 모드 전환 없음**: Tiptap은 단일 WYSIWYG 모드만 지원. 구 TUI의 마크다운/WYSIWYG 탭 전환은 제거됨
- **sanitizerOff 상태**: `html: true`로 raw HTML을 허용함. 내부 인트라넷 용도이므로 XSS 위험 낮음
- **테이블 셀 내 이미지 리사이즈**: 테이블 셀 안의 이미지는 클릭 이벤트에서 `e.target.closest('td, th')` 가드로 리사이즈 툴바를 표시하지 않음
