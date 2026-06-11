#!/usr/bin/env python3
"""CLI aliases and shortcuts for AI Orchestrator.

Provides shell aliases and wrapper scripts for common commands.
Install with: python -m orchestrator.cli.aliases install

Author: Hermes Agent
Date: 2026-06-10
Phase: 5 (UI/CLI Unification)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_shell_type() -> str:
    """Detect current shell type."""
    shell = os.environ.get('SHELL', '')
    if 'bash' in shell:
        return 'bash'
    if 'zsh' in shell:
        return 'zsh'
    if os.environ.get('COMSPEC', '').endswith('cmd.exe'):
        return 'cmd'
    if os.environ.get('PSMODULEPATH'):
        return 'powershell'
    return 'unknown'


def generate_bash_aliases() -> str:
    """Generate bash/zsh aliases."""
    return """
# AI Orchestrator aliases
alias orch='python -m orchestrator.cli.main'
alias orch-refresh='python -m orchestrator.cli.main refresh'
alias orch-status='python -m orchestrator.cli.main status'
alias orch-dispatch='python -m orchestrator.cli.main dispatch'
alias orch-workers='python -m orchestrator.cli.main workers'
alias orch-budget='python -m orchestrator.cli.main budget'
alias orch-receipts='python -m orchestrator.cli.main receipts'
alias orch-route='python -m orchestrator.cli.main route'
alias orch-refresh-budget='python -m orchestrator.cli.main refresh-budget'
""".strip()


def generate_powershell_aliases() -> str:
    """Generate PowerShell aliases/functions."""
    return """
# AI Orchestrator aliases
function orch { python -m orchestrator.cli.main @args }
function orch-Refresh { python -m orchestrator.cli.main refresh }
function orch-Status { python -m orchestrator.cli.main status }
function orch-Dispatch { param([string]$Text) python -m orchestrator.cli.main dispatch $Text }
function orch-Workers { param([int]$Limit = 20) python -m orchestrator.cli.main workers --limit $Limit }
function orch-Budget { python -m orchestrator.cli.main budget }
function orch-Receipts { param([int]$Last = 20) python -m orchestrator.cli.main receipts --last $Last }
function orch-Route { param([string]$JobClass, [string]$Text) python -m orchestrator.cli.main route --job-class $JobClass --text $Text }
function orch-RefreshBudget { python -m orchestrator.cli.main refresh-budget }
""".strip()


def install_aliases() -> None:
    """Install aliases to appropriate shell config file."""
    shell = get_shell_type()
    home = Path.home()
    
    if shell in ('bash', 'zsh'):
        if shell == 'bash':
            config_file = home / '.bashrc'
        else:
            config_file = home / '.zshrc'
        
        aliases = generate_bash_aliases()
        
        # Check if already installed
        if config_file.exists():
            content = config_file.read_text()
            if 'AI Orchestrator aliases' in content:
                print(f"Aliases already installed in {config_file}")
                return
        
        # Append aliases
        with open(config_file, 'a') as f:
            f.write(f"\n{aliases}\n")
        
        print(f"✅ Aliases installed to {config_file}")
        print(f"   Run 'source {config_file}' or restart your shell to use them")
    
    elif shell == 'powershell':
        # PowerShell profile
        ps_profile = Path(os.environ.get('PSModulePath', '').split(';')[0]).parent / 'Microsoft.PowerShell_profile.ps1'
        
        # Try common profile locations
        profile_candidates = [
            Path.home() / 'Documents/PowerShell/Microsoft.PowerShell_profile.ps1',
            Path.home() / 'Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1',
        ]
        
        profile_file = None
        for candidate in profile_candidates:
            if candidate.exists() or candidate.parent.exists():
                profile_file = candidate
                break
        
        if not profile_file:
            profile_file = profile_candidates[0]
            profile_file.parent.mkdir(parents=True, exist_ok=True)
        
        aliases = generate_powershell_aliases()
        
        # Check if already installed
        if profile_file.exists():
            content = profile_file.read_text()
            if 'AI Orchestrator aliases' in content:
                print(f"Aliases already installed in {profile_file}")
                return
        
        # Append aliases
        with open(profile_file, 'a') as f:
            f.write(f"\n{aliases}\n")
        
        print(f"✅ Aliases installed to {profile_file}")
        print(f"   Restart PowerShell or run '. {profile_file}' to use them")
    
    else:
        print(f"⚠ Unsupported shell: {shell}")
        print("Manual installation:")
        print(generate_bash_aliases())


def show_aliases() -> None:
    """Print aliases for current shell."""
    shell = get_shell_type()
    
    if shell in ('bash', 'zsh'):
        print(generate_bash_aliases())
    elif shell == 'powershell':
        print(generate_powershell_aliases())
    else:
        print("# Unknown shell, showing bash aliases:")
        print(generate_bash_aliases())


def main() -> None:
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.cli.aliases [install|show]")
        print("  install - Install aliases to shell config")
        print("  show    - Show aliases for current shell")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == 'install':
        install_aliases()
    elif command == 'show':
        show_aliases()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == '__main__':
    main()
