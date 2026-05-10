# M2-14 CSRF Host 보존 조건 Probe 결과

- 실행 시각(UTC): 20260509T172318Z
- 전체 결과: PASS

## 검증 항목

| 검증 항목 | 결과 |
|---|---|
| `host_not_in_stripped_headers` | PASS |
| `downstream_host_preserved_normal` | PASS |
| `downstream_x_forwarded_host_equals_external` | PASS |
| `downstream_host_unchanged_on_xff_host_attack` | PASS |
| `downstream_x_forwarded_host_not_attacker` | PASS |
| `downstream_x_forwarded_host_is_external_after_attack` | PASS |
| `sse_downstream_host_preserved` | PASS |
| `sse_downstream_x_forwarded_host_equals_external` | PASS |
| `csrf_a_lan_ip_same_origin_passes` | PASS |
| `csrf_b_hostname_same_origin_passes` | PASS |
| `csrf_c_cross_origin_blocked` | PASS |
| `csrf_d_no_src_bypasses_check` | PASS |
| `multi_domain_lan_ip_passes` | PASS |
| `multi_domain_hostname_passes` | PASS |
| `multi_domain_cross_origin_blocked` | PASS |

## 실패 항목

없음

## 채택 결론: Host 보존 방식

- `front_router.FORWARDED_HEADER_NAMES`에 `host`가 없으므로 inbound Host 헤더는 strip되지 않는다.
- FrontRouter는 ASGI scope를 그대로 downstream에 전달하므로 외부 원본 Host가 유지된다.
- `app._check_csrf()`는 `Host` 헤더만 비교하므로 운영 코드 변경 없이 CSRF 검증이 동작한다.
- M2-11 strip-then-set 정책으로 외부 위조 X-Forwarded-Host는 라우터가 외부 원본 값으로 덮어씌워 무력화된다.

## 정책 결과: 외부 도메인 다중 정책

외부 도메인이 PC LAN IP(`192.168.0.18:8443`)와 사내 hostname(`whatudoin-host:8443`) 두 개일 때:

- 동일 origin 내부 요청(Origin netloc == Host)은 모두 통과한다.
  - `192.168.0.18:8443` → `https://192.168.0.18:8443/`: **통과**
  - `whatudoin-host:8443` → `https://whatudoin-host:8443/`: **통과**
- Cross-origin 요청(Origin netloc != Host)은 403으로 차단된다.
  - Host=`192.168.0.18:8443`, Origin=`https://whatudoin-host:8443/`: **403**
  - Host=`192.168.0.18:8443`, Origin=`https://attacker.example/`: **403**

> 주의: Origin/Referer 모두 없는 경우(`src == ''`)는 검증이 우회된다(현재 정책).
> 이는 의도된 동작이며 별도 보안 개선이 필요하면 M3 이후 검토.

## 운영 코드 변경

- `app.py`, `front_router.py`, `auth.py`, `supervisor.py` 변경 **없음**.
- 신규 파일만 추가됨: probe 스크립트, regression 테스트.
