# 환경 메타데이터 — M1a-7 baseline

측정 시작: 2026-05-09 17:38:40
run 디렉터리: D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\run_173837

## 시스템

| 항목 | 값 |
|------|-----|
| OS | Microsoft Windows 11 Home 10.0.26200 |
| CPU | AMD Ryzen AI 9 HX 370 w/ Radeon 890M            |
| RAM | 23.6 GB |
| Python | Python 3.12.9 |
| Node | v24.14.1 |
| locust | 2.43.4 |
| httpx | 0.28.1 |

## DB 상태 (측정 시작 전)

| 항목 | 값 |
|------|-----|
| whatudoin.db 크기 | 836 KB |
| users=3 |
| events=524 |
| checklists=65 |
| notifications=87 |


## 첨부 디렉터리 (meetings/)

| 항목 | 값 |
|------|-----|
| 파일 수 | 83 |
| 사용량 | 1000.1 KB |

## 측정 환경 정책

| 항목 | 상태 |
|------|------|
| server-locust 동거 | 동일 호스트 (localhost). 서버 CPU/메모리 경합 포함됨 |
| locust host | https://localhost:8443 (자체 서명 TLS) |
| SSE 분리 측정 | Phase 6에서 50 VU와 병렬 (M1a-6 sse_keepalive.py) |
| sanity run | 1 VU × 30s, WU_PERF_RESTRICT_HEAVY=true |
| 본 측정 단계 | 1/5/10 VU (RESTRICT_HEAVY=true) → 25/50 VU (해제) |
| snapshot SHA256 | B2E6640C9CA23D0DCA2D33571EB3395CCF034D3B6708E844BA980F7AF3EF41F9 |
