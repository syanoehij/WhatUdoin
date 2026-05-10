# M2-6 AVR policy probe

- verdict: PASS

| check | url | status | pass | evidence |
|---|---|---:|---|---|
| remote redirect | `http://127.0.0.1:8000/remote` | 307 | True | `location=/avr` |
| session login denied | `http://127.0.0.1:8000/avr` | 403 | True | `{"detail":"AVR 접근 권한이 없습니다."}` |
| ip whitelist avr page | `http://127.0.0.1:8000/avr` | 200 | True | `viewer_url_present=True` |
| frame-src csp | `http://127.0.0.1:8000/avr` | 200 | True | `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; frame-src http://127.0.0.1:51050; frame-ancestors 'none'` |
| https plain-http avr redirect | `https://127.0.0.1:8443/avr` | 307 | True | `location=http://127.0.0.1:8000/avr` |

## Cleanup

```json
{
  "rows": {
    "users": 1,
    "user_ips": 1,
    "sessions": 1
  },
  "port_8000_open_after_stop": false,
  "port_8443_open_after_stop": false
}
```
