from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    if args.mode == "startup_fail":
        return 7
    run_dir = Path(args.run_dir)
    token_file = os.environ.get("WHATUDOIN_INTERNAL_TOKEN_FILE", "")
    token_from_file = ""
    if token_file and Path(token_file).exists():
        token_from_file = Path(token_file).read_text(encoding="utf-8").strip()
    payload = {
        "mode": args.mode,
        "pid": os.getpid(),
        "service_name": os.environ.get("WHATUDOIN_SERVICE_NAME", ""),
        "token_env_present": bool(os.environ.get("WHATUDOIN_INTERNAL_TOKEN")),
        "token_file": token_file,
        "token_matches_file": os.environ.get("WHATUDOIN_INTERNAL_TOKEN", "") == token_from_file,
        "argv": sys.argv,
    }
    (run_dir / f"child_{args.mode}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.mode == "runtime_crash":
        time.sleep(1.2)
        return 9
    time.sleep(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
