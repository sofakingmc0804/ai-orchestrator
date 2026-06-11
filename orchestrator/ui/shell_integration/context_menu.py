# Windows Context Menu Integration for AI Orchestrator
# Install with: python -m orchestrator.ui.shell_integration.context_menu install
# Requires admin PowerShell

from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path


def get_orchestrator_path() -> str:
    """Get path to orchestrator CLI."""
    return f'python -m orchestrator.cli.main'


def add_context_menu() -> None:
    """Add context menu entries to Windows Registry."""
    
    # Registry paths
    background_key = r"Directory\Background\shell\AIOrchestrator"
    folder_key = r"Directory\shell\AIOrchestrator"
    
    commands = [
        (background_key, "Dispatch here", "dispatch \"{0}\""),
        (folder_key, "Dispatch in folder", "dispatch \"{0}\""),
    ]
    
    for base_key, menu_text, command_template in commands:
        try:
            # Create main key
            key_path = f"{base_key}"
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, menu_text)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, "powershell.exe")
            winreg.CloseKey(key)
            
            # Create command subkey
            command_key = f"{base_key}\\command"
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, command_key)
            
            # Build command
            orchestrator_cmd = get_orchestrator_path()
            full_command = f'powershell -NoExit -Command "cd \\"%V\\"; {orchestrator_cmd} dispatch \"\""'
            
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, full_command)
            winreg.CloseKey(key)
            
            print(f"✅ Added: {menu_text}")
            
        except Exception as e:
            print(f"❌ Failed to add {menu_text}: {e}")


def remove_context_menu() -> None:
    """Remove context menu entries from Windows Registry."""
    
    keys_to_remove = [
        r"Directory\Background\shell\AIOrchestrator",
        r"Directory\shell\AIOrchestrator",
    ]
    
    for key_path in keys_to_remove:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            print(f"✅ Removed: {key_path}")
        except FileNotFoundError:
            print(f"⚠ Not found: {key_path}")
        except Exception as e:
            print(f"❌ Failed to remove {key_path}: {e}")


def main() -> None:
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.ui.shell_integration.context_menu [install|uninstall]")
        print("  install   - Add context menu entries")
        print("  uninstall - Remove context menu entries")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == 'install':
        print("Installing AI Orchestrator context menu...")
        print("Note: This adds entries to HKCU (current user only, no admin required)")
        add_context_menu()
        print("\n✅ Installation complete!")
        print("   Right-click in any folder or desktop background to see 'Dispatch here'")
    
    elif command == 'uninstall':
        print("Removing AI Orchestrator context menu...")
        remove_context_menu()
        print("\n✅ Uninstallation complete!")
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == '__main__':
    main()
