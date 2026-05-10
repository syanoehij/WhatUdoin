# M2-0 gate report

Created: 2026-05-10T00:23:50
Run dir: `D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\m2_0_002210`
Host: `https://localhost:8443`

## Verdict

**NO-GO**

- no measurable p95 regression under query/SSE/restart probes

## API probe

| scenario | requests | failures | p50 ms | p95 ms | p99 ms | rps |
|----------|----------|----------|--------|--------|--------|-----|
| baseline_no_sse | 3391 | 0 | 35.4 | 89.8 | 121.3 | 113.03 |
| with_50_sse | 3466 | 0 | 34.3 | 89.3 | 116.7 | 115.53 |
| restart_reconnect_window | 3494 | 0 | 33.0 | 88.3 | 117.9 | 116.47 |

## SSE probe

- Initial connect: 50/50 in 619.1 ms
- Restart reconnect: 50/50 reached 95% threshold in 216.4 ms
- Total reconnect count: 50
- Total connect failures: 100

## Server readiness

- Initial readiness: 3446.3 ms
- Restart readiness: 1923.4 ms

## Limits

- single PC measurement; server and probe share CPU/network
- query pressure only; upload pressure was not part of this M2-0 probe
- SSE publish latency is approximated by connection/reconnect behavior; broker has no server timestamp
