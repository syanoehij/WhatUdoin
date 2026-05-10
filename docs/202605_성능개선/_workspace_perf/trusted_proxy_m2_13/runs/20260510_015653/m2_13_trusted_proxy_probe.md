# M2-13 Trusted Proxy Boundary Probe

- passed: True

| check | result |
|---|---|
| `-m py_compile app.py main.py supervisor.py tests/phase55_front_router_trust_boundary.py` | PASS |
| `tests/phase55_front_router_trust_boundary.py` | PASS |

```json
{
  "commands": [
    {
      "args": [
        "D:\\Program Files\\Python\\Python312\\python.exe",
        "-m",
        "py_compile",
        "app.py",
        "main.py",
        "supervisor.py",
        "tests/phase55_front_router_trust_boundary.py"
      ],
      "returncode": 0,
      "stdout": "",
      "stderr": "",
      "passed": true
    },
    {
      "args": [
        "D:\\Program Files\\Python\\Python312\\python.exe",
        "tests/phase55_front_router_trust_boundary.py"
      ],
      "returncode": 0,
      "stdout": "PASS {'supervisor_factory_uses_web_api_name': True, 'supervisor_factory_preserves_command': True, 'supervisor_factory_keeps_internal_env_together': True, 'supervisor_sets_trusted_proxy': True, 'supervisor_sets_loopback_bind': True, 'supervisor_requires_front_router': True, 'main_supports_bind_host_env': True, 'trusted_proxy_uses_xff': True, 'untrusted_direct_ignores_xff': True, 'internal_only_blocks_external_even_with_loopback_xff': True, 'internal_only_allows_loopback_router': True, 'default_runtime_not_changed': True, 'guard_missing_type_passthrough': True, 'guard_lifespan_passthrough': True}",
      "stderr": "",
      "passed": true
    }
  ],
  "passed": true
}
```
