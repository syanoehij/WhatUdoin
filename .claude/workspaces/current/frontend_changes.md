# #15-1 프론트엔드 변경 내역 — 없음

#15-1 (히든 프로젝트 다중 팀 전환)은 백엔드 전용 변경(database.py 헬퍼 SELECT 쿼리 전환 +
`add_hidden_project_member` 시그니처 정리 + app.py 라우트 호출부 1줄)이다.

멤버 후보 드롭다운(`/api/manage/hidden-projects/{name}/addable-members`)·assignee 후보
(`/api/hidden-project-assignees`)는 템플릿/JS가 백엔드 반환 JSON을 그대로 렌더하므로
프론트엔드 로직 변경이 필요 없다. backend-dev 작업 중 `addable-members` 관련 템플릿에서
`users.team_id`/`CURRENT_USER.team_id` 분기 같은 잔존 코드도 발견되지 않았다.

→ frontend-dev 생략. 변경 파일 없음.
