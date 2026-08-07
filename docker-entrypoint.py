#!/usr/bin/env python3
"""Prepare the persistent data directory, then drop root privileges."""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path


APP_USER = "habits"
DEFAULT_DB_PATH = "/app/data/habits.db"


def chown_tree(path: Path, uid: int, gid: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid, follow_symlinks=False)
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            os.chown(Path(root) / name, uid, gid, follow_symlinks=False)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("No application command provided")

    account = pwd.getpwnam(APP_USER)
    data_dir = Path(os.environ.get("DB_PATH", DEFAULT_DB_PATH)).parent
    chown_tree(data_dir, account.pw_uid, account.pw_gid)

    os.initgroups(APP_USER, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
