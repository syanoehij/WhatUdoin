# M2-4 HTTP fallback probe: pre_policy

- expect: allowed
- verdict: FAIL
- marker: `m2_http_20260510_004316`

| endpoint | method | status | outcome | elapsed_ms | body |
|---|---:|---:|---|---:|---|
| `/api/events` | POST | 200 | allowed | 10.2 | `{"id":2912}` |
| `/api/doc/183` | PUT | 200 | allowed | 14.4 | `{"ok":true}` |
| `/api/checklists` | POST | 200 | allowed | 7.9 | `{"id":69}` |
| `/api/upload/image` | POST | 400 | other | 88.3 | `{"detail":"유효하지 않은 이미지 파일입니다."}` |
| `/api/upload/attachment` | POST | 200 | allowed | 5.7 | `{"name":"m2_4.txt","url":"/uploads/meetings/2026/05/20471fa558884b8daa15f3605d21b480.txt","size":15,"uploaded_at":"260510_0043"}` |
| `/api/events` | GET | 200 | allowed | 14.2 | `[{"id":6,"title":"신규 기능 개발","start":"2026-04-10T03:00","end":"2026-04-10T04:00","allDay":false,"classNames":["ev-schedule"],"extendedProps":{"project":null,"description":"123","...` |

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
    "meetings\\2026\\05\\20471fa558884b8daa15f3605d21b480.txt"
  ],
  "port_8000_open_after_stop": false
}
```
