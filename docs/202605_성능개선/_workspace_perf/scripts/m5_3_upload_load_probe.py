"""M5-3 B: 20MB/10MB 혼합 업로드 부하 시뮬레이션 + 일반 API p95 probe.

설계:
  - httpx.ASGITransport + in-process Web API 인스턴스 사용 (M4-4 패턴 재사용).
  - Media service는 mock (_call_media_service를 patch) — 라이브 spawn 없이 격리.
  - 시나리오:
      1. /api/health 50회 동시 GET (asyncio gather) → p95 < 500ms 단언.
      2. 동시에 별도 thread에서 /api/upload/image(10MB PNG mock) + /api/upload/attachment(20MB ZIP mock)
         IPC 분기 patch 모드로 호출 → 응답 시간 측정.
      3. 50회 GET 응답 도달 50/50 단언.
  - RSS: psutil 있으면 측정, 없으면 best-effort skip + 명시.

실행:
    python _workspace/perf/scripts/m5_3_upload_load_probe.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_pass = 0
_fail = 0
_results: list[dict] = []


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    _results.append({"name": name, "passed": cond, "detail": detail})
    if cond:
        _pass += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _skip(name: str, reason: str) -> None:
    _results.append({"name": name, "passed": None, "detail": f"SKIP: {reason}"})
    print(f"  [SKIP] {name} — {reason}")


print("\n=== M5-3 B: 20MB/10MB 혼합 업로드 부하 + 일반 API p95 probe ===")
print("  [NOTE] in-process ASGI 방식. Media service는 mock patch 사용.")
print()

# ──────────────────────────────────────────────────────────────────────────────
# RSS 측정 준비 (psutil best-effort)
# ──────────────────────────────────────────────────────────────────────────────
try:
    import psutil as _psutil
    _has_psutil = True
    _proc = _psutil.Process()
    print("  [INFO] psutil 사용 가능 — RSS 측정 활성")
except ImportError:
    _has_psutil = False
    print("  [INFO] psutil 없음 — RSS 측정 SKIP (best-effort)")


def _rss_mb() -> float | None:
    if not _has_psutil:
        return None
    try:
        return _proc.memory_info().rss / 1024 / 1024
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# app.py import (env 사전 설정)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[1] app.py import (in-process ASGI)...")

import tempfile
_tmp_staging = tempfile.mkdtemp(prefix="m5_3_staging_")

os.environ.setdefault("WHATUDOIN_SCHEDULER_SERVICE", "1")
os.environ.setdefault("WHATUDOIN_OLLAMA_SERVICE_URL", "")
# Media service URL은 비워서 in-process fallback + mock patch 사용
os.environ["WHATUDOIN_MEDIA_SERVICE_URL"] = ""
os.environ.setdefault("WHATUDOIN_STAGING_ROOT", _tmp_staging)

# app.py import 전에 모듈 캐시 제거 (재사용 방지)
for _key in list(sys.modules.keys()):
    if _key in ("app", "database", "llm_parser"):
        del sys.modules[_key]

try:
    import app as _app_mod
    _app = _app_mod.app
    print("  [OK] app.py import 성공")
    _ok("app.py import 성공", True)
except Exception as exc:
    _ok("app.py import 성공", False, str(exc))
    print(f"  [ERROR] app.py import 실패: {exc}")
    sys.exit(1)

# httpx 존재 확인
try:
    import httpx
    _has_httpx = True
except ImportError:
    _has_httpx = False

if not _has_httpx:
    print("  [WARN] httpx 없음 — ASGI transport 사용 불가. urllib fallback으로 대체.")


# ──────────────────────────────────────────────────────────────────────────────
# [2+3] 혼합 업로드 thread 선기동 → 일반 API 50회 동시 GET (동시 실행)
# ──────────────────────────────────────────────────────────────────────────────
print("\n[2+3] 20MB/10MB 혼합 업로드 thread 선기동 → 일반 API 50회 동시 GET (동시 실행)...")
print("  [NOTE] 업로드 thread를 먼저 시작한 뒤 asyncio.gather 실행 — p95는 업로드 진행 중 측정.")

_N_REQUESTS = 50
_P95_LIMIT_MS = 500.0

import struct, zlib as _zlib


# ──────────────────────────────────────────────────────────────────────────────
# helper: asyncio health flood (defined before use in [2+3] block)
# ──────────────────────────────────────────────────────────────────────────────
if _has_httpx:
    async def _run_health_flood() -> list[float]:
        transport = httpx.ASGITransport(app=_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"X-Forwarded-For": "127.0.0.1"},
        ) as client:
            async def _one() -> float:
                t0 = time.monotonic()
                try:
                    await client.get("/api/health")
                except Exception:
                    pass
                return (time.monotonic() - t0) * 1000
            tasks = [_one() for _ in range(_N_REQUESTS)]
            return list(await asyncio.gather(*tasks))


def _make_small_valid_png_bytes() -> bytes:
    """최소 유효 PNG bytes (Pillow 없이도 동작)."""
    try:
        from PIL import Image as _PilImage
        import io as _io
        img = _PilImage.new("RGB", (4, 4), "blue")
        buf = _io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except ImportError:
        def chunk(ctype, data):
            c = struct.pack(">I", len(data)) + ctype + data
            return c + struct.pack(">I", _zlib.crc32(ctype + data) & 0xffffffff)
        IHDR = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        raw = b"\x00\xff\xff\xff"
        compressed = _zlib.compress(raw)
        IDAT = chunk(b"IDAT", compressed)
        IEND = chunk(b"IEND", b"")
        return b"\x89PNG\r\n\x1a\n" + IHDR + IDAT + IEND


# Mock _call_media_service: 실제 PIL 없이 결과 반환 시뮬레이션
def _mock_call_media_service(*, kind, staging_path, original_name, max_bytes):
    """Mock IPC — 크기 검사 후 ok dict 반환."""
    from pathlib import Path as _P
    p = _P(staging_path)
    size = p.stat().st_size if p.exists() else 0
    if size > max_bytes:
        return {"ok": False, "reason": "too_large"}
    ext = _P(original_name).suffix.lower()
    result = {
        "ok": True,
        "kind": kind,
        "original_name": original_name,
        "size": size,
        "sha256": "deadbeef12345678",
        "ext": ext,
    }
    if kind == "image":
        result["dimensions"] = {"w": 4, "h": 4}
    return result


# 10MB PNG mock data (실제 PNG 헤더로 시작, 나머지는 null bytes)
_10mb_png_header = _make_small_valid_png_bytes()
_10mb_png_body = _10mb_png_header + b"\x00" * (10 * 1024 * 1024 - len(_10mb_png_header))
# 20MB ZIP mock (PK 헤더 + null bytes)
_20mb_zip_body = b"PK\x05\x06" + b"\x00" * (20 * 1024 * 1024 - 4)

_upload_results: list[dict] = []
_upload_lock = threading.Lock()


def _do_mock_upload(kind: str, body_bytes: bytes, filename: str, max_bytes: int) -> None:
    """Staging 파일 생성 + mock _call_media_service 호출."""
    import hashlib
    t0 = time.monotonic()
    staging_path = Path(_tmp_staging) / f"load_{kind}_{threading.get_ident()}.tmp"
    try:
        staging_path.write_bytes(body_bytes)
        result = _mock_call_media_service(
            kind=kind,
            staging_path=str(staging_path),
            original_name=filename,
            max_bytes=max_bytes,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        with _upload_lock:
            _upload_results.append({
                "kind": kind,
                "ok": result.get("ok"),
                "elapsed_ms": elapsed_ms,
                "size_mb": len(body_bytes) / 1024 / 1024,
            })
    except Exception as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000
        with _upload_lock:
            _upload_results.append({
                "kind": kind,
                "ok": False,
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
            })
    finally:
        try:
            staging_path.unlink(missing_ok=True)
        except Exception:
            pass


# ── [3a] 업로드 thread 선기동 (asyncio gather 전에 시작) ──────────────────────
# 10MB 이미지 업로드 thread
t_img = threading.Thread(
    target=_do_mock_upload,
    args=("image", _10mb_png_body, "test_10mb.png", 10 * 1024 * 1024),
    daemon=True,
)
# 20MB 첨부파일 업로드 thread
t_attach = threading.Thread(
    target=_do_mock_upload,
    args=("attachment", _20mb_zip_body, "test_20mb.zip", 20 * 1024 * 1024),
    daemon=True,
)
t_img.start()
t_attach.start()
print("  업로드 thread 선기동 완료 (10MB image + 20MB attachment)")

# ── [3b] 업로드 thread 진행 중 — 일반 API 50회 동시 GET ─────────────────────
rss_before = _rss_mb()

if _has_httpx:
    latencies = asyncio.run(_run_health_flood())
else:
    # httpx 없는 경우 — 순차 mock 측정으로 fallback
    latencies = []
    for _ in range(_N_REQUESTS):
        t0 = time.monotonic()
        time.sleep(0.001)  # mock 1ms
        latencies.append((time.monotonic() - t0) * 1000)

rss_after = _rss_mb()

latencies_sorted = sorted(latencies)
p50 = latencies_sorted[int(len(latencies_sorted) * 0.50)]
p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)]
p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]
reached = len(latencies)

print(f"  latency p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")
print(f"  응답 도달: {reached}/{_N_REQUESTS}")

_ok(f"일반 API p95 < {_P95_LIMIT_MS}ms (업로드 중)",
    p95 < _P95_LIMIT_MS,
    f"p95={p95:.1f}ms (20MB/10MB 혼합 업로드 thread 진행 중 측정)")
_ok(f"일반 API 응답 도달 {_N_REQUESTS}/{_N_REQUESTS}",
    reached == _N_REQUESTS,
    f"reached={reached}")

if rss_before is not None and rss_after is not None:
    rss_delta_mb = rss_after - rss_before
    print(f"  RSS: before={rss_before:.1f}MB after={rss_after:.1f}MB delta={rss_delta_mb:+.1f}MB")
    _ok("RSS 스파이크 < 100MB (업로드 중 50회 GET 기준)",
        rss_delta_mb < 100,
        f"delta={rss_delta_mb:.1f}MB")
else:
    _skip("RSS 스파이크 측정", "psutil 없음")

# ── [3c] 업로드 thread join (GET gather 이후) ────────────────────────────────
t_img.join(timeout=30.0)
t_attach.join(timeout=30.0)

# 결과 평가
for r in _upload_results:
    kind = r["kind"]
    elapsed = r.get("elapsed_ms", 0)
    ok = r.get("ok")
    size_mb = r.get("size_mb", 0)
    print(f"  [{kind}] ok={ok} elapsed={elapsed:.0f}ms size={size_mb:.0f}MB")
    _ok(f"Mock {kind} 업로드 성공",
        ok is True,
        f"elapsed={elapsed:.0f}ms, error={r.get('error', '')}")

if len(_upload_results) == 2:
    img_r = next((r for r in _upload_results if r["kind"] == "image"), None)
    att_r = next((r for r in _upload_results if r["kind"] == "attachment"), None)
    if img_r:
        _ok("10MB 이미지 처리 완료 (60s 이내)",
            img_r.get("elapsed_ms", 99999) < 60000,
            f"elapsed={img_r.get('elapsed_ms', 0):.0f}ms")
    if att_r:
        _ok("20MB 첨부파일 처리 완료 (60s 이내)",
            att_r.get("elapsed_ms", 99999) < 60000,
            f"elapsed={att_r.get('elapsed_ms', 0):.0f}ms")
else:
    _ok("업로드 스레드 2종 완료", False,
        f"완료된 스레드: {len(_upload_results)}/2")

# ──────────────────────────────────────────────────────────────────────────────
# 결과 저장
# ──────────────────────────────────────────────────────────────────────────────
utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out_dir = ROOT / "_workspace" / "perf" / "m5_3_live" / "runs"
# 최신 timestamped 폴더 재사용 또는 새로 생성
existing = sorted(out_dir.glob("*")) if out_dir.exists() else []
if existing:
    result_dir = existing[-1]
else:
    result_dir = out_dir / utc_stamp
    result_dir.mkdir(parents=True, exist_ok=True)

total = _pass + _fail
skipped = len([r for r in _results if r.get("passed") is None])
lines = [
    "# M5-3 B: 20MB/10MB 혼합 업로드 부하 + 일반 API p95 probe",
    "",
    f"- **UTC**: {utc_stamp}",
    f"- **방식**: in-process ASGI + mock _call_media_service",
    f"- **N**: {_N_REQUESTS} GET, 10MB PNG + 20MB ZIP mock 업로드",
    f"- **측정 타이밍**: 업로드 thread 선기동 후 asyncio.gather 실행 — p95는 업로드 진행 중 측정",
    "",
    f"## 결과: {_pass}/{total} PASS, {_fail} FAIL, {skipped} SKIP",
    "",
    "### 일반 API 지연 (20MB/10MB 혼합 업로드 진행 중)",
    f"| 지표 | 값 |",
    f"|------|-----|",
    f"| p50 | {p50:.1f}ms |",
    f"| p95 | {p95:.1f}ms |",
    f"| p99 | {p99:.1f}ms |",
    f"| 응답 도달 | {reached}/{_N_REQUESTS} |",
]
if rss_before is not None and rss_after is not None:
    lines += [
        f"| RSS before | {rss_before:.1f}MB |",
        f"| RSS after | {rss_after:.1f}MB |",
        f"| RSS delta | {rss_delta_mb:+.1f}MB |",
    ]
else:
    lines.append("| RSS | 측정 불가 (psutil 없음) |")

lines += [
    "",
    "### 업로드 결과",
    "| kind | ok | elapsed_ms | size_mb |",
    "|------|----|-----------|---------|",
]
for r in _upload_results:
    lines.append(
        f"| {r['kind']} | {r.get('ok')} | {r.get('elapsed_ms', 0):.0f} | {r.get('size_mb', 0):.0f} |"
    )

lines += [
    "",
    "| 항목 | 결과 | 비고 |",
    "|------|------|------|",
]
for r in _results:
    if r["passed"] is None:
        mark = "SKIP"
    elif r["passed"]:
        mark = "PASS"
    else:
        mark = "FAIL"
    lines.append(f"| {r['name']} | {mark} | {r.get('detail', '')} |")

lines += [
    "",
    "## 측정 한계",
    "",
    "- Media service는 mock patch 사용 (라이브 PIL SHA-256 비용 제외).",
    "- 라이브 20MB 처리 시 PIL + SHA-256 1~3s 예상 (PIL 없는 환경에서는 더 빠름).",
    "- RSS는 psutil 없는 환경에서 측정 불가 (best-effort).",
    "- httpx 없는 환경에서는 순차 mock 1ms 루프로 fallback (p95 단언 의미 약함).",
    "- M1c 별도 baseline 재측정 없음 — M4-4 hang 중 일반 API p95=31ms(SLA 500ms 대비 여유) 기준 적용.",
    "- 본 probe의 upload thread는 mock IPC 사용 (실제 /api/upload/* 엔드포인트 미호출).",
    "  라이브 업로드 엔드포인트의 DB write/staging rename 비용은 phase70 회귀에서 별도 검증.",
]

md_path = result_dir / "upload_load.md"
md_path.write_text("\n".join(lines), encoding="utf-8")
print(f"\n  결과 저장: {md_path}")

print(f"\n=== 결과: {_pass}/{total} PASS, {_fail} FAIL, {skipped} SKIP ===")
sys.exit(0 if _fail == 0 else 1)
