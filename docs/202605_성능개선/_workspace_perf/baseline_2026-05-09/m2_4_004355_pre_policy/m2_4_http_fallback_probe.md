# M2-4 HTTP fallback probe: pre_policy

- expect: allowed
- verdict: PASS
- marker: `m2_http_20260510_004355`

| endpoint | method | status | outcome | elapsed_ms | body |
|---|---:|---:|---|---:|---|
| `/api/events` | POST | 200 | allowed | 28.1 | `{"id":2913}` |
| `/api/doc/184` | PUT | 200 | allowed | 19.4 | `{"ok":true}` |
| `/api/checklists` | POST | 200 | allowed | 10.7 | `{"id":70}` |
| `/api/upload/image` | POST | 200 | allowed | 35.8 | `{"url":"/uploads/meetings/2026/05/7ce2f86caf444a51ac4a68147a646749.png"}` |
| `/api/upload/attachment` | POST | 200 | allowed | 5.9 | `{"name":"m2_4.txt","url":"/uploads/meetings/2026/05/93cfc88f813a4ca8ad84ea888a41f491.txt","size":15,"uploaded_at":"260510_0043"}` |
| `/api/events` | GET | 200 | allowed | 13.0 | `[{"id":6,"title":"신규 기능 개발","start":"2026-04-10T03:00","end":"2026-04-10T04:00","allDay":false,"classNames":["ev-schedule"],"extendedProps":{"project":null,"description":"123","...` |

## Cleanup

```json
{
  "rows": {
    "events": 1,
    "checklist_histories": 0,
    "checklists": 1,
    "meeting_histories": 1,
    "meeting_locks": 0,
    "meetings": 1,
    "sessions": 0,
    "user_ips": 1,
    "users": 1
  },
  "files": [
    "meetings\\2026\\05\\7ce2f86caf444a51ac4a68147a646749.png",
    "meetings\\2026\\05\\93cfc88f813a4ca8ad84ea888a41f491.txt"
  ],
  "port_8000_open_after_stop": false
}
```
