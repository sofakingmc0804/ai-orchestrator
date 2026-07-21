from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from typing import Sequence


def run_hermes_command(executable: str, argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Invoke Hermes with a true argv array so Windows does not strip embedded quotes."""
    return subprocess.run([executable, *argv], capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(prog="governed-hermes-exec")
    parser.add_argument("--executable", required=True)
    parser.add_argument("--argv-b64", required=True)
    args = parser.parse_args()
    decoded = base64.b64decode(args.argv_b64.encode("ascii")).decode("utf-8")
    parsed = json.loads(decoded)
    if not isinstance(parsed, list):
        raise SystemExit("--argv-b64 must decode to a JSON array")
    completed = run_hermes_command(str(args.executable), [str(value) for value in parsed])
    sys.stdout.buffer.write(completed.stdout)
    sys.stderr.buffer.write(completed.stderr)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
