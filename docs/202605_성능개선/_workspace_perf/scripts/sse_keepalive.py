"""
M1a-6: SSE 분리 keep-alive 스크립트

목적:
  - main locust 시나리오와 동시 실행 가능한 50 SSE 연결 keep-alive
  - 측정 지표 3종을 main API p95와 분리 기록:
      (a) 연결 유지 성공률
      (b) publish -> 수신 지연 (inter-arrival 상대 측정)
      (c) QueueFull 발생 수 (클라이언트 측 추정 -- 한계 명시)
  - 결과: baseline_<DATE>/sse_keepalive_<HHMMSS>.csv

SSE 엔드포인트 (app.py:1861-1899):
  - 경로: GET /api/stream
  - 인증: session_id 쿠키
  - 이벤트: "event: {name}\\ndata: {json}\\n\\n"
  - ping comment: 25초마다 ": ping\\n\\n"
  - 서버 disconnect 감지 루프: 3초마다 is_disconnected() (서버 측)

broker.py 메시지 형식:
  - id: 필드 없음 -- sequence 추적 불가
  - QueueFull: 서버가 조용히 무시. 클라이언트 측 검출 불가.
    M1c-10 단계에서 서버 QueueFull 카운터 도입 후 정확 측정 예정.

Cookie pool 분리 정책:
  - main locust (M1a-5)는 test_perf_001~050 계정 인덱스 0~49 사용.
  - 본 스크립트는 동일 50개 계정을 재사용한다.
    [근거] SSE /api/stream 은 GET-only. CRUD 요청이 없으므로
    동일 session_id를 main locust VU와 공유해도 데이터 interleave 위험이 없다.
    auth.get_session_user()에 single-connection-per-session 강제가 없음을
    auth.py SESSION_COOKIE 구현에서 확인함 (grep: SESSION_COOKIE 단순 쿠키 조회).
    M1a-4 §4.2 다중 탭 모델(12 VU x 2탭)의 SSE 탭 시뮬레이션과 동일.
  - 50개 미만 계정이라면 게스트(빈 쿠키)로 패딩하며 docstring에 명시.
  - 향후 100개 계정이 필요하면 seed_users.py 범위 확장 후 --cookie-offset CLI 인자 활용.

동시 실행 방법 (사용자 승인 후):
  터미널 1: locust --host https://192.168.0.18:8443 -f locustfile.py --headless -u 50 -r 5
  터미널 2: python _workspace/perf/scripts/sse_keepalive.py --host https://192.168.0.18:8443

사전 조건:
  - WhatUdoin 서버 실행 중 (https://192.168.0.18:8443 또는 localhost)
  - M1a-2 seed_users.py 실행 완료 (session_cookies.json 존재)
  - httpx 설치: pip install httpx

사용법:
  python sse_keepalive.py [--host URL] [--n 50] [--duration 300] [--output-dir PATH]
"""

import argparse
import asyncio
import csv
import json
import os
import time
import warnings
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError as e:
    raise ImportError("httpx 미설치. 설치: pip install httpx") from e

warnings.filterwarnings("ignore", message=".*verify.*", category=Warning)
try:
    import urllib3
    urllib3.disable_warnings()
except ImportError:
    pass

# ── 경로 ──────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parents[3]
_COOKIES_PATH = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "session_cookies.json"
_BASELINE_DIR = _REPO_ROOT / "_workspace" / "perf" / "baseline_2026-05-09"

# ── 설정 ──────────────────────────────────────────────────────────────────────

DEFAULT_N        = 50
DEFAULT_DURATION = 300   # 초 (5분 -- locust 기본 측정 윈도우에 맞춤)
CONNECT_TIMEOUT  = 5.0
REPORT_INTERVAL  = 30    # 초마다 진행 상황 콘솔 출력

# ── 데이터 구조 ───────────────────────────────────────────────────────────────

@dataclass
class ConnStat:
    conn_id: int
    username: str
    # (a) 연결 유지
    connected: bool = False
    connect_time_ms: float = 0.0     # 연결 수립 소요 시간
    disconnected_early: bool = False  # duration 이내 끊김
    reconnect_count: int = 0          # 끊김 후 재연결 시도 수
    # (b) inter-arrival latency
    messages_received: int = 0
    inter_arrival_ms: list = field(default_factory=list)
    ia_avg_ms: float = 0.0
    ia_p95_ms: float = 0.0
    # (c) QueueFull 추정
    queue_full_est: int = 0   # 항상 0 (클라이언트 측 검출 불가, M1c-10 이후 대체)
    # 오류
    last_error: str = ""


def _compute_ia_stats(ia_list: list[float]) -> tuple[float, float]:
    """(avg, p95) 반환. 데이터 없으면 (0, 0)."""
    if not ia_list:
        return 0.0, 0.0
    avg = sum(ia_list) / len(ia_list)
    sorted_ia = sorted(ia_list)
    p95 = sorted_ia[int(len(sorted_ia) * 0.95)]
    return round(avg, 1), round(p95, 1)


# ── cookie 로드 ───────────────────────────────────────────────────────────────

def _load_cookies() -> list[tuple[str, str]]:
    """[(username, session_id), ...] 반환. 없으면 빈 list."""
    if not _COOKIES_PATH.exists():
        print(f"[WARN] session_cookies.json 없음: {_COOKIES_PATH}")
        print("  게스트 연결(빈 쿠키)으로 진행합니다.")
        return []
    with open(_COOKIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [(name, v["session_id"]) for name, v in sorted(data.items())]


# ── 단일 SSE 연결 (재연결 포함) ───────────────────────────────────────────────

async def _run_single(stat: ConnStat, host: str, session_id: str, deadline: float) -> None:
    """
    duration 동안 SSE 연결 유지. 끊기면 재연결 시도.

    측정:
      (a) connected, disconnected_early, reconnect_count
      (b) messages_received, inter_arrival_ms
      (c) queue_full_est = 0 (클라이언트 측 검출 불가)
    """
    url = f"{host}/api/stream"
    cookies = {"session_id": session_id} if session_id else {}

    while time.monotonic() < deadline:
        t_connect_start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                verify=False,
                timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=None, write=5.0, pool=5.0),
                cookies=cookies,
            ) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        stat.last_error = f"HTTP {resp.status_code}"
                        stat.disconnected_early = True
                        break

                    if not stat.connected:
                        stat.connected = True
                        stat.connect_time_ms = round((time.monotonic() - t_connect_start) * 1000, 1)
                    else:
                        stat.reconnect_count += 1

                    last_msg_t: Optional[float] = None

                    async for line in resp.aiter_lines():
                        if time.monotonic() >= deadline:
                            return  # 정상 종료

                        # line 단위 카운트: 이벤트 1건 = "event:" + "data:" 2줄.
                        # CSV의 messages_received는 "이벤트 수"가 아닌 "라인 수".
                        # ping ":..." = 1줄, 일반 이벤트 = 2줄로 합산됨에 주의.
                        if line.startswith(":") or line.startswith("event:") or line.startswith("data:"):
                            now = time.monotonic()
                            stat.messages_received += 1
                            if last_msg_t is not None:
                                stat.inter_arrival_ms.append(round((now - last_msg_t) * 1000, 1))
                            last_msg_t = now

        except httpx.ConnectTimeout:
            stat.last_error = "ConnectTimeout"
            await asyncio.sleep(2.0)
        except httpx.ConnectError as exc:
            stat.last_error = f"ConnectError: {exc}"
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            stat.last_error = f"{type(exc).__name__}: {exc}"
            if time.monotonic() < deadline:
                await asyncio.sleep(1.0)
            continue

    # 정상 hold 완료 후에도 connected=False면 끊김 표시
    if not stat.connected:
        stat.disconnected_early = True
    elif stat.reconnect_count > 0:
        # 재연결이 있었으면 끊김으로 기록
        stat.disconnected_early = True


# ── 전체 실행 ─────────────────────────────────────────────────────────────────

async def run(n: int, host: str, duration: float, output_dir: Path) -> Path:
    cookies = _load_cookies()
    print(f"[INFO] 쿠키 계정: {len(cookies)}개, 요청 연결: {n}개")

    if len(cookies) < n:
        print(f"[WARN] 계정({len(cookies)}) < 요청({n}). 나머지는 게스트(빈 쿠키)로 패딩.")

    stats = []
    for i in range(n):
        if i < len(cookies):
            username, session_id = cookies[i]
        else:
            username, session_id = f"guest_{i}", ""
        stats.append(ConnStat(conn_id=i, username=username))

    deadline = time.monotonic() + duration
    print(f"[INFO] {duration:.0f}s 동안 {n}개 연결 유지 시작...")

    # 진행 보고 태스크
    async def _progress():
        while time.monotonic() < deadline:
            await asyncio.sleep(REPORT_INTERVAL)
            remaining = max(0, deadline - time.monotonic())
            connected_now = sum(1 for s in stats if s.connected and not s.disconnected_early)
            print(f"[PROGRESS] 연결 중: {connected_now}/{n}, 남은 시간: {remaining:.0f}s")

    tasks = [
        _run_single(stats[i], host, cookies[i][1] if i < len(cookies) else "", deadline)
        for i in range(n)
    ]
    tasks.append(_progress())

    await asyncio.gather(*tasks, return_exceptions=True)

    # 통계 집계
    total = len(stats)
    ok = sum(1 for s in stats if s.connected and not s.disconnected_early)
    early = sum(1 for s in stats if s.disconnected_early)
    success_rate = ok / total * 100 if total else 0

    print(f"\n[결과 요약]")
    print(f"  (a) 연결 유지 성공률: {ok}/{total} = {success_rate:.1f}%")
    print(f"      끊김 발생: {early}건, 재연결 합계: {sum(s.reconnect_count for s in stats)}건")

    # latency 요약
    all_ia = []
    for s in stats:
        all_ia.extend(s.inter_arrival_ms)
    if all_ia:
        avg_ia = sum(all_ia) / len(all_ia)
        p95_ia = sorted(all_ia)[int(len(all_ia) * 0.95)]
        print(f"  (b) inter-arrival 평균: {avg_ia:.1f}ms, p95: {p95_ia:.1f}ms")
        print(f"      [한계] broker.py server-side timestamp 없음. 대부분 ~25s(ping 주기) 간격.")
        print(f"      실제 이벤트 latency는 M1c-10 QueueFull 카운터 도입 후 정확 측정 가능.")
    else:
        print(f"  (b) inter-arrival: 메시지 없음 (서버 이벤트 발생 없음)")

    print(f"  (c) QueueFull 추정: 0건 (클라이언트 측 검출 불가. M1c-10 이후 서버 카운터로 대체)")

    # CSV 저장
    ts = datetime.now().strftime("%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"sse_keepalive_{ts}.csv"

    for s in stats:
        s.ia_avg_ms, s.ia_p95_ms = _compute_ia_stats(s.inter_arrival_ms)

    fieldnames = [
        "conn_id", "username", "connected", "connect_time_ms",
        "disconnected_early", "reconnect_count",
        "messages_received", "ia_avg_ms", "ia_p95_ms",
        "queue_full_est", "last_error",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in stats:
            row = asdict(s)
            row.pop("inter_arrival_ms", None)  # 대량 데이터 제외
            writer.writerow({k: row[k] for k in fieldnames})

    # 요약 md 추가
    md_path = output_dir / f"sse_keepalive_{ts}.md"
    md_lines = [
        "# SSE keep-alive 결과",
        "",
        f"실행 일시: {datetime.now(timezone.utc).isoformat()}",
        f"대상 호스트: {host}",
        f"연결 수: {n}",
        f"측정 시간: {duration:.0f}s",
        "",
        "## 지표 3종",
        "",
        "### (a) 연결 유지 성공률",
        f"- 성공: {ok}/{total} = {success_rate:.1f}%",
        f"- 끊김: {early}건",
        f"- 재연결 합계: {sum(s.reconnect_count for s in stats)}건",
        "",
        "### (b) publish -> 수신 지연 (inter-arrival 상대 측정)",
        "  [한계] broker.py에 server-side timestamp 없음(id: 필드 미부여).",
        "  inter-arrival 값은 대부분 ~25000ms(ping 주기) 구간에 분포.",
        "  실제 이벤트(CRUD publish) latency는 M1c-10 이후 정확 측정 가능.",
    ]
    if all_ia:
        md_lines += [
            f"  - inter-arrival 평균: {avg_ia:.1f} ms",
            f"  - inter-arrival p95: {p95_ia:.1f} ms",
        ]
    else:
        md_lines.append("  - 측정 데이터 없음 (서버 이벤트 발생 없었거나 연결 실패)")
    md_lines += [
        "",
        "### (c) QueueFull 발생 수",
        "  - 클라이언트 추정: 0건",
        "  - [한계] broker.py QueueFull은 silent drop. sequence id 없어 클라이언트 검출 불가.",
        "  - M1c-10 단계에서 서버 측 카운터로 교체 예정.",
        "",
        f"## 상세 CSV",
        f"  {csv_path}",
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"\n[DONE] CSV: {csv_path}")
    print(f"[DONE] MD:  {md_path}")
    return csv_path


def main():
    parser = argparse.ArgumentParser(description="SSE keep-alive: 50 연결 유지 측정")
    parser.add_argument("--host", default="https://localhost:8443")
    parser.add_argument("--n", type=int, default=DEFAULT_N,
                        help=f"동시 연결 수 (기본: {DEFAULT_N})")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION,
                        help=f"측정 시간(초) (기본: {DEFAULT_DURATION})")
    parser.add_argument("--output-dir", default=str(_BASELINE_DIR),
                        help="결과 저장 디렉터리")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    asyncio.run(run(args.n, args.host, args.duration, output_dir))


if __name__ == "__main__":
    main()
