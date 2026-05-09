from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


_RUN_DIR = Path(os.environ.get("WHATUDOIN_RUN_DIR", Path(__file__).parent))

INTERNAL_TOKEN_FILE_NAME = "internal_token"
INTERNAL_TOKEN_ENV = "WHATUDOIN_INTERNAL_TOKEN"
INTERNAL_TOKEN_FILE_ENV = "WHATUDOIN_INTERNAL_TOKEN_FILE"
SERVICE_NAME_ENV = "WHATUDOIN_SERVICE_NAME"

M2_STARTUP_SEQUENCE = (
    "resolve_runtime_paths",
    "ensure_internal_token_file",
    "prepare_shared_service_environment",
    "start_front_router_listener",
    "start_web_api_service",
    "start_sse_service",
    "verify_health_and_publish_status",
)


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

    def start_service(self, spec: ServiceSpec) -> ServiceState:
        self.ensure_runtime_dirs()
        state = self.services.get(spec.name)
        if state and state._process and state._process.poll() is None:
            return state
        if state is None:
            state = ServiceState(name=spec.name)
            self.services[spec.name] = state
        elif state.status in {"running", "crashed", "failed_startup", "stopped"}:
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
        return state

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
        for name in list(self.services):
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
    "INTERNAL_TOKEN_ENV",
    "INTERNAL_TOKEN_FILE_ENV",
    "M2_STARTUP_SEQUENCE",
    "SERVICE_NAME_ENV",
    "InternalTokenInfo",
    "ServiceSpec",
    "ServiceState",
    "WhatUdoinSupervisor",
]
