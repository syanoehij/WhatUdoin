# M2-5 HSTS probe

- verdict: PASS

| url | status | strict-transport-security | elapsed_ms |
|---|---:|---|---:|
| `https://127.0.0.1:8443/api/health` | 200 | `(absent)` | 25.6 |
| `https://127.0.0.1:8443/` | 303 | `(absent)` | 33.1 |
| `http://127.0.0.1:8000/api/health` | 200 | `(absent)` | 3.1 |

## Cleanup

```json
{
  "port_8000_open_after_stop": false,
  "port_8443_open_after_stop": false
}
```
