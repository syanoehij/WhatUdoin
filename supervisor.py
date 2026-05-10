from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


_RUN_DIR = Path(os.environ.get("WHATUDOIN_RUN_DIR", Path(__file__).parent))

INTERNAL_TOKEN_FILE_NAME = "internal_token"
INTERNAL_TOKEN_ENV = "WHATUDOIN_INTERNAL_TOKEN"
INTERNAL_TOKEN_FILE_ENV = "WHATUDOIN_INTERNAL_TOKEN_FILE"
SERVICE_NAME_ENV = "WHATUDOIN_SERVICE_NAME"
TRUSTED_PROXY_ENV = "TRUSTED_PROXY"
WEB_API_BIND_HOST_ENV = "WHATUDOIN_BIND_HOST"
WEB_API_INTERNAL_ONLY_ENV = "WHATUDOIN_WEB_API_INTERNAL_ONLY"
FRONT_ROUTER_LOOPBACK_HOST = "127.0.0.1"
WEB_API_SERVICE_NAME = "web-api"
SSE_SERVICE_NAME = "sse"
SSE_SERVICE_BIND_HOST_ENV = "WHATUDOIN_BIND_HOST"
SSE_SERVICE_PORT_ENV = "WHATUDOIN_SSE_SERVICE_PORT"
SSE_SERVICE_PUBLISH_URL_ENV = "WHATUDOIN_SSE_PUBLISH_URL"
SSE_SERVICE_URL_ENV = "WHATUDOIN_SSE_SERVICE_URL"
SSE_SERVICE_DEFAULT_PORT = 8765

# M3-2: Scheduler service 상수
SCHEDULER_SERVICE_NAME = "scheduler"
SCHEDULER_SERVICE_BIND_HOST_ENV = "WHATUDOIN_SCHEDULER_BIND_HOST"
SCHEDULER_SERVICE_PORT_ENV = "WHATUDOIN_SCHEDULER_PORT"
SCHEDULER_SERVICE_DEFAULT_PORT = 8766
SCHEDULER_SERVICE_ENABLE_ENV = "WHATUDOIN_SCHEDULER_SERVICE"

# M4-1: Ollama service 상수
OLLAMA_SERVICE_NAME = "ollama"
OLLAMA_SERVICE_BIND_HOST_ENV = "WHATUDOIN_OLLAMA_BIND_HOST"
OLLAMA_SERVICE_PORT_ENV = "WHATUDOIN_OLLAMA_PORT"
OLLAMA_SERVICE_URL_ENV = "WHATUDOIN_OLLAMA_SERVICE_URL"
OLLAMA_SERVICE_DEFAULT_PORT = 8767

# M5-2: Media service 상수
MEDIA_SERVICE_NAME = "media"
MEDIA_SERVICE_BIND_HOST_ENV = "WHATUDOIN_MEDIA_BIND_HOST"
MEDIA_SERVICE_PORT_ENV = "WHATUDOIN_MEDIA_PORT"
MEDIA_SERVICE_URL_ENV = "WHATUDOIN_MEDIA_SERVICE_URL"
MEDIA_SERVICE_DEFAULT_PORT = 8768
MEDIA_SERVICE_STAGING_ROOT_ENV = "WHATUDOIN_STAGING_ROOT"

CRASH_LOOP_WINDOW_SECONDS = 300
CRASH_LOOP_MAX_FAILURES = 3

# M3-3/M5-2: graceful shutdown 순서 — 의존 서비스부터 종료
# Ollama → Media → SSE → Scheduler → Web API
# Media는 외부 endpoint 미보유라 SSE보다 먼저 stop.
STOP_ORDER = ("ollama", "media", "sse", "scheduler", "web-api")

M2_STARTUP_SEQUENCE = (
    "resolve_runtime_paths",
    "ensure_internal_token_file",
    "prepare_shared_service_environment",
    "start_front_router_listener",
    "start_web_api_service",
    "start_sse_service",
    "start_scheduler_service",   # M3-2: Scheduler service (5번째, SSE 다음)
    "start_ollama_service",      # M4-1: Ollama service (6번째, Scheduler 다음)
    "start_media_service",       # M5-2: Media service (7번째, Ollama 다음)
    "verify_health_and_publish_status",
)


def web_api_internal_service_env(
    router_host: str = FRONT_ROUTER_LOOPBACK_HOST,
    ollama_port: int = OLLAMA_SERVICE_DEFAULT_PORT,
    media_port: int = MEDIA_SERVICE_DEFAULT_PORT,
) -> dict[str, str]:
    return {
        TRUSTED_PROXY_ENV: router_host,
        WEB_API_BIND_HOST_ENV: FRONT_ROUTER_LOOPBACK_HOST,
        WEB_API_INTERNAL_ONLY_ENV: "1",
        # M3-2: supervisor spawn 경로에서는 Scheduler service가 반드시 별도 프로세스로 동작.
        # Web API lifespan이 APScheduler를 시작하지 않도록 분기 신호 주입.
        SCHEDULER_SERVICE_ENABLE_ENV: "1",
        # M4-1: Ollama service URL — web-api가 외부 Ollama 직접 호출 대신 IPC 위임.
        OLLAMA_SERVICE_URL_ENV: f"http://127.0.0.1:{ollama_port}/internal/llm",
        # M5-2: Media service URL — web-api가 파일 처리 위임.
        MEDIA_SERVICE_URL_ENV: f"http://127.0.0.1:{media_port}/internal/process",
    }


@dataclass(frozen=True)
class InternalTokenInfo:
    path: str
    created: bool
    acl_applied: bool
    acl_warning: str = ""


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: Sequence[str]
    env: Mapping[str, str] = field(default_factory=dict)
    startup_grace_seconds: float = 1.0


def sse_service_spec(
    command: Sequence[str],
    *,
    name: str = SSE_SERVICE_NAME,
    port: int = SSE_SERVICE_DEFAULT_PORT,
    extra_env: Mapping[str, str] | None = None,
    startup_grace_seconds: float = 1.0,
) -> ServiceSpec:
    """SSE service 프로세스 spec 팩토리.

    보호 env:
    - WHATUDOIN_BIND_HOST: 항상 127.0.0.1 (loopback bind 강제)
    - WHATUDOIN_SSE_SERVICE_PORT: 지정 포트 강제

    향후 M2-17에서 WHATUDOIN_INTERNAL_TOKEN 주입 추가 예정.
    """
    protected = {
        SSE_SERVICE_BIND_HOST_ENV,
        SSE_SERVICE_PORT_ENV,
        INTERNAL_TOKEN_ENV,  # supervisor가 직접 주입 — extra_env override 차단
    }
    env = {
        str(k): str(v)
        for k, v in (extra_env or {}).items()
        if str(k) not in protected
    }
    env[SSE_SERVICE_BIND_HOST_ENV] = "127.0.0.1"
    env[SSE_SERVICE_PORT_ENV] = str(port)
    return ServiceSpec(
        name=name,
        command=command,
        env=env,
        startup_grace_seconds=startup_grace_seconds,
    )


def scheduler_service_spec(
    command: Sequence[str],
    *,
    name: str = SCHEDULER_SERVICE_NAME,
    port: int = SCHEDULER_SERVICE_DEFAULT_PORT,
    extra_env: Mapping[str, str] | None = None,
    startup_grace_seconds: float = 1.0,
) -> ServiceSpec:
    """Scheduler service 프로세스 spec 팩토리.

    보호 env (extra_env override 차단):
    - WHATUDOIN_SCHEDULER_BIND_HOST: 항상 127.0.0.1 (loopback bind 강제)
    - WHATUDOIN_SCHEDULER_PORT: 지정 포트 강제
    - WHATUDOIN_SCHEDULER_SERVICE: 항상 1 (scheduler 단독 모드 강제)
    - WHATUDOIN_INTERNAL_TOKEN: supervisor가 직접 주입
    """
    protected = {
        SCHEDULER_SERVICE_BIND_HOST_ENV,
        SCHEDULER_SERVICE_PORT_ENV,
        SCHEDULER_SERVICE_ENABLE_ENV,
        INTERNAL_TOKEN_ENV,  # supervisor가 직접 주입 — extra_env override 차단
    }
    env = {
        str(k): str(v)
        for k, v in (extra_env or {}).items()
        if str(k) not in protected
    }
    env[SCHEDULER_SERVICE_BIND_HOST_ENV] = "127.0.0.1"
    env[SCHEDULER_SERVICE_PORT_ENV] = str(port)
    env[SCHEDULER_SERVICE_ENABLE_ENV] = "1"
    return ServiceSpec(
        name=name,
        command=command,
        env=env,
        startup_grace_seconds=startup_grace_seconds,
    )


def ollama_service_spec(
    command: Sequence[str],
    *,
    name: str = OLLAMA_SERVICE_NAME,
    port: int = OLLAMA_SERVICE_DEFAULT_PORT,
    extra_env: Mapping[str, str] | None = None,
    startup_grace_seconds: float = 1.0,
) -> ServiceSpec:
    """Ollama service 프로세스 spec 팩토리.

    보호 env (extra_env override 차단):
    - WHATUDOIN_OLLAMA_BIND_HOST: 항상 127.0.0.1 (loopback bind 강제)
    - WHATUDOIN_OLLAMA_PORT: 지정 포트 강제
    - WHATUDOIN_INTERNAL_TOKEN: supervisor가 직접 주입
    """
    protected = {
        OLLAMA_SERVICE_BIND_HOST_ENV,
        OLLAMA_SERVICE_PORT_ENV,
        INTERNAL_TOKEN_ENV,  # supervisor가 직접 주입 — extra_env override 차단
    }
    env = {
        str(k): str(v)
        for k, v in (extra_env or {}).items()
        if str(k) not in protected
    }
    env[OLLAMA_SERVICE_BIND_HOST_ENV] = "127.0.0.1"
    env[OLLAMA_SERVICE_PORT_ENV] = str(port)
    return ServiceSpec(
        name=name,
        command=command,
        env=env,
        startup_grace_seconds=startup_grace_seconds,
    )


def media_service_spec(
    command: Sequence[str],
    *,
    name: str = MEDIA_SERVICE_NAME,
    port: int = MEDIA_SERVICE_DEFAULT_PORT,
    staging_root: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    startup_grace_seconds: float = 1.0,
) -> ServiceSpec:
    """Media service 프로세스 spec 팩토리.

    보호 env (extra_env override 차단):
    - WHATUDOIN_MEDIA_BIND_HOST: 항상 127.0.0.1 (loopback bind 강제)
    - WHATUDOIN_MEDIA_PORT: 지정 포트 강제
    - WHATUDOIN_INTERNAL_TOKEN: supervisor가 직접 주입
    - WHATUDOIN_STAGING_ROOT: supervisor가 결정한 staging 루트 강제

    WHATUDOIN_MEDIA_SERVICE_URL은 web-api 측 env이므로 이 spec에 포함하지 않는다.
    """
    protected = {
        MEDIA_SERVICE_BIND_HOST_ENV,
        MEDIA_SERVICE_PORT_ENV,
        INTERNAL_TOKEN_ENV,  # supervisor가 직접 주입 — extra_env override 차단
        MEDIA_SERVICE_STAGING_ROOT_ENV,  # supervisor가 결정 — 사용자 override 차단
    }
    env = {
        str(k): str(v)
        for k, v in (extra_env or {}).items()
        if str(k) not in protected
    }
    env[MEDIA_SERVICE_BIND_HOST_ENV] = "127.0.0.1"
    env[MEDIA_SERVICE_PORT_ENV] = str(port)
    if staging_root is not None:
        env[MEDIA_SERVICE_STAGING_ROOT_ENV] = staging_root
    return ServiceSpec(
        name=name,
        command=command,
        env=env,
        startup_grace_seconds=startup_grace_seconds,
    )


def web_api_service_spec(
    command: Sequence[str],
    *,
    name: str = WEB_API_SERVICE_NAME,
    router_host: str = FRONT_ROUTER_LOOPBACK_HOST,
    extra_env: Mapping[str, str] | None = None,
    startup_grace_seconds: float = 1.0,
) -> ServiceSpec:
    protected = {
        TRUSTED_PROXY_ENV,
        WEB_API_BIND_HOST_ENV,
        WEB_API_INTERNAL_ONLY_ENV,
        INTERNAL_TOKEN_ENV,  # supervisor가 직접 주입 — extra_env override 차단
    }
    env = {
        str(k): str(v)
        for k, v in (extra_env or {}).items()
        if str(k) not in protected
    }
    env.update(web_api_internal_service_env(router_host))
    return ServiceSpec(
        name=name,
        command=command,
        env=env,
        startup_grace_seconds=startup_grace_seconds,
    )


@dataclass
class ServiceState:
    name: str
    status: str = "stopped"
    pid: int | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    restart_count: int = 0
    startup_failures: int = 0
    runtime_crashes: int = 0
    last_error: str = ""
    stdout_log: str = ""
    stderr_log: str = ""
    crash_history: list = field(default_factory=list)
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _startup_confirmed: bool = False
    _exit_counted: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "pid": self.pid,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "restart_count": self.restart_count,
            "startup_failures": self.startup_failures,
            "runtime_crashes": self.runtime_crashes,
            "last_error": self.last_error,
            "stdout_log": self.stdout_log,
            "stderr_log": self.stderr_log,
        }


class WhatUdoinSupervisor:
    """Minimal process supervisor skeleton for M2 service split work.

    This module intentionally does not implement Front Router or SSE service
    routing yet. M2-9 only establishes the lifecycle primitives that those
    later steps use.
    """

    def __init__(self, run_dir: Path | str | None = None):
        self.run_dir = Path(run_dir) if run_dir else _RUN_DIR
        self.log_dir = self.run_dir / "logs" / "services"
        self.token_path = self.run_dir / INTERNAL_TOKEN_FILE_NAME
        self.services: dict[str, ServiceState] = {}
        self.token_info: InternalTokenInfo | None = None

    def ensure_runtime_dirs(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def ensure_internal_token(self) -> InternalTokenInfo:
        self.ensure_runtime_dirs()
        created = False
        if not self.token_path.exists():
            self.token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
            created = True
        token = self.token_path.read_text(encoding="utf-8").strip()
        if not token:
            token = secrets.token_urlsafe(32)
            self.token_path.write_text(token, encoding="utf-8")
            created = True
        acl_applied, acl_warning = _restrict_token_file(self.token_path)
        self.token_info = InternalTokenInfo(
            path=str(self.token_path),
            created=created,
            acl_applied=acl_applied,
            acl_warning=acl_warning,
        )
        return self.token_info

    def service_env(self, spec: ServiceSpec) -> dict[str, str]:
        token_info = self.ensure_internal_token()
        token = self.token_path.read_text(encoding="utf-8").strip()
        env = {
            **os.environ,
            INTERNAL_TOKEN_ENV: token,
            INTERNAL_TOKEN_FILE_ENV: token_info.path,
            SERVICE_NAME_ENV: spec.name,
        }
        env.update({str(k): str(v) for k, v in spec.env.items()})
        return env

    @staticmethod
    def _record_crash(state: "ServiceState") -> None:
        """현재 시각을 crash_history에 기록하고 5분 윈도우 외 항목을 prune."""
        now = time.time()
        state.crash_history.append(now)
        cutoff = now - CRASH_LOOP_WINDOW_SECONDS
        state.crash_history = [t for t in state.crash_history if t >= cutoff]

    @staticmethod
    def _is_crash_loop(state: "ServiceState") -> bool:
        """5분 윈도우 안 누적 실패 횟수가 임계값 이상이면 crash-loop."""
        now = time.time()
        cutoff = now - CRASH_LOOP_WINDOW_SECONDS
        recent = [t for t in state.crash_history if t >= cutoff]
        return len(recent) >= CRASH_LOOP_MAX_FAILURES

    def start_service(self, spec: ServiceSpec) -> ServiceState:
        self.ensure_runtime_dirs()
        state = self.services.get(spec.name)
        if state and state._process and state._process.poll() is None:
            return state
        if state is None:
            state = ServiceState(name=spec.name)
            self.services[spec.name] = state

        # crash-loop 차단 — 진입 시 먼저 확인 (restart_count 증가 전)
        if self._is_crash_loop(state):
            state.status = "degraded"
            state.last_error = "crash-loop blocked"
            return state

        if state.status in {"running", "crashed", "failed_startup", "stopped"}:
            state.restart_count += 1
            state._exit_counted = False
            state._startup_confirmed = False

        stdout_path = self.log_dir / f"{spec.name}.stdout.log"
        stderr_path = self.log_dir / f"{spec.name}.stderr.log"
        state.stdout_log = str(stdout_path)
        state.stderr_log = str(stderr_path)
        state.started_at = time.time()
        state.stopped_at = None
        state.last_error = ""
        try:
            stdout = open(stdout_path, "a", encoding="utf-8", errors="replace")
            stderr = open(stderr_path, "a", encoding="utf-8", errors="replace")
            try:
                proc = subprocess.Popen(
                    list(spec.command),
                    cwd=str(Path(__file__).parent),
                    env=self.service_env(spec),
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                )
            finally:
                stdout.close()
                stderr.close()
        except Exception as exc:
            state.status = "failed_startup"
            state.startup_failures += 1
            state.last_error = f"spawn failed: {exc}"
            state.pid = None
            self._record_crash(state)
            if self._is_crash_loop(state):
                state.status = "degraded"
                state.last_error = "crash-loop blocked"
            return state

        state._process = proc
        state.pid = proc.pid
        state.status = "starting"
        time.sleep(max(0.0, spec.startup_grace_seconds))
        if proc.poll() is not None:
            state.status = "failed_startup"
            state.startup_failures += 1
            state.last_error = f"startup exited with code {proc.returncode}"
            state.stopped_at = time.time()
            state._exit_counted = True
            self._record_crash(state)
            if self._is_crash_loop(state):
                state.status = "degraded"
                state.last_error = "crash-loop blocked"
            return state
        state.status = "running"
        state._startup_confirmed = True
        return state

    def poll_service(self, name: str) -> ServiceState | None:
        state = self.services.get(name)
        if not state or not state._process:
            return state
        returncode = state._process.poll()
        if returncode is None:
            if state.status == "starting":
                state.status = "running"
                state._startup_confirmed = True
            return state
        if not state._exit_counted:
            state.stopped_at = time.time()
            if state._startup_confirmed:
                state.runtime_crashes += 1
                state.status = "crashed"
                state.last_error = f"runtime exited with code {returncode}"
            else:
                state.startup_failures += 1
                state.status = "failed_startup"
                state.last_error = f"startup exited with code {returncode}"
            state._exit_counted = True
            self._record_crash(state)
            if self._is_crash_loop(state):
                state.status = "degraded"
        return state

    def reset_crash_loop(self, name: str) -> ServiceState | None:
        """crash-loop 이력을 초기화하고 status를 stopped으로 재설정.

        다음 start_service 호출 시 정상 spawn 진행.
        """
        state = self.services.get(name)
        if not state:
            return None
        state.crash_history = []
        state.status = "stopped"
        state.last_error = ""
        return state

    def probe_healthz(self, state: "ServiceState", url: str) -> dict:
        """GET <url>/healthz 호출 후 상태 확인.

        반환: {"ok": bool, "status": str|None, "error": str}
        - ok=True: HTTP 200 + JSON {"status": "ok"}
        - ok=False: 연결 실패, timeout, 비정상 응답
        stdlib urllib만 사용 (외부 의존성 없음).
        """
        endpoint = url.rstrip("/") + "/healthz"
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status != 200:
                    return {"ok": False, "status": None, "error": f"http {resp.status}"}
                body = resp.read(4096)
                data = __import__("json").loads(body)
                svc_status = data.get("status")
                return {"ok": svc_status == "ok", "status": svc_status, "error": ""}
        except Exception as exc:
            return {"ok": False, "status": None, "error": str(exc)}

    def poll_all(self) -> dict[str, ServiceState]:
        for name in list(self.services):
            self.poll_service(name)
        return self.services

    def stop_service(self, name: str, timeout: float = 5.0) -> ServiceState | None:
        state = self.services.get(name)
        if not state or not state._process:
            return state
        proc = state._process
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
        state.status = "stopped"
        state.stopped_at = time.time()
        state.pid = None
        state._exit_counted = True
        return state

    def stop_all(self, timeout: float = 5.0) -> None:
        # STOP_ORDER 기준으로 순서대로 종료, grace 0.5s 확보
        stopped: set[str] = set()
        for name in STOP_ORDER:
            if name in self.services:
                self.stop_service(name, timeout=timeout)
                stopped.add(name)
                time.sleep(0.5)
        # STOP_ORDER에 없는 서비스는 나머지 처리
        for name in list(self.services):
            if name not in stopped:
                self.stop_service(name, timeout=timeout)

    def snapshot(self) -> dict:
        self.poll_all()
        return {
            "startup_sequence": list(M2_STARTUP_SEQUENCE),
            "run_dir": str(self.run_dir),
            "internal_token": self.token_info.__dict__ if self.token_info else None,
            "services": {name: state.to_dict() for name, state in self.services.items()},
        }


def _restrict_token_file(path: Path) -> tuple[bool, str]:
    try:
        if os.name != "nt":
            path.chmod(0o600)
            return True, ""
        whoami = subprocess.run(
            ["whoami"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        account = (whoami.stdout or "").strip() or os.environ.get("USERNAME", "")
        if not account:
            return False, "could not resolve current Windows account"
        commands = [
            ["icacls", str(path), "/inheritance:r"],
            ["icacls", str(path), "/grant:r", f"{account}:F"],
            ["icacls", str(path), "/remove:g", "*S-1-1-0", "*S-1-5-11", "*S-1-5-32-545"],
        ]
        warnings: list[str] = []
        ok = True
        for cmd in commands:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode != 0:
                ok = False
                warnings.append((result.stderr or result.stdout or "icacls failed").strip())
        return ok, " | ".join(warnings)
    except Exception as exc:
        return False, str(exc)


__all__ = [
    "CRASH_LOOP_MAX_FAILURES",
    "CRASH_LOOP_WINDOW_SECONDS",
    "INTERNAL_TOKEN_ENV",
    "STOP_ORDER",
    "INTERNAL_TOKEN_FILE_ENV",
    "M2_STARTUP_SEQUENCE",
    "SCHEDULER_SERVICE_BIND_HOST_ENV",
    "SCHEDULER_SERVICE_DEFAULT_PORT",
    "SCHEDULER_SERVICE_ENABLE_ENV",
    "SCHEDULER_SERVICE_NAME",
    "SCHEDULER_SERVICE_PORT_ENV",
    "SERVICE_NAME_ENV",
    "SSE_SERVICE_BIND_HOST_ENV",
    "SSE_SERVICE_DEFAULT_PORT",
    "SSE_SERVICE_NAME",
    "SSE_SERVICE_PORT_ENV",
    "SSE_SERVICE_PUBLISH_URL_ENV",
    "SSE_SERVICE_URL_ENV",
    "TRUSTED_PROXY_ENV",
    "WEB_API_BIND_HOST_ENV",
    "WEB_API_INTERNAL_ONLY_ENV",
    "WEB_API_SERVICE_NAME",
    "InternalTokenInfo",
    "ServiceSpec",
    "ServiceState",
    "WhatUdoinSupervisor",
    "ollama_service_spec",
    "scheduler_service_spec",
    "sse_service_spec",
    "web_api_internal_service_env",
    "web_api_service_spec",
    "OLLAMA_SERVICE_BIND_HOST_ENV",
    "OLLAMA_SERVICE_DEFAULT_PORT",
    "OLLAMA_SERVICE_NAME",
    "OLLAMA_SERVICE_PORT_ENV",
    "OLLAMA_SERVICE_URL_ENV",
    # M5-2: Media service
    "MEDIA_SERVICE_BIND_HOST_ENV",
    "MEDIA_SERVICE_DEFAULT_PORT",
    "MEDIA_SERVICE_NAME",
    "MEDIA_SERVICE_PORT_ENV",
    "MEDIA_SERVICE_STAGING_ROOT_ENV",
    "MEDIA_SERVICE_URL_ENV",
    "media_service_spec",
]
