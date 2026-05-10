# M2-12 SlowAPI Limiter Probe

- passed: True
- key_func: `auth.get_client_ip`

| check | result |
|---|---|
| limiter_uses_auth_get_client_ip | PASS |
| trusted_proxy_50_distinct_buckets | PASS |
| trusted_proxy_first_forwarded_ip | PASS |
| untrusted_proxy_ignores_forwarded | PASS |
| trusted_proxy_without_forwarded_falls_back_peer | PASS |
| same_ip_11th_request_429 | PASS |

```json
{
  "key_func": "auth.get_client_ip",
  "trusted_keys_sample": [
    "192.0.2.1",
    "192.0.2.2",
    "192.0.2.3",
    "192.0.2.4",
    "192.0.2.5"
  ],
  "trusted_key_count": 50,
  "untrusted_key": "203.0.113.9",
  "trusted_same_proxy_key": "198.51.100.7",
  "no_forwarded_key": "127.0.0.1",
  "statuses": [
    200,
    200,
    200,
    200,
    200,
    200,
    200,
    200,
    200,
    200,
    429
  ],
  "rate_limit_counter_for_192_0_2_77": 11,
  "checks": {
    "limiter_uses_auth_get_client_ip": true,
    "trusted_proxy_50_distinct_buckets": true,
    "trusted_proxy_first_forwarded_ip": true,
    "untrusted_proxy_ignores_forwarded": true,
    "trusted_proxy_without_forwarded_falls_back_peer": true,
    "same_ip_11th_request_429": true
  },
  "passed": true
}
```
