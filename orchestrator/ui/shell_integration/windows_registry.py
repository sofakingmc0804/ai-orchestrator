from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


MENU_NAME = "AIOrchestratorQueue"
FILE_MENU_PATH = rf"Software\Classes\*\shell\{MENU_NAME}"
FOLDER_MENU_PATH = rf"Software\Classes\Directory\shell\{MENU_NAME}"


def _require_windows() -> Any:
    if os.name != "nt":
        raise RuntimeError("Windows shell integration is only available on Windows.")
    import winreg

    return winreg


def _command(repo_root: Path, kind: str) -> str:
    launcher = repo_root / "scripts" / "queue-selection.ps1"
    return (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{launcher}" '
        f'-SelectedPath "%1" -Kind {kind}'
    )


def _write_menu(winreg: Any, key_path: str, label: str, command: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, label)
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(Path(sys.executable)))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\command") as cmd_key:
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)


def install(repo_root: Path | None = None) -> dict[str, str]:
    winreg = _require_windows()
    root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
    _write_menu(winreg, FILE_MENU_PATH, "Queue in AI Orchestrator", _command(root, "file"))
    _write_menu(winreg, FOLDER_MENU_PATH, "Queue in AI Orchestrator", _command(root, "folder"))
    return {"file_menu": FILE_MENU_PATH, "folder_menu": FOLDER_MENU_PATH, "repo_root": str(root)}


def _delete_tree(winreg: Any, root: Any, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(winreg, key, child)
    except FileNotFoundError:
        return
    winreg.DeleteKey(root, path)


def uninstall() -> dict[str, str]:
    winreg = _require_windows()
    _delete_tree(winreg, winreg.HKEY_CURRENT_USER, FILE_MENU_PATH)
    _delete_tree(winreg, winreg.HKEY_CURRENT_USER, FOLDER_MENU_PATH)
    return {"removed_file_menu": FILE_MENU_PATH, "removed_folder_menu": FOLDER_MENU_PATH}


def main() -> None:
    parser = argparse.ArgumentParser(prog="orchestrator-windows-shell")
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args()
    if args.action == "install":
        result = install(Path(args.repo_root) if args.repo_root else None)
    else:
        result = uninstall()
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
