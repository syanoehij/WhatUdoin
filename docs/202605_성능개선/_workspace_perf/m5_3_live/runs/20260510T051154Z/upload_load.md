# M5-3 B: 20MB/10MB 혼합 업로드 부하 + 일반 API p95 probe

- **UTC**: 20260510T051958Z
- **방식**: in-process ASGI + mock _call_media_service
- **N**: 50 GET, 10MB PNG + 20MB ZIP mock 업로드
- **측정 타이밍**: 업로드 thread 선기동 후 asyncio.gather 실행 — p95는 업로드 진행 중 측정

## 결과: 8/8 PASS, 0 FAIL, 0 SKIP

### 일반 API 지연 (20MB/10MB 혼합 업로드 진행 중)
| 지표 | 값 |
|------|-----|
| p50 | 78.0ms |
| p95 | 78.0ms |
| p99 | 94.0ms |
| 응답 도달 | 50/50 |
| RSS before | 110.4MB |
| RSS after | 113.0MB |
| RSS delta | +2.6MB |

### 업로드 결과
| kind | ok | elapsed_ms | size_mb |
|------|----|-----------|---------|
| image | True | 16 | 10 |
| attachment | True | 16 | 20 |

| 항목 | 결과 | 비고 |
|------|------|------|
| app.py import 성공 | PASS |  |
| 일반 API p95 < 500.0ms (업로드 중) | PASS | p95=78.0ms (20MB/10MB 혼합 업로드 thread 진행 중 측정) |
| 일반 API 응답 도달 50/50 | PASS | reached=50 |
| RSS 스파이크 < 100MB (업로드 중 50회 GET 기준) | PASS | delta=2.6MB |
| Mock image 업로드 성공 | PASS | elapsed=16ms, error= |
| Mock attachment 업로드 성공 | PASS | elapsed=16ms, error= |
| 10MB 이미지 처리 완료 (60s 이내) | PASS | elapsed=16ms |
| 20MB 첨부파일 처리 완료 (60s 이내) | PASS | elapsed=16ms |

## 측정 한계

- Media service는 mock patch 사용 (라이브 PIL SHA-256 비용 제외).
- 라이브 20MB 처리 시 PIL + SHA-256 1~3s 예상 (PIL 없는 환경에서는 더 빠름).
- RSS는 psutil 없는 환경에서 측정 불가 (best-effort).
- httpx 없는 환경에서는 순차 mock 1ms 루프로 fallback (p95 단언 의미 약함).
- M1c 별도 baseline 재측정 없음 — M4-4 hang 중 일반 API p95=31ms(SLA 500ms 대비 여유) 기준 적용.
- 본 probe의 upload thread는 mock IPC 사용 (실제 /api/upload/* 엔드포인트 미호출).
  라이브 업로드 엔드포인트의 DB write/staging rename 비용은 phase70 회귀에서 별도 검증.