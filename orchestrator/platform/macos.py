from __future__ import annotations


class MacPlatformAdapter:
    def __getattribute__(self, name: str):
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise NotImplementedError("macOS platform adapter is a stable v1 stub.")

