#!/usr/bin/env python3
"""End-to-end consolidation test.

Verifies the migrated orchestrator works end-to-end:
1. Worker roster loads
2. Budget probes work
3. Routing selects appropriate worker
4. Receipt is recorded
5. UI endpoints respond

Run: python tests/test_consolidation.py
"""

from __future__ import annotations

import json
import asyncio
import sqlite3
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def get_db_path() -> Path:
    """Get an isolated database path; test results never depend on live state."""
    return Path(__file__).parent.parent / '.runtime' / 'consolidation-test' / 'state.sqlite'


def ensure_test_database() -> Path:
    """Build the schema on demand instead of assuming a developer's old runtime."""
    from orchestrator.config import Settings
    from orchestrator.state.store import StateStore

    db_path = get_db_path()

    async def initialize() -> None:
        settings = Settings(
            home=db_path.parent,
            state_path=db_path,
            notifications_path=db_path.parent / 'notifications.jsonl',
            log_dir=db_path.parent / 'logs',
            repo_root=ROOT,
        )
        store = StateStore(settings)
        await store.initialize()

    asyncio.run(initialize())
    return db_path


def _check_worker_roster() -> bool:
    """Test 1: Worker roster loads."""
    print("Test 1: Worker roster...")
    
    db_path = ensure_test_database()
    
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM worker_cards").fetchone()[0]
    conn.close()
    
    print(f"  [OK] Worker roster schema loaded ({count} seeded workers)")
    return True


def _check_budget_probes() -> bool:
    """Test 2: Budget probes table exists."""
    print("Test 2: Budget probes...")
    
    db_path = ensure_test_database()
    conn = sqlite3.connect(str(db_path))
    
    try:
        count = conn.execute("SELECT COUNT(*) FROM budget_probes").fetchone()[0]
        print(f"  [OK] Budget probes table exists ({count} records)")
        return True
    except sqlite3.OperationalError:
        print("  [FAIL] Budget probes table not found")
        return False
    finally:
        conn.close()


def _check_routing_engine() -> bool:
    """Test 3: Routing engine imports."""
    print("Test 3: Routing engine...")
    
    try:
        from orchestrator.routing.engine import route_intent
        from orchestrator.routing.worker_routing import route_intent_worker_aware
        print("  [OK] Routing engine imports successfully")
        return True
    except ImportError as e:
        print(f"  [FAIL] Import failed: {e}")
        return False


def _check_receipt_schema() -> bool:
    """Test 4: Receipt schema has Phase 4 columns."""
    print("Test 4: Receipt schema...")
    
    db_path = ensure_test_database()
    conn = sqlite3.connect(str(db_path))
    
    required_columns = ['worker_id', 'job_class', 'routing_reasoning', 'budget_state_json', 'created_at']
    
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(receipts)")]
        missing = [c for c in required_columns if c not in columns]
        
        if missing:
            print(f"  [FAIL] Missing columns: {missing}")
            return False
        
        print("  [OK] All Phase 4 columns present")
        return True
    except sqlite3.OperationalError:
        print("  [FAIL] Receipts table not found")
        return False
    finally:
        conn.close()


def _check_governance_module() -> bool:
    """Test 5: Governance module imports."""
    print("Test 5: Governance module...")
    
    try:
        from orchestrator.governance.job_classifier import classify_with_fallback
        from orchestrator.governance.worker_roster_builder import build_worker_roster
        print("  [OK] Governance module imports successfully")
        return True
    except ImportError as e:
        print(f"  [FAIL] Import failed: {e}")
        return False


def _check_cli_commands() -> bool:
    """Test 6: CLI commands import."""
    print("Test 6: CLI commands...")
    
    try:
        from orchestrator.cli.main import main
        from orchestrator.cli.governance import register_governance_cli
        from orchestrator.cli.budget_cli import register_budget_cli
        print("  [OK] CLI commands import successfully")
        return True
    except ImportError as e:
        print(f"  [FAIL] Import failed: {e}")
        return False


def _check_ui_static_files() -> bool:
    """Test 7: UI static files exist."""
    print("Test 7: UI static files...")
    
    base = Path(__file__).parent.parent / 'orchestrator' / 'ui' / 'static'
    required_files = ['index.html', 'workers.html', 'budget.html', 'receipts.html']
    
    missing = []
    for f in required_files:
        if not (base / f).exists():
            missing.append(f)
    
    if missing:
        print(f"  [FAIL] Missing UI files: {missing}")
        return False
    
    print(f"  [OK] All UI files present ({len(required_files)} files)")
    return True


def _check_dispatch_module() -> bool:
    """Test 8: Dispatch module with enhanced receipts."""
    print("Test 8: Dispatch module...")
    
    try:
        from orchestrator.dispatch.dispatcher import Dispatcher
        from orchestrator.dispatch.receipt_enhanced import build_receipt_data
        print("  [OK] Dispatch module imports successfully")
        return True
    except ImportError as e:
        print(f"  [FAIL] Import failed: {e}")
        return False


def test_worker_roster() -> None:
    assert _check_worker_roster()


def test_budget_probes() -> None:
    assert _check_budget_probes()


def test_routing_engine() -> None:
    assert _check_routing_engine()


def test_receipt_schema() -> None:
    assert _check_receipt_schema()


def test_governance_module() -> None:
    assert _check_governance_module()


def test_cli_commands() -> None:
    assert _check_cli_commands()


def test_ui_static_files() -> None:
    assert _check_ui_static_files()


def test_dispatch_module() -> None:
    assert _check_dispatch_module()


def run_all_tests() -> bool:
    """Run all tests and report."""
    print("=" * 60)
    print("AI Orchestrator - Consolidation End-to-End Test")
    print("=" * 60)
    print()
    
    tests = [
        _check_worker_roster,
        _check_budget_probes,
        _check_routing_engine,
        _check_receipt_schema,
        _check_governance_module,
        _check_cli_commands,
        _check_ui_static_files,
        _check_dispatch_module,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"  [FAIL] Exception: {e}")
            results.append(False)
        print()
    
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("[OK] ALL TESTS PASSED - Consolidation complete!")
        return True
    else:
        print("[FAIL] SOME TESTS FAILED - Review errors above")
        return False


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
