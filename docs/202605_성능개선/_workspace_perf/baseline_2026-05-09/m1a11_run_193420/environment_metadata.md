# 환경 메타데이터 — M1a-11 baseline

측정 시작: 2026-05-09 19:34:21
run 디렉터리: D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\m1a11_run_193420

## 시스템

| 항목 | 값 |
|------|-----|
| OS | Windows 11 10.0.26200 |
| CPU | AMD64 Family 26 Model 36 Stepping 0, AuthenticAMD (24 logical) |
| RAM | 23.6 GB |
| Python | 3.12.9 |
| Node | v24.14.1 |
| Playwright | Version 1.59.1 |
| locust | locust 2.43.4 from D:\Program Files\Python\Python312\Lib\site-packages\locust (Python 3.12.9) |
| httpx | 0.28.1 |

## DB 상태 (측정 시작 전)

| 항목 | 값 |
|------|-----|
| whatudoin.db 크기 | 1036.0 KB |
| users rows | 3 |
| events rows | 524 |
| checklists rows | 65 |
| notifications rows | 87 |

## 측정 환경 정책

| 항목 | 상태 |
|------|------|
| 서버 바인드 | 0.0.0.0:8443 (TLS) — 192.168.0.18:8443 접근 포함 |
| Playwright BASE | https://192.168.0.18:8443 (IP whitelist 자동 로그인) |
| 캐시 비활성화 | CDP Network.setCacheDisabled(true) |
| CPU throttle | CDP Emulation.setCPUThrottlingRate(rate=4) |
| seed 쿠키 | session_cookies.json (50건) — IP whitelist 자동 로그인 보조 |
| M1a-7 baseline | D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09/run_181951/ |
