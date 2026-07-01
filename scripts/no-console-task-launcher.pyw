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

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(os.devnull, "rb") as stdin, open(os.devnull, "wb") as stdout, open(os.devnull, "wb") as stderr:
        return subprocess.call(args, stdin=stdin, stdout=stdout, stderr=stderr, creationflags=creationflags)


if __name__ == "__main__":
    raise SystemExit(main())
