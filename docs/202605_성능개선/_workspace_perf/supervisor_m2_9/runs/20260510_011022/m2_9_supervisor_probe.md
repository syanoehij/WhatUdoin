# M2-9 Supervisor probe

- verdict: PASS
- run_dir: `D:\Github\WhatUdoin\_workspace\perf\supervisor_m2_9\runs\20260510_011022`

| check | result |
|---|---|
| startup_sequence_has_7_steps | True |
| internal_token_file_exists | True |
| internal_token_not_empty | True |
| internal_token_acl_applied | True |
| spawn_env_token_matches_file | True |
| spawn_env_service_name | True |
| web_api_started_with_pid | True |
| startup_failure_counter_only | True |
| runtime_crash_counter_only | True |
| service_log_paths_exist | True |
| py_compile_passed | True |

## Token

```json
{
  "path": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\internal_token",
  "created": true,
  "acl_applied": true,
  "acl_warning": ""
}
```

## Snapshot

```json
{
  "startup_sequence": [
    "resolve_runtime_paths",
    "ensure_internal_token_file",
    "prepare_shared_service_environment",
    "start_front_router_listener",
    "start_web_api_service",
    "start_sse_service",
    "verify_health_and_publish_status"
  ],
  "run_dir": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime",
  "internal_token": {
    "path": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\internal_token",
    "created": false,
    "acl_applied": true,
    "acl_warning": ""
  },
  "services": {
    "web-api": {
      "name": "web-api",
      "status": "stopped",
      "pid": null,
      "started_at": 1778343022.6187372,
      "stopped_at": 1778343025.0872965,
      "restart_count": 0,
      "startup_failures": 0,
      "runtime_crashes": 0,
      "last_error": "",
      "stdout_log": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\logs\\services\\web-api.stdout.log",
      "stderr_log": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\logs\\services\\web-api.stderr.log"
    },
    "startup-fail": {
      "name": "startup-fail",
      "status": "failed_startup",
      "pid": 17416,
      "started_at": 1778343022.9812722,
      "stopped_at": 1778343023.3317437,
      "restart_count": 0,
      "startup_failures": 1,
      "runtime_crashes": 0,
      "last_error": "startup exited with code 7",
      "stdout_log": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\logs\\services\\startup-fail.stdout.log",
      "stderr_log": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\logs\\services\\startup-fail.stderr.log"
    },
    "runtime-crash": {
      "name": "runtime-crash",
      "status": "crashed",
      "pid": 32368,
      "started_at": 1778343023.3317437,
      "stopped_at": 1778343025.0852852,
      "restart_count": 0,
      "startup_failures": 0,
      "runtime_crashes": 1,
      "last_error": "runtime exited with code 9",
      "stdout_log": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\logs\\services\\runtime-crash.stdout.log",
      "stderr_log": "D:\\Github\\WhatUdoin\\_workspace\\perf\\supervisor_m2_9\\runs\\20260510_011022\\runtime\\logs\\services\\runtime-crash.stderr.log"
    }
  }
}
```
