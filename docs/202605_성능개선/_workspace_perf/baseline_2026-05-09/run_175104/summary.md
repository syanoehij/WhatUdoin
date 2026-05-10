# M1a-7 baseline 측정 요약

생성 일시: 2026-05-09 17:57:33
run 디렉터리: D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\run_175104

## 가드 검증 결과

| 항목 | 결과 |
|------|------|
| snapshot SHA256 | | snapshot SHA256 | ae0266a090331370e0e8aa5436ca13e84915aabe71f7eb70c8386760aeff1693 | |
| seed users/sessions | 50 / 50 |
| cleanup 검증 | 통과 (모두 0) |
| sanity 통과 | p95=N/Ams / 실패율=N/A |

## 단계별 지표

| 단계 | p95 (ms) | p99 (ms) | 실패율 | RPS |
|------|---------|---------|--------|-----|
| vu1 | N/A | N/A | N/A | 0.0 |
| vu5 | N/A | N/A | N/A | 0.0 |
| vu10 | N/A | N/A | N/A | 0.0 |
| vu25 | N/A | N/A | N/A | 0.0 |
| vu50 | N/A | N/A | N/A | 0.0 |

## SSE 지표 3종 (Phase 6)

연결 성공 50/50 | 조기 끊김 0 | inter-arrival p95 27172 ms

> [한계] broker.py server-side timestamp 없음. inter-arrival 값의 대부분은 ~25s(ping 주기).
> 실제 이벤트 latency는 M1c-10 QueueFull 카운터 도입 후 정확 측정 가능.

## 연결 파일

- 환경 메타데이터: [environment_metadata.md](environment_metadata.md)
- 서버 로그: [server_stderr.log](server_stderr.log)
- sanity CSV: sanity_locust_stats.csv

## 비고

- upload_file 실패율: PIL.verify 한계로 높을 수 있음 (예상된 실패)
- ai_parse 실패율: Ollama 응답 시간에 따라 가변
- server-locust 동거 환경 → p95에 측정 서버 자체 부하 포함
