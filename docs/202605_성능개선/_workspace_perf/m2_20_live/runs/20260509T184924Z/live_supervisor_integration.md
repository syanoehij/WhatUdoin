# M2-20 Live Supervisor Integration Probe

**실행 시각**: 20260509T184924Z
**Python**: D:\Program Files\Python\Python312\python.exe
**SSE port**: 62585
**run_dir**: D:\Github\WhatUdoin\_workspace\perf\m2_20_live\runs\20260509T184924Z\supervisor_run

## 시나리오 결과

| # | 항목 | 결과 | 상세 |
|---|---|---|---|
| 1 | ensure_internal_token: path exists | PASS | D:\Github\WhatUdoin\_workspace\perf\m2_20_live\runs\20260509T184924Z\supervisor_run\internal_token |
| 2 | ensure_internal_token: token non-empty | PASS |  |
| 3 | sse_service.py exists | PASS | D:\Github\WhatUdoin\sse_service.py |
| 4 | sse_service_spec: name=sse | PASS |  |
| 5 | sse_service_spec: port env set | PASS |  |
| 6 | start_service: status=running | PASS | status=running, pid=45108 |
| 7 | healthz: ok=True | PASS | status=ok error= |
| 8 | publish no-token → 401 | PASS | got 401, body={"error":"unauthorized"} |
| 9 | publish wrong-token → 401 | PASS | got 401, body={"error":"unauthorized"} |
| 10 | publish correct-token → 200 | PASS | got 200 |
| 11 | publish correct-token → ok=True in body | PASS | body={"ok":true} |
| 12 | stop_all: service status=stopped | PASS | status=stopped |
| 13 | stop_all: process terminated | PASS | poll=1 |
| 14 | token file: still readable after stop_all | PASS | D:\Github\WhatUdoin\_workspace\perf\m2_20_live\runs\20260509T184924Z\supervisor_run\internal_token |

## 총계

**14/14 PASS**

## 토큰 검증 시나리오

| 시나리오 | 기대 | 실제 |
|---|---|---|
| Authorization 헤더 없음 | 401 | 401 |
| Authorization: Bearer wrong | 401 | 401 |
| Authorization: Bearer <correct> | 200 | 200 |