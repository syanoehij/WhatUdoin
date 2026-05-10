# M2-11 Forwarded Header Probe

- passed: True

| case | service | status | result |
|---|---|---:|---|
| web_api_forwarded_strip_then_set | `web_api` | 200 | PASS |
| sse_forwarded_strip_then_set | `sse` | 200 | PASS |
| internal_blocked_no_downstream_headers | `-` | 404 | PASS |

```json
{
  "results": [
    {
      "service": "web_api",
      "path": "/api/events",
      "forwarded_keys_present": [
        "x-real-ip",
        "x-forwarded-port",
        "x-forwarded-proto",
        "x-forwarded-for",
        "x-forwarded-host"
      ],
      "expected_headers": {
        "x-forwarded-for": "192.0.2.10",
        "x-forwarded-host": "whatudoin.local:8443",
        "x-forwarded-proto": "https",
        "x-forwarded-port": "8443",
        "x-real-ip": "192.0.2.10",
        "cookie": "session_id=test-session",
        "authorization": "Bearer keep-me",
        "host": "whatudoin.local:8443"
      },
      "spoof_values_absent": true,
      "no_forwarded_header": true,
      "passed": true,
      "status": 200,
      "name": "web_api_forwarded_strip_then_set"
    },
    {
      "service": "sse",
      "path": "/api/stream",
      "forwarded_keys_present": [
        "x-real-ip",
        "x-forwarded-port",
        "x-forwarded-proto",
        "x-forwarded-for",
        "x-forwarded-host"
      ],
      "expected_headers": {
        "x-forwarded-for": "192.0.2.10",
        "x-forwarded-host": "whatudoin.local:8443",
        "x-forwarded-proto": "https",
        "x-forwarded-port": "8443",
        "x-real-ip": "192.0.2.10",
        "cookie": "session_id=test-session",
        "authorization": "Bearer keep-me",
        "host": "whatudoin.local:8443"
      },
      "spoof_values_absent": true,
      "no_forwarded_header": true,
      "passed": true,
      "status": 200,
      "name": "sse_forwarded_strip_then_set"
    },
    {
      "service": null,
      "path": "/internal/publish",
      "status": 404,
      "downstream_calls": 0,
      "passed": true,
      "name": "internal_blocked_no_downstream_headers"
    }
  ],
  "passed": true
}
```
