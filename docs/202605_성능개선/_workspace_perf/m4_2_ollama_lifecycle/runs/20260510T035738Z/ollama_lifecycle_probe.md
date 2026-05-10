# M4-2 Ollama Lifecycle Probe — 20260510T035738Z

모드: LIVE

## 결과 요약

- PASS: 19
- FAIL: 0
- 총계: 19

## 세부 결과

```
  [PASS] supervisor 생성 성공
  [PASS] internal_token 파일 존재
  [PASS] internal_token 비어 있지 않음
  [PASS] spec 생성
  [PASS] start_service 반환
  [PASS] status = running or starting
  [PASS] 포트 open 대기 성공
  [PASS] probe_healthz ok=True or status=ok
  [PASS] IPC 응답 ok 키 존재
  [PASS] IPC 응답 reason 또는 result 키 존재
  [PASS] IPC ok=False → reason 키 존재
  [PASS] stop_service 후 status=stopped
  [PASS] 포트 닫힘 확인
  [PASS] kill 후 OllamaUnavailableError(reason=connect)
  [PASS] kill 후 메시지 = AI 사용 불가
  [PASS] 재시작 status = running or starting
  [PASS] 재시작 포트 open
  [PASS] 재시작 probe_healthz PASS
  [PASS] 재시작 IPC ok 키 존재
```

## 메모

- 5종 분기 회귀: A 섹션(IPC 모드) 완료
- spec 항목2 'urllib.error.URLError' 코드-스펙 불일치 확인:
  `_call_ollama_service`는 `requests.ConnectionError`로 처리. 구현 자체는 올바름.
- in-process fallback 분기: B 섹션 완료
- 강제 종료 → OllamaUnavailableError(reason="connect"): C 섹션
- 재시작 → IPC 정상 응답 시그니처: C 섹션
