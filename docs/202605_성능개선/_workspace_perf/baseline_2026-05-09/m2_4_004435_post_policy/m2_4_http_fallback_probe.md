# M2-4 HTTP fallback probe: post_policy

- expect: blocked
- verdict: PASS
- marker: `m2_http_20260510_004435`

| endpoint | method | status | outcome | elapsed_ms | body |
|---|---:|---:|---|---:|---|
| `/api/events` | POST | 403 | blocked | 24.3 | `{"detail": "HTTP fallback에서는 쓰기 요청이 차단됩니다. HTTPS로 접속하세요."}` |
| `/api/doc/185` | PUT | 403 | blocked | 0.8 | `{"detail": "HTTP fallback에서는 쓰기 요청이 차단됩니다. HTTPS로 접속하세요."}` |
| `/api/checklists` | POST | 403 | blocked | 0.7 | `{"detail": "HTTP fallback에서는 쓰기 요청이 차단됩니다. HTTPS로 접속하세요."}` |
| `/api/upload/image` | POST | 403 | blocked | 1.2 | `{"detail": "HTTP fallback에서는 쓰기 요청이 차단됩니다. HTTPS로 접속하세요."}` |
| `/api/upload/attachment` | POST | 403 | blocked | 0.7 | `{"detail": "HTTP fallback에서는 쓰기 요청이 차단됩니다. HTTPS로 접속하세요."}` |
| `/api/events` | GET | 200 | allowed | 11.1 | `[{"id":6,"title":"신규 기능 개발","start":"2026-04-10T03:00","end":"2026-04-10T04:00","allDay":false,"classNames":["ev-schedule"],"extendedProps":{"project":null,"description":"123","...` |

## Cleanup

```json
{
  "rows": {
    "events": 0,
    "checklist_histories": 0,
    "checklists": 0,
    "meeting_histories": 0,
    "meeting_locks": 0,
    "meetings": 1,
    "sessions": 0,
    "user_ips": 1,
    "users": 1
  },
  "files": [],
  "port_8000_open_after_stop": false
}
```
