# M1a-7 baseline 측정 요약

생성 일시: 2026-05-09 18:26:23
run 디렉터리: D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\run_181951

## 가드 검증 결과

| 항목 | 결과 |
|------|------|
| snapshot SHA256 | | snapshot SHA256 | 2825413f53a485f15e0d11ec95da1643bb9d89b834accd50b07b0639d99b2d3f | |
| seed users/sessions | 50 / 50 |
| cleanup 검증 | 통과 (모두 0) |
| sanity 통과 | p95=2100ms / 실패율=21.7% |

## 단계별 지표

| 단계 | p95 (ms) | p99 (ms) | 실패율 | RPS |
|------|---------|---------|--------|-----|
| vu1 | 2100 | 2100 | 28.8% | 0.8993841136669055 |
| vu5 | 2300 | 2300 | 26.9% | 4.2063601715442065 |
| vu10 | 2400 | 2500 | 27.4% | 8.835917522310062 |
| vu25 | 3200 | 7400 | 27.3% | 11.096219766191126 |
| vu50 | 5300 | 12000 | 27.4% | 17.424133812096446 |

## SSE 지표 3종 (Phase 6)

연결 성공 50/50 | 조기 끊김 0 | inter-arrival p95 328 ms

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
