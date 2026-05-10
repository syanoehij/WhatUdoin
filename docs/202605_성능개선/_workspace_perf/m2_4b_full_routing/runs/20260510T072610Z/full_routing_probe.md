================================================================
m2_4b_live_routing_probe — 20260510T072610Z
================================================================

[1] supervisor 인스턴스 + 토큰 발급
  [PASS] token_file_exists — D:\Github\WhatUdoin\internal_token

[2] SSE service spawn (127.0.0.1:8765)
  [PASS] sse_running — status=running pid=20840
  [PASS] sse_healthz_ok — {'ok': True, 'status': 'ok', 'error': ''}

[3] Web API service spawn (127.0.0.1:8769 internal-only)
  [PASS] web_api_running — status=running pid=28852
  waiting 3s for Web API startup...
  [PASS] web_api_healthz_ok — {'ok': True, 'status': 'ok', 'error': ''}

[4] Front Router service spawn (0.0.0.0:8000/8443)
  [PASS] front_router_running — status=running pid=19484
  waiting 1.5s for Front Router startup...

[5] 외부 http://127.0.0.1:8000/healthz → Front Router → Web API
  [PASS] http_healthz_status_200 — status=200
  [PASS] http_healthz_body_ok — body='{"status":"ok","service":"web-api","sse_publish_failures":0,"sse_publish_last_event":null,"sse_publish_last_at":null}'
    body: '{"status":"ok","service":"web-api","sse_publish_failures":0,"sse_publish_last_event":null,"sse_publish_last_at":null}'

[6] 외부 https://127.0.0.1:8443/healthz → Front Router → Web API
  [PASS] https_healthz_status_200 — status=200
  [PASS] https_healthz_body_ok — body='{"status":"ok","service":"web-api","sse_publish_failures":0,"sse_publish_last_event":null,"sse_publish_last_at":null}'
    body: '{"status":"ok","service":"web-api","sse_publish_failures":0,"sse_publish_last_event":null,"sse_publish_last_at":null}'

[7] 외부 /api/stream → Front Router → SSE service
  [PASS] stream_response_200_and_event_stream — HTTP/1.1 200 OK
date: Sun, 10 May 2026 07:26:20 GMT
server: uvicorn
cache-control: no-cache
x-accel-buffering: no
content-type: text/event-stream; charset=utf-8
connection: close
transfer-encod

[8] 외부 /internal/publish → 404
  [PASS] internal_block_404 — status=404

[9] supervisor.stop_all() → 3 service graceful shutdown
  [PASS] sse_stopped — status=stopped
  [PASS] web-api_stopped — status=stopped
  [PASS] front-router_stopped — status=stopped

================================================================
결과: 15 PASS, 0 FAIL
================================================================