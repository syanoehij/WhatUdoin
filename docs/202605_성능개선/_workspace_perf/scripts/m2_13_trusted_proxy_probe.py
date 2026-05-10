from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _run(args: list[str]) -> dict:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "args": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "passed": proc.returncode == 0,
    }


def _write_report(payload: dict) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "_workspace" / "perf" / "trusted_proxy_m2_13" / "runs" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "m2_13_trusted_proxy_probe.md"
    lines = [
        "# M2-13 Trusted Proxy Boundary Probe",
        "",
        f"- passed: {payload['passed']}",
        "",
        "| check | result |",
        "|---|---|",
    ]
    for item in payload["commands"]:
        label = " ".join(item["args"][1:])
        lines.append(f"| `{label}` | {'PASS' if item['passed'] else 'FAIL'} |")
    lines.extend(["", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""])
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    commands = [
        _run(
            [
                sys.executable,
                "-m",
                "py_compile",
                "app.py",
                "main.py",
                "supervisor.py",
                "tests/phase55_front_router_trust_boundary.py",
            ]
        ),
        _run([sys.executable, "tests/phase55_front_router_trust_boundary.py"]),
    ]
    payload = {"commands": commands, "passed": all(item["passed"] for item in commands)}
    report = _write_report(payload)
    print(f"report={report}")
    print(f"passed={payload['passed']}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
