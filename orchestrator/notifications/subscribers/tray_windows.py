from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any


class WindowsTraySubscriber:
    name = "tray_windows"
    channel = "tray"

    async def deliver(self, notification: dict[str, Any]) -> dict[str, Any]:
        if os.name != "nt":
            return {"ok": False, "notification_id": notification.get("id"), "error": "Windows tray subscriber only runs on Windows."}
        if not shutil.which("powershell.exe"):
            return {"ok": False, "notification_id": notification.get("id"), "error": "powershell.exe not found."}
        title = str(notification.get("title") or "AI Orchestrator")[:120]
        body = str(notification.get("body") or "")[:240]
        script = (
            "& { param($ToastTitle, $ToastBody);"
            "$ErrorActionPreference='Stop';"
            "if(Get-Command New-BurntToastNotification -ErrorAction SilentlyContinue){"
            "New-BurntToastNotification -Text @($ToastTitle,$ToastBody) | Out-Null;"
            "Write-Output 'burnttoast'"
            "} else { Write-Output 'toast_module_missing'; exit 3 }"
            "}"
        )
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
            title,
            body,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode == 0:
            return {"ok": True, "notification_id": notification.get("id"), "provider": out or "windows_toast"}
        return {
            "ok": False,
            "notification_id": notification.get("id"),
            "error": err or out or "Windows toast provider unavailable.",
            "repair_action": "Install a Windows toast provider such as BurntToast or wire pywin32 shell COM.",
        }
