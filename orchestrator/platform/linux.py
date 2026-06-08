from __future__ import annotations


class LinuxPlatformAdapter:
    def __getattribute__(self, name: str):
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise NotImplementedError("Linux platform adapter is a stable v1 stub.")

