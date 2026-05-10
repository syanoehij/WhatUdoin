# M2-10 Front Router Probe

- passed: True
- route_table: `(('/api/stream', 'sse_service'), ('/uploads/meetings/*', 'web_api_service'), ('/internal/*', 'blocked'), ('/*', 'web_api_service'))`

| case | path | status | router_target | router_rule | downstream | result |
|---|---|---:|---|---|---|---|
| api_stream_to_sse | `/api/stream` | 200 | `sse_service` | `sse_stream` | `sse` | PASS |
| api_events_to_web_api | `/api/events` | 200 | `web_api_service` | `default_web_api` | `web_api` | PASS |
| root_to_web_api | `/` | 200 | `web_api_service` | `default_web_api` | `web_api` | PASS |
| uploads_meetings_to_web_api | `/uploads/meetings/2026/05/test.png` | 200 | `web_api_service` | `protected_meeting_upload` | `web_api` | PASS |
| internal_blocked | `/internal/publish` | 404 | `blocked` | `external_internal_block` | `-` | PASS |

```json
{
  "route_table": [
    [
      "/api/stream",
      "sse_service"
    ],
    [
      "/uploads/meetings/*",
      "web_api_service"
    ],
    [
      "/internal/*",
      "blocked"
    ],
    [
      "/*",
      "web_api_service"
    ]
  ],
  "results": [
    {
      "name": "api_stream_to_sse",
      "path": "/api/stream",
      "status": 200,
      "router_target": "sse_service",
      "router_rule": "sse_stream",
      "downstream_calls": [
        {
          "service": "sse",
          "path": "/api/stream"
        }
      ],
      "passed": true
    },
    {
      "name": "api_events_to_web_api",
      "path": "/api/events",
      "status": 200,
      "router_target": "web_api_service",
      "router_rule": "default_web_api",
      "downstream_calls": [
        {
          "service": "web_api",
          "path": "/api/events"
        }
      ],
      "passed": true
    },
    {
      "name": "root_to_web_api",
      "path": "/",
      "status": 200,
      "router_target": "web_api_service",
      "router_rule": "default_web_api",
      "downstream_calls": [
        {
          "service": "web_api",
          "path": "/"
        }
      ],
      "passed": true
    },
    {
      "name": "uploads_meetings_to_web_api",
      "path": "/uploads/meetings/2026/05/test.png",
      "status": 200,
      "router_target": "web_api_service",
      "router_rule": "protected_meeting_upload",
      "downstream_calls": [
        {
          "service": "web_api",
          "path": "/uploads/meetings/2026/05/test.png"
        }
      ],
      "passed": true
    },
    {
      "name": "internal_blocked",
      "path": "/internal/publish",
      "status": 404,
      "router_target": "blocked",
      "router_rule": "external_internal_block",
      "downstream_calls": [],
      "passed": true
    }
  ],
  "passed": true
}
```
