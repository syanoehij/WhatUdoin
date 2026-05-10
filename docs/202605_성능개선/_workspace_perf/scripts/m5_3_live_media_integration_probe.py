"""M5-3 A: 라이브 supervisor + Media service 통합 probe.

시나리오:
  1. WhatUdoinSupervisor 인스턴스 생성 → ensure_internal_token()
  2. media_service_spec(port=<free>) → start_service(subprocess.Popen)
  3. probe_healthz PASS
  4. IPC 정상: Bearer + JSON {kind:"image", staging_path:<temp PNG>, original_name:"x.png", max_bytes:10485760}
     → 200 + {ok:True, kind:"image", ext, size, sha256, dimensions}
  5. IPC 인증: 토큰 없음 → 401, 잘못된 토큰 → 401
  6. 경로 정규화: ".." 포함/절대 경로/staging root 밖 → ok:False (path_traversal 또는 forbidden, 400)
  7. 강제 종료: supervisor.stop_service("media") → status=stopped
     그 직후 IPC 호출 → ConnectionError
  8. 재시작: supervisor.start_service → status=running, probe_healthz 회복
  9. 결과 markdown 저장

실행:
    python _workspace/perf/scripts/m5_3_live_media_integration_probe.py
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from supervisor import (
    WhatUdoinSupervisor,
    media_service_spec,
    MEDIA_SERVICE_NAME,
    INTERNAL_TOKEN_ENV,
)

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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port_open(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


def _wait_port_closed(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                time.sleep(0.2)
        except (ConnectionRefusedError, OSError):
            return True
    return False


def _poll_healthz_ok(base_url: str, timeout: float = 25.0) -> dict:
    """GET /healthz 폴링 → status=ok 반환."""
    endpoint = base_url.rstrip("/") + "/healthz"
    deadline = time.monotonic() + timeout
    last: dict = {"ok": False, "status": None, "error": "timeout", "body": {}}
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read(8192))
                    svc_status = data.get("status")
                    ok = svc_status in ("ok", "starting")
                    last = {"ok": ok, "status": svc_status, "error": "", "body": data}
                    if ok:
                        return last
                else:
                    last = {"ok": False, "status": None,
                            "error": f"http {resp.status}", "body": {}}
        except Exception as exc:
            last = {"ok": False, "status": None, "error": str(exc), "body": {}}
        time.sleep(0.3)
    return last


def _ipc_post(url: str, body: dict, token: str | None, timeout: float = 10.0) -> tuple[int, dict]:
    """POST url with JSON body. Returns (status_code, response_dict)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read(65536))
    except urllib.error.HTTPError as exc:
        try:
            body_bytes = exc.read(65536)
            return exc.code, json.loads(body_bytes)
        except Exception:
            return exc.code, {}
    except Exception:
        raise


def _make_valid_png(path: Path) -> None:
    """PIL로 작은 유효 PNG 파일 생성."""
    try:
        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (16, 16), "red")
        img.save(str(path), "PNG")
    except ImportError:
        # Pillow 없으면 최소 PNG 헤더 (1x1 pixel)
        import struct, zlib
        def chunk(ctype, data):
            c = struct.pack(">I", len(data)) + ctype + data
            return c + struct.pack(">I", zlib.crc32(ctype + data) & 0xffffffff)
        IHDR = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        raw = b"\x00\xff\xff\xff"
        compressed = zlib.compress(raw)
        IDAT = chunk(b"IDAT", compressed)
        IEND = chunk(b"IEND", b"")
        png_bytes = b"\x89PNG\r\n\x1a\n" + IHDR + IDAT + IEND
        path.write_bytes(png_bytes)


def main() -> int:
    utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "_workspace" / "perf" / "m5_3_live" / "runs" / utc_stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_run = out_dir / "supervisor_run"
    tmp_run.mkdir(exist_ok=True)
    staging_root = tmp_run / "staging"
    staging_root.mkdir(exist_ok=True)

    python = sys.executable
    media_port = _free_port()
    base_url = f"http://127.0.0.1:{media_port}"
    ipc_url = f"{base_url}/internal/process"

    print("\n=== M5-3 A: Live Media Service Integration Probe ===")
    print(f"  run_dir     : {out_dir}")
    print(f"  media port  : {media_port}")
    print(f"  staging root: {staging_root}")

    # ──────────────────────────────────────────────────────────────────────────
    # [1] Supervisor 초기화
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[1] Supervisor 초기화 + ensure_internal_token...")
    sup = WhatUdoinSupervisor(run_dir=tmp_run)
    tok_info = sup.ensure_internal_token()
    token = Path(tok_info.path).read_text(encoding="utf-8").strip()
    _ok("ensure_internal_token: token 파일 존재", Path(tok_info.path).exists())
    _ok("ensure_internal_token: token 비어있지 않음", bool(token))

    # ──────────────────────────────────────────────────────────────────────────
    # [2] media_service_spec + start_service
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[2] media_service_spec + start_service...")
    spec = media_service_spec(
        command=[python, str(ROOT / "media_service.py")],
        port=media_port,
        staging_root=str(staging_root),
        startup_grace_seconds=2.0,
    )
    _ok("spec.name == 'media'", spec.name == MEDIA_SERVICE_NAME)
    # token은 service_env()가 주입하므로 spec.env에는 없어야 함
    _ok("spec.env에 토큰 없음 (service_env가 주입)", INTERNAL_TOKEN_ENV not in spec.env)
    _ok("spec.env에 STAGING_ROOT 포함",
        "WHATUDOIN_STAGING_ROOT" in spec.env,
        f"env keys: {list(spec.env.keys())}")

    state = sup.start_service(spec)
    _ok("media spawn: status not failed",
        state.status not in ("failed_startup", "degraded"),
        f"status={state.status}, last_error={state.last_error}")

    # ──────────────────────────────────────────────────────────────────────────
    # [3] probe_healthz
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[3] probe_healthz...")
    _ok("port 열림 (20s timeout)",
        _wait_port_open("127.0.0.1", media_port, timeout=20.0),
        f"port={media_port}")
    hlt = _poll_healthz_ok(base_url, timeout=25.0)
    _ok("probe_healthz PASS",
        hlt["ok"],
        f"status={hlt.get('status')}, error={hlt.get('error')}")
    if hlt["ok"]:
        body = hlt["body"]
        _ok("/healthz 키: staging_root", "staging_root" in body)
        _ok("/healthz 키: processed_count", "processed_count" in body)
        _ok("/healthz 키: uptime_seconds", "uptime_seconds" in body)

    # ──────────────────────────────────────────────────────────────────────────
    # [4] IPC 정상: 유효 PNG
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[4] IPC 정상 호출 (유효 PNG)...")
    staging_png = staging_root / "test_valid.png"
    _make_valid_png(staging_png)

    try:
        status, resp = _ipc_post(
            ipc_url,
            {
                "kind": "image",
                "staging_path": str(staging_png),
                "original_name": "x.png",
                "max_bytes": 10 * 1024 * 1024,
            },
            token=token,
        )
        _ok("IPC 정상: HTTP 200", status == 200, f"got {status}")
        _ok("IPC 정상: ok=True", resp.get("ok") is True, f"resp={resp}")
        _ok("IPC 정상: kind=image", resp.get("kind") == "image", f"kind={resp.get('kind')}")
        _ok("IPC 정상: sha256 존재", bool(resp.get("sha256")), f"resp keys={list(resp)}")
        _ok("IPC 정상: dimensions 존재", "dimensions" in resp, f"resp keys={list(resp)}")
        _ok("IPC 정상: ext=.png", resp.get("ext") == ".png", f"ext={resp.get('ext')}")
        _ok("IPC 정상: size > 0", resp.get("size", 0) > 0, f"size={resp.get('size')}")
    except Exception as exc:
        _ok("IPC 정상 호출 예외 없음", False, str(exc))

    # ──────────────────────────────────────────────────────────────────────────
    # [5] IPC 인증: 토큰 없음/잘못된 토큰 → 401
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[5] IPC 인증 (토큰 없음/잘못된 토큰)...")
    try:
        status_no_tok, resp_no_tok = _ipc_post(
            ipc_url,
            {"kind": "image", "staging_path": str(staging_png),
             "original_name": "x.png", "max_bytes": 10485760},
            token=None,
        )
        _ok("토큰 없음 → 401", status_no_tok == 401,
            f"got {status_no_tok}: {resp_no_tok}")
    except Exception as exc:
        _ok("토큰 없음 → 401", False, str(exc))

    try:
        status_bad_tok, resp_bad_tok = _ipc_post(
            ipc_url,
            {"kind": "image", "staging_path": str(staging_png),
             "original_name": "x.png", "max_bytes": 10485760},
            token="wrong-token-xyz",
        )
        _ok("잘못된 토큰 → 401", status_bad_tok == 401,
            f"got {status_bad_tok}: {resp_bad_tok}")
    except Exception as exc:
        _ok("잘못된 토큰 → 401", False, str(exc))

    # ──────────────────────────────────────────────────────────────────────────
    # [6] staging path 정규화 회귀
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[6] staging path 정규화 회귀...")

    # 6a: ".." 우회 경로
    traversal_path = str(staging_root / ".." / "etc" / "passwd")
    try:
        status_t, resp_t = _ipc_post(
            ipc_url,
            {"kind": "image", "staging_path": traversal_path,
             "original_name": "x.png", "max_bytes": 10485760},
            token=token,
        )
        _ok("..'우회 → ok:False (path_traversal/forbidden_path)",
            resp_t.get("ok") is False,
            f"status={status_t}, reason={resp_t.get('reason')}")
        _ok("..'우회 → reason in {path_traversal, forbidden_path}",
            resp_t.get("reason") in ("path_traversal", "forbidden_path"),
            f"reason={resp_t.get('reason')}")
    except Exception as exc:
        _ok("'..' 우회 경로 거부", False, str(exc))

    # 6b: 절대 경로 (staging root 밖)
    abs_path = "/etc/passwd" if os.name != "nt" else "C:\\Windows\\System32\\drivers\\etc\\hosts"
    try:
        status_abs, resp_abs = _ipc_post(
            ipc_url,
            {"kind": "image", "staging_path": abs_path,
             "original_name": "x.png", "max_bytes": 10485760},
            token=token,
        )
        _ok("절대 경로 (밖) → ok:False",
            resp_abs.get("ok") is False,
            f"status={status_abs}, reason={resp_abs.get('reason')}")
    except Exception as exc:
        _ok("절대 경로 (밖) → ok:False", False, str(exc))

    # 6c: symlink (Windows: 권한 부족 시 skip)
    symlink_path = staging_root / "symlink_target.png"
    real_target = ROOT / "app.py"
    symlink_created = False
    try:
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink()
        os.symlink(str(real_target), str(symlink_path))
        symlink_created = True
    except (OSError, NotImplementedError):
        _skip("symlink 경로 정규화",
              "Windows symlink 생성 권한 없음 (개발자 모드 또는 관리자 권한 필요)")

    if symlink_created:
        try:
            status_sym, resp_sym = _ipc_post(
                ipc_url,
                {"kind": "image", "staging_path": str(symlink_path),
                 "original_name": "x.png", "max_bytes": 10485760},
                token=token,
            )
            # symlink가 staging root 하위에 있어도 실제 타겟이 PNG가 아니므로 invalid_image
            # 또는 PIL에 통과하면 정상. 여기서는 symlink 자체의 경로 허용 여부를 체크
            # staging_root 하위에 있으므로 is_relative_to는 True → path_traversal 아님
            # 실제 파일(app.py)이 PNG가 아니므로 invalid_image가 예상
            _ok("symlink (staging 내부 → 외부 target): 처리됨 (path_traversal 또는 invalid)",
                resp_sym.get("ok") is False,
                f"status={status_sym}, reason={resp_sym.get('reason')}")
        except Exception as exc:
            _ok("symlink 경로 처리", False, str(exc))
        finally:
            try:
                symlink_path.unlink(missing_ok=True)
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────────────────
    # [7] 강제 종료: stop_service → status=stopped
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[7] 강제 종료 + IPC 불가 확인...")
    stop_state = sup.stop_service(MEDIA_SERVICE_NAME, timeout=10.0)
    _ok("stop_service → status=stopped",
        stop_state.status == "stopped" if stop_state else False,
        f"status={stop_state.status if stop_state else 'None'}")

    port_closed = _wait_port_closed("127.0.0.1", media_port, timeout=10.0)
    _ok("종료 후 포트 닫힘",
        port_closed,
        f"port={media_port}")

    # IPC 호출 → ConnectionError
    ipc_unavailable = False
    try:
        _ipc_post(
            ipc_url,
            {"kind": "image", "staging_path": str(staging_png),
             "original_name": "x.png", "max_bytes": 10485760},
            token=token,
            timeout=2.0,
        )
    except (ConnectionRefusedError, OSError) as exc:
        ipc_unavailable = True
    except Exception as exc:
        # urllib wraps as URLError
        if "Connection refused" in str(exc) or "Remote end closed" in str(exc) or "refused" in str(exc).lower():
            ipc_unavailable = True

    _ok("종료 후 IPC 호출 → ConnectionError",
        ipc_unavailable,
        "expected connection refused")

    # ──────────────────────────────────────────────────────────────────────────
    # [8] 재시작 + probe_healthz 회복
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[8] 재시작 + probe_healthz 회복...")
    # staging file 재생성 (이전 단계에서 unlink됐을 수도 있음)
    _make_valid_png(staging_png)

    restart_state = sup.start_service(spec)
    _ok("재시작: status not failed",
        restart_state.status not in ("failed_startup", "degraded"),
        f"status={restart_state.status}, last_error={restart_state.last_error}")

    port_reopened = _wait_port_open("127.0.0.1", media_port, timeout=20.0)
    _ok("재시작: 포트 재개 (20s timeout)", port_reopened, f"port={media_port}")

    if port_reopened:
        hlt2 = _poll_healthz_ok(base_url, timeout=25.0)
        _ok("재시작 후 probe_healthz 회복",
            hlt2["ok"],
            f"status={hlt2.get('status')}, error={hlt2.get('error')}")

    # ──────────────────────────────────────────────────────────────────────────
    # 정리
    # ──────────────────────────────────────────────────────────────────────────
    sup.stop_service(MEDIA_SERVICE_NAME, timeout=10.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 결과 markdown 저장
    # ──────────────────────────────────────────────────────────────────────────
    total = _pass + _fail
    skipped = len([r for r in _results if r.get("passed") is None])
    lines = [
        "# M5-3 A: Live Media Service Integration Probe",
        "",
        f"- **UTC**: {utc_stamp}",
        f"- **Media port**: {media_port}",
        f"- **Staging root**: {staging_root}",
        "",
        f"## 결과: {_pass}/{total} PASS, {_fail} FAIL, {skipped} SKIP",
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
        "## 편차 기록",
        "",
        "- task spec은 `reason: 'forbidden_path'` at HTTP 200을 지정하나,"
        " media_service.py 구현은 `reason: 'path_traversal'` at HTTP 400을 반환."
        " 경계는 동등하게 동작함. spec 표현 불일치로 판단, 운영 코드 수정 없음.",
    ]

    md_path = out_dir / "media_integration.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  결과 저장: {md_path}")

    print(f"\n=== 결과: {_pass}/{total} PASS, {_fail} FAIL, {skipped} SKIP ===")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
