"""Read-only toolchain checks; no installs, privilege changes or secret output."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys


def probe(name: str, command: list[str]) -> dict[str, str | bool]:
    if shutil.which(command[0]) is None:
        return {"name": name, "ok": False, "detail": f"{command[0]} is unavailable"}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        detail = (result.stdout or result.stderr).strip().splitlines()
        return {"name": name, "ok": result.returncode == 0,
                "detail": detail[0][:240] if detail else f"exit {result.returncode}"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "ok": False, "detail": type(exc).__name__}


def checks() -> list[dict[str, str | bool]]:
    results = [probe("Python 3.12", ["python3.12", "--version"]),
               probe("Node 26 LTS", ["node", "--version"]),
               probe("npm", ["npm", "--version"]),
               probe("Git", ["git", "--version"]),
               probe("Docker CLI", ["docker", "--version"]),
               probe("Compose", ["docker", "compose", "version"]),
               probe("Docker daemon access", ["docker", "info", "--format", "{{.ServerVersion}}"])]
    node = results[1]
    node["ok"] = bool(node["ok"] and str(node["detail"]).startswith("v26."))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable results")
    args = parser.parse_args()
    results = checks()
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(f"{'PASS' if result['ok'] else 'FAIL'} {result['name']}: {result['detail']}")
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
