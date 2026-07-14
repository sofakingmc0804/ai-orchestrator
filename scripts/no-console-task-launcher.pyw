from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    args = sys.argv[1:]
    if "--" in args:
        args = args[args.index("--") + 1 :]
    if not args:
        return 64

    env = os.environ.copy()
    for name in ("SSLKEYLOGFILE",):
        env.pop(name, None)

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(os.devnull, "rb") as stdin, open(os.devnull, "wb") as stdout, open(os.devnull, "wb") as stderr:
        return subprocess.call(args, stdin=stdin, stdout=stdout, stderr=stderr, creationflags=creationflags, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
