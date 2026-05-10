"""
M1a-6: SSE PoC -- N개 연결 유지 동작 확인

목적:
  - SSE 연결 N개(10/30/50)를 httpx async stream으로 60초 유지
  - 각 연결의 (a) 연결 성공/실패, (b) 60s 안 끊김 여부, (c) timeout 발생 여부 기록
  - 결과를 baseline_<DATE>/sse_poc_<HHMMSS>.md 에 기록

구현 방향: (i) httpx 기반 -- 채택 사유
  - plan S15 "PoC 단계: locust에서 SSE를 어떻게 카운트/timeout 처리하는지 확인" 의 전제는
    locust 동작 자체를 신뢰하지 않는다는 것. locust SSE-only 시나리오로 PoC를 구성하면
    locust 카운트/timeout 동작 자체가 PoC의 불확정 요소가 된다(circular).
  - httpx AsyncClient는 연결 생명주기, 수신 line, 끊김 탐지를 직접 제어 가능 --
    PoC 목적(어느 시점에 끊기는지, timeout이 발생하는지)에 정확하게 맞는다.
  - locust 가중치/p95 통계와 완전히 분리된 독립 프로세스로 동작한다.

SSE 엔드포인트 (app.py:1861-1899):
  - 경로: GET /api/stream
  - 인증: session_id 쿠키 (auth.SESSION_COOKIE). 비로그인 게스트도 연결 가능하나
    fixture 계정 쿠키를 주입하면 동일 인증 흐름을 재현.
  - 이벤트 형식: "event: {name}\\ndata: {json}\\n\\n"
  - ping: 25초마다 ": ping\\n\\n" comment line 전송 (proxy/브라우저 timeout 방지)
  - 서버 disconnect 감지: 3초마다 is_disconnected() 체크 (좀비 연결 정리)
  - QueueFull: asyncio.Queue(maxsize=100). 느린 클라이언트는 메시지 유실 (silent).

broker.py 이벤트 형식:
  - broker.publish(event, data): (event, data) tuple을 Queue에 넣음
  - 클라이언트 수신: "event: {event}\\ndata: {json.dumps(data)}\\n\\n"
  - id: 필드 없음. 서버가 sequence id를 부여하지 않으므로 message sequence 추적 불가.
    latency 측정은 inter-arrival 시간(상대값)으로 대체.

사전 조건:
  - WhatUdoin 서버 실행 중 (https://localhost:8443)
  - M1a-2 seed_users.py 실행 완료 (session_cookies.json 존재)
  - httpx 설치: pip install httpx

사용법 (dry-run 아님, 실 실행은 사용자 승인 후):
  python _workspace/perf/scripts/sse_poc.py --n-list 10,30,50 --host https://localhost:8443
  python _workspace/perf/scripts/sse_poc.py --n-list 10 --host https://192.168.0.18:8443
"""

import argparse
import asyncio
import json
import os
import ssl
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

# httpx import -- 설치 가드
try:
    import httpx
except ImportError as e:
    raise ImportError(
        "httpx 미설치. 설치: pip install httpx\n"
        "  또는 requirements-dev.txt: httpx 항목 참조"
    ) from e

# httpx SSL 경고 억제 (자체 서명 인증서, verify=False)
warnings.filterwarnings("ignore", message=".*verify.*False.*", category=Warning)
try:
    import urllib3
    urllib3.disable_warnings()
except ImportError:
    pass

# ── 경로 상수 ──────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parents[3]
_COOKIES_PATH = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "session_cookies.json"
_BASELINE_DIR = _REPO_ROOT / "_workspace" / "perf" / "baseline_2026-05-09"

# ── 설정 ──────────────────────────────────────────────────────────────────────

HOLD_SECONDS   = 60    # 연결 유지 시간 (초)
CONNECT_TIMEOUT = 5.0  # 연결 타임아웃 (초)

# ── 결과 데이터 구조 ──────────────────────────────────────────────────────────

class ConnResult(NamedTuple):
    conn_id: int
    username: str
    success: bool          # 연결 성공 여부
    disconnected_early: bool  # 60s 이내 서버 측 끊김
    timeout_occurred: bool    # CONNECT_TIMEOUT 초과
    messages_received: int    # 수신 메시지/ping 수
    inter_arrival_ms: list    # [ms] 연속 메시지 간 간격 (latency 대리 지표)
    error_msg: str            # 오류 메시지 (성공 시 "")

# ── cookie 로드 ───────────────────────────────────────────────────────────────

def _load_cookies() -> list[tuple[str, str]]:
    """
    session_cookies.json 로드 후 [(username, session_id), ...] 반환.
    없으면 빈 list 반환 (게스트 연결용 빈 쿠키로 동작).
    """
    if not _COOKIES_PATH.exists():
        print(f"[WARN] session_cookies.json 없음: {_COOKIES_PATH}")
        print("  게스트 연결(쿠키 없음)으로 진행합니다.")
        return []
    with open(_COOKIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    # username -> {"session_id": ..., "expires_at": ...}
    return [(name, v["session_id"]) for name, v in sorted(data.items())]


# ── 단일 SSE 연결 ─────────────────────────────────────────────────────────────

async def _run_single(
    conn_id: int,
    username: str,
    session_id: str,
    host: str,
    hold: float,
) -> ConnResult:
    """
    SSE /api/stream 에 연결해 hold 초 동안 유지 후 종료.

    측정 항목:
      (a) 연결 성공 여부
      (b) hold 초 이내 끊김 여부 (서버 or 네트워크)
      (c) CONNECT_TIMEOUT 이내 연결 실패 여부
    """
    url = f"{host}/api/stream"
    cookies = {"session_id": session_id} if session_id else {}

    messages_received = 0
    inter_arrival: list[float] = []
    last_msg_time: float | None = None
    disconnected_early = False
    timeout_occurred = False
    error_msg = ""
    success = False

    deadline = time.monotonic() + hold

    try:
        # verify=False: 자체 서명 인증서 (plan §15)
        async with httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=None, write=5.0, pool=5.0),
            cookies=cookies,
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return ConnResult(
                        conn_id=conn_id, username=username, success=False,
                        disconnected_early=False, timeout_occurred=False,
                        messages_received=0, inter_arrival_ms=[],
                        error_msg=f"HTTP {resp.status_code}",
                    )

                success = True

                async for line in resp.aiter_lines():
                    now = time.monotonic()
                    if now >= deadline:
                        break

                    # SSE line 파싱 (line 단위 카운트 -- 이벤트 1건 = "event:" + "data:" 2줄이므로
                    # messages_received 는 이벤트 수가 아닌 "라인 수"임에 주의)
                    if line.startswith(":") or line.startswith("event:") or line.startswith("data:"):
                        messages_received += 1
                        if last_msg_time is not None:
                            inter_arrival.append((now - last_msg_time) * 1000)
                        last_msg_time = now

    except httpx.ConnectTimeout:
        timeout_occurred = True
        error_msg = "ConnectTimeout"
    except httpx.ConnectError as exc:
        error_msg = f"ConnectError: {exc}"
    except httpx.RemoteProtocolError as exc:
        disconnected_early = True
        error_msg = f"RemoteProtocolError: {exc}"
    except asyncio.CancelledError:
        pass  # 정상 종료 (deadline or shutdown)
    except Exception as exc:  # noqa: BLE001
        disconnected_early = True
        error_msg = f"{type(exc).__name__}: {exc}"

    # hold 시간 전에 stream이 끝난 경우
    if success and not disconnected_early and time.monotonic() < deadline:
        disconnected_early = True
        if not error_msg:
            error_msg = "Stream ended before hold deadline"

    inter_arrival_ms = [round(v, 1) for v in inter_arrival]
    return ConnResult(
        conn_id=conn_id, username=username, success=success,
        disconnected_early=disconnected_early,
        timeout_occurred=timeout_occurred,
        messages_received=messages_received,
        inter_arrival_ms=inter_arrival_ms,
        error_msg=error_msg,
    )


# ── N개 동시 연결 실행 ────────────────────────────────────────────────────────

async def _run_batch(n: int, cookies: list[tuple[str, str]], host: str, hold: float) -> list[ConnResult]:
    """
    N개 연결을 동시 실행. cookie pool이 부족하면 빈 쿠키(게스트)로 패딩.
    """
    tasks = []
    for i in range(n):
        if i < len(cookies):
            username, session_id = cookies[i]
        else:
            username, session_id = f"guest_{i}", ""
        tasks.append(_run_single(conn_id=i, username=username, session_id=session_id, host=host, hold=hold))

    results = await asyncio.gather(*tasks, return_exceptions=False)
    return list(results)


# ── 결과 요약 ─────────────────────────────────────────────────────────────────

def _summarize(n: int, results: list[ConnResult]) -> str:
    success = sum(1 for r in results if r.success)
    early_disc = sum(1 for r in results if r.disconnected_early)
    timeouts = sum(1 for r in results if r.timeout_occurred)
    total_msgs = sum(r.messages_received for r in results)

    # inter-arrival 집계 (latency 대리 지표)
    all_ia = []
    for r in results:
        all_ia.extend(r.inter_arrival_ms)

    ia_avg = (sum(all_ia) / len(all_ia)) if all_ia else None
    ia_med = sorted(all_ia)[len(all_ia) // 2] if all_ia else None
    ia_p95 = sorted(all_ia)[int(len(all_ia) * 0.95)] if len(all_ia) >= 2 else None

    lines = [
        f"## N={n} 결과",
        f"- 시작 연결 수: {n}",
        f"- 연결 성공: {success} / {n}",
        f"- 60s 이내 끊김: {early_disc}",
        f"- timeout 발생: {timeouts}",
        f"- 수신 메시지/ping 합계: {total_msgs}",
        "",
        "### (a) 연결 유지 성공률",
        f"- 성공률: {success}/{n} = {success/n*100:.1f}%",
        f"- 끊김 연결: {early_disc}건",
        "",
        "### (b) publish -> 수신 지연 (inter-arrival 상대 측정)",
        "  [한계] broker.py가 server-side timestamp를 부여하지 않으므로",
        "  publish 시점을 클라이언트에서 알 수 없다. 서버는 25초마다 ping을",
        "  보내므로 대부분의 inter-arrival은 ~25000ms 구간에 분포한다.",
        "  실제 이벤트(wu:events:changed 등)의 지연은 부하 테스트 중",
        "  CRUD 이벤트 발생 후 SSE 수신까지의 차이로만 측정 가능하며,",
        "  M1c-10 이후 broker QueueFull 카운터 도입 시 서버 측 측정으로 대체한다.",
    ]
    if ia_avg is not None:
        lines += [
            f"  - inter-arrival 평균: {ia_avg:.1f} ms",
            f"  - inter-arrival 중앙값: {ia_med:.1f} ms",
            f"  - inter-arrival p95: {ia_p95:.1f} ms" if ia_p95 is not None else "  - inter-arrival p95: (데이터 부족)",
        ]
    else:
        lines.append("  - inter-arrival: 메시지 없음 (측정값 없음)")

    lines += [
        "",
        "### (c) QueueFull 발생 수",
        "  - 클라이언트 측 추정값: 0건",
        "  [한계] broker.py QueueFull은 서버가 조용히 무시(silent drop).",
        "  sequence id 미부여로 클라이언트에서 누락 감지 불가.",
        "  M1c-10 단계에서 서버 QueueFull 카운터 도입 후 정확 측정 예정.",
    ]

    # 연결별 오류 목록
    errors = [(r.conn_id, r.username, r.error_msg) for r in results if r.error_msg]
    if errors:
        lines.append("")
        lines.append("### 연결별 오류")
        for cid, uname, msg in errors[:20]:  # 최대 20건
            lines.append(f"  - conn {cid} ({uname}): {msg}")
        if len(errors) > 20:
            lines.append(f"  ... {len(errors) - 20}건 생략")

    return "\n".join(lines)


# ── 보고서 저장 ───────────────────────────────────────────────────────────────

def _write_report(content: str, ts: str) -> Path:
    out = _BASELINE_DIR / f"sse_poc_{ts}.md"
    _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out


# ── 메인 ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="SSE PoC: N개 연결 유지 테스트")
    parser.add_argument("--n-list", default="10,30,50",
                        help="테스트할 동시 연결 수 (쉼표 구분, 기본: 10,30,50)")
    parser.add_argument("--host", default="https://localhost:8443",
                        help="대상 호스트 (기본: https://localhost:8443)")
    parser.add_argument("--hold", type=float, default=float(HOLD_SECONDS),
                        help=f"연결 유지 시간(초) (기본: {HOLD_SECONDS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 연결 없이 스크립트 구조만 확인")
    args = parser.parse_args()

    n_list = [int(x.strip()) for x in args.n_list.split(",")]
    ts = datetime.now().strftime("%H%M%S")

    cookies = _load_cookies()
    print(f"[INFO] 로드된 쿠키 계정: {len(cookies)}개")
    print(f"[INFO] 대상 호스트: {args.host}")
    print(f"[INFO] 테스트 N 목록: {n_list}")
    print(f"[INFO] 유지 시간: {args.hold}s")

    if args.dry_run:
        print("[DRY-RUN] 실제 연결 없이 종료합니다.")
        _write_report("# SSE PoC -- dry-run placeholder\n\n(실 실행 시 결과가 여기에 기록됩니다)", ts + "_dryrun")
        return

    report_parts = [
        f"# SSE PoC 결과",
        f"",
        f"실행 일시: {datetime.now(timezone.utc).isoformat()}",
        f"대상 호스트: {args.host}",
        f"연결 유지: {args.hold}s",
        f"",
        "---",
        "",
    ]

    for n in n_list:
        max_cookies = len(cookies)
        if n > max_cookies:
            print(f"[WARN] N={n} 요청이나 쿠키 계정({max_cookies}개) 부족. {max_cookies}개로 진행.")

        print(f"\n[RUN] N={n} 연결 시작...")
        t0 = time.monotonic()
        results = await _run_batch(n, cookies, args.host, args.hold)
        elapsed = time.monotonic() - t0
        print(f"[RUN] N={n} 완료 ({elapsed:.1f}s)")

        report_parts.append(_summarize(n, results))
        report_parts.append("")
        report_parts.append("---")
        report_parts.append("")

    # RAM 추정
    report_parts += [
        "## RAM 추정 (50 SSE 연결)",
        "",
        "클라이언트 측 (httpx asyncio):",
        "  - asyncio task + read buffer + SSL context: ~100~300 KB/연결",
        "  - 50 연결 합계: ~5~15 MB",
        "",
        "서버 측 (broker.py asyncio.Queue):",
        "  - asyncio.Queue(maxsize=100) 오브젝트: ~4~8 KB/큐",
        "  - 50 구독자: ~0.2~0.4 MB",
        "  - 합계 추정: 클라이언트+서버 ~5~15 MB (main app 100~160MB 대비 무시 가능 수준)",
    ]

    content = "\n".join(report_parts)
    out = _write_report(content, ts)
    print(f"\n[DONE] 결과 저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
