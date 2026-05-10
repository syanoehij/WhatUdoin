# M2-15 HTTPS Probe Middleware Front Router 호환 Probe 결과

- 실행 시각(UTC): 20260509T173100Z
- 전체 결과: PASS

## 검증 항목

| 검증 항목 | 결과 |
|---|---|
| `scheme_preserved_in_forwarded_scope_https` | PASS |
| `scheme_preserved_in_forwarded_scope_http` | PASS |
| `https_inbound_reaches_downstream` | PASS |
| `https_inbound_no_probe_html_title` | PASS |
| `https_inbound_no_probe_html_cookie` | PASS |
| `https_inbound_downstream_scheme_preserved` | PASS |
| `http_browser_inbound_probe_html_title` | PASS |
| `http_browser_inbound_probe_html_cookie` | PASS |
| `http_browser_inbound_status_200` | PASS |
| `http_browser_inbound_no_downstream_reach` | PASS |
| `http_cert_skip_cookie_bypasses_probe` | PASS |
| `http_cert_skip_reaches_downstream` | PASS |
| `http_api_skip_prefix_bypasses_probe` | PASS |
| `http_api_skip_reaches_downstream` | PASS |
| `http_head_bypasses_probe` | PASS |
| `http_cors_non_navigate_bypasses_probe` | PASS |
| `http_cors_non_navigate_reaches_downstream` | PASS |
| `direct_http_no_router_probe_html_title` | PASS |
| `direct_http_no_router_probe_html_cookie` | PASS |
| `direct_http_no_router_no_downstream_reach` | PASS |
| `no_operational_code_changes` | PASS |

## 실패 항목

없음

## 결론

- `_scope_with_router_forwarded_headers()`는 `scheme` 키를 변경하지 않는다.
- FrontRouter ASGI dispatcher를 통해 dispatch된 downstream scope.scheme이 inbound scope.scheme과 동일하게 보존된다.
- 외부 https(8443) 접속 시 scope.scheme=https → `_BrowserHTTPSRedirectMiddleware`의 `scope.get('scheme') != 'http'` 분기로 자연스럽게 통과.
- 외부 직접 http(8000) 접속 시 scope.scheme=http → middleware 동작 → probe HTML 반환.
- middleware 동작은 라우터 유무와 무관하게 동일하다.
- 운영 코드 변경 0건.

## 운영 코드 변경

- `app.py`, `front_router.py`, `auth.py`, `supervisor.py`, `main.py` 변경 **없음**.
- 신규 파일만 추가됨: probe 스크립트, regression 테스트.
