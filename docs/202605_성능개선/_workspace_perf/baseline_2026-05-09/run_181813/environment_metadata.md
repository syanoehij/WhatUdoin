# 환경 메타데이터 — M1a-7 baseline

측정 시작: 2026-05-09 18:18:14
run 디렉터리: D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\run_181813

## 시스템

| 항목 | 값 |
|------|-----|
| OS | Windows 11 10.0.26200 |
| CPU | AMD64 Family 26 Model 36 Stepping 0, AuthenticAMD (24 logical) |
| RAM | 23.6 GB |
| Python | 3.12.9 |
| locust | locust 2.43.4 from D:\Program Files\Python\Python312\Lib\site-packages\locust (Python 3.12.9) |
| httpx | 0.28.1 |

## DB 상태 (측정 시작 전)

| 항목 | 값 |
|------|-----|
| whatudoin.db 크기 | 836.0 KB |
| users rows | 3 |
| events rows | 524 |
| checklists rows | 65 |
| notifications rows | 87 |

## meetings/ 사용량

| 항목 | 값 |
|------|-----|
| 파일 수 | 83 |
| 사용량 | 1000.1 KB |

## 측정 환경 정책

| 항목 | 상태 |
|------|------|
| server-locust 동거 | 동일 호스트 (localhost). 서버 CPU/메모리 경합 포함됨 |
| locust host | https://localhost:8443 (자체 서명 TLS) |
| SSE 분리 측정 | Phase 6에서 50 VU와 병렬 (sse_keepalive.py) |
| sanity run | 1 VU × 30s, WU_PERF_RESTRICT_HEAVY=true |
| 본 측정 단계 | 1/5/10 VU (RESTRICT_HEAVY=true) → 25/50 VU (해제) |

| snapshot SHA256 | 0884382deceb34918daa14fafadad9b337c02ebb63e606d0f58ae5f53d1cec73 |
