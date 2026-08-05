"""Subscription entitlement probes backed by each provider's real authority.

The budget page has two separate lanes:
- subscription entitlements: account/profile quota windows for signed-in tools
- API budgets: optional owner policy caps for metered API use

Most subscription products do not publish a literal token_limit. They publish
rolling quota windows, percentages, model buckets, or credit/request budgets.
This module stores those windows explicitly instead of forcing them into API
budget semantics.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from orchestrator.config import Settings
from orchestrator.hermes.claude_code import oauth_only_environment
from orchestrator.state.store import StateStore


DEFAULT_GEMINI_CLI_OAUTH_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"


@dataclass
class UsageWindow:
    label: str
    unit: str = "percent"
    used_percent: float | None = None
    remaining_percent: float | None = None
    reset_at: str | None = None
    limit_window_seconds: int | None = None
    current: float | int | None = None
    limit: float | int | None = None
    remaining: float | int | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "unit": self.unit,
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "reset_at": self.reset_at,
            "limit_window_seconds": self.limit_window_seconds,
            "current": self.current,
            "limit": self.limit,
            "remaining": self.remaining,
            "source": self.source,
        }


@dataclass
class SubscriptionSnapshot:
    service_id: str
    account_id: str
    profile_id: str
    subscription_name: str
    plan_name: str | None
    source_type: str
    source_command: str | None
    tokens_limit: int | None
    tokens_used_total: int | None
    tokens_remaining: int | None
    tokens_used_by_app: int
    tokens_used_elsewhere: int | None
    reset_at: str | None
    checked_at: str
    ok: bool
    confidence: str
    status: str
    error: str | None
    raw: dict[str, Any]
    usage_windows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SubscriptionSource:
    service_id: str
    subscription_name: str
    executable: str
    command_candidates: tuple[tuple[str, ...], ...]
    env_command: str | None = None
    account_env: str | None = None
    profile_env: str | None = None
    provider_aliases: tuple[str, ...] = ()
    extra_env: tuple[tuple[str, str], ...] = ()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_sources() -> list[SubscriptionSource]:
    desktop_hermes_home = str(Path.home() / "AppData" / "Local" / "hermes")
    return [
        SubscriptionSource(
            service_id="openai_chatgpt",
            subscription_name="OpenAI ChatGPT / Codex",
            executable="codex",
            env_command="ORCHESTRATOR_OPENAI_SUBSCRIPTION_USAGE_CMD",
            account_env="OPENAI_ACCOUNT_EMAIL",
            profile_env="CODEX_HOME",
            provider_aliases=("openai", "codex", "chatgpt", "openai-api"),
            command_candidates=(("login", "status"),),
        ),
        SubscriptionSource(
            service_id="anthropic_claude",
            subscription_name="Anthropic Claude",
            executable="claude",
            env_command="ORCHESTRATOR_CLAUDE_SUBSCRIPTION_USAGE_CMD",
            account_env="CLAUDE_ACCOUNT_EMAIL",
            profile_env="CLAUDE_CONFIG_DIR",
            provider_aliases=("claude", "claude-max", "anthropic", "anthropic-api"),
            command_candidates=(("auth", "status"),),
        ),
        SubscriptionSource(
            service_id="github_copilot",
            subscription_name="GitHub Copilot",
            executable="gh",
            env_command="ORCHESTRATOR_COPILOT_SUBSCRIPTION_USAGE_CMD",
            account_env="GITHUB_ACCOUNT",
            profile_env="GH_CONFIG_DIR",
            provider_aliases=("github_copilot", "github-copilot", "copilot", "copilot-gh"),
            command_candidates=(("auth", "status"),),
        ),
        SubscriptionSource(
            service_id="google_gemini",
            subscription_name="Google Gemini",
            executable="gemini",
            env_command="ORCHESTRATOR_GEMINI_SUBSCRIPTION_USAGE_CMD",
            account_env="GEMINI_ACCOUNT_EMAIL",
            profile_env="GEMINI_CONFIG_DIR",
            provider_aliases=("gemini", "google", "google-gemini"),
            command_candidates=(("--version",),),
        ),
        SubscriptionSource(
            service_id="nous_hermes",
            subscription_name="Nous via Hermes",
            executable="hermes",
            env_command="ORCHESTRATOR_HERMES_SUBSCRIPTION_USAGE_CMD",
            account_env="HERMES_ACCOUNT_EMAIL",
            profile_env="HERMES_HOME",
            provider_aliases=("nous", "hermes", "nous-portal"),
            command_candidates=(("portal", "status"),),
            extra_env=(("HERMES_HOME", desktop_hermes_home),),
        ),
    ]


SUBSCRIPTION_STALE_AFTER_SECONDS = 12 * 3600


def _annotate_freshness(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None, int]:
    """Tag each cached snapshot with its age and a `stale` flag so the read path can
    show how old the quota figures are instead of presenting them as if live."""
    now = datetime.now(timezone.utc)
    newest: str | None = None
    stale_count = 0
    annotated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        checked = str(item.get("checked_at") or "")
        age: float | None = None
        if checked:
            try:
                parsed = datetime.fromisoformat(checked.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age = max(0.0, (now - parsed).total_seconds())
                if newest is None or checked > newest:
                    newest = checked
            except ValueError:
                age = None
        item["age_seconds"] = age
        item["stale"] = age is None or age > SUBSCRIPTION_STALE_AFTER_SECONDS
        if item["stale"]:
            stale_count += 1
        annotated.append(item)
    return annotated, newest, stale_count


async def build_subscription_usage_payload(store: StateStore, refresh: bool = False) -> dict[str, Any]:
    if refresh:
        snapshots = await probe_subscription_usage(store.settings, store)
        result = await store.upsert_subscription_usage_snapshots([snapshot_to_dict(s) for s in snapshots])
    else:
        result = {"stored": 0, "failed": 0, "total": 0}
    snapshots = await store.list_subscription_usage_snapshots()
    snapshots, newest_checked_at, stale_count = _annotate_freshness(snapshots)
    return {
        "state": "produced",
        "budget_model": "subscription_entitlement",
        "description": "Subscription limits are account/profile scoped. API budgets are policy caps and live on the API budget lane.",
        "refresh": result,
        "subscriptions": snapshots,
        "newest_checked_at": newest_checked_at,
        "stale_count": stale_count,
        "stale_after_seconds": SUBSCRIPTION_STALE_AFTER_SECONDS,
        "totals": _subscription_totals(snapshots),
    }


async def build_api_budget_payload(store: StateStore) -> dict[str, Any]:
    probes = await store.list_budget_probes()
    providers = sorted({str(row.get("provider_id") or "unknown") for row in probes})
    return {
        "state": "produced",
        "budget_model": "api_policy",
        "description": "API access is treated as uncapped unless an owner-defined policy cap exists. Subscription quotas are tracked separately.",
        "policies": await store.list_api_budget_policies(),
        "legacy_probes": probes,
        "providers_seen": providers,
    }


async def probe_subscription_usage(settings: Settings, store: StateStore) -> list[SubscriptionSnapshot]:
    app_usage = await store.token_usage_by_subscription_source()
    return [_probe_source(source, app_usage.get(source.service_id, 0), settings) for source in default_sources()]


def snapshot_to_dict(snapshot: SubscriptionSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot_id(snapshot.service_id, snapshot.account_id, snapshot.profile_id),
        "service_id": snapshot.service_id,
        "account_id": snapshot.account_id,
        "profile_id": snapshot.profile_id,
        "subscription_name": snapshot.subscription_name,
        "plan_name": snapshot.plan_name,
        "source_type": snapshot.source_type,
        "source_command": snapshot.source_command,
        "tokens_limit": snapshot.tokens_limit,
        "tokens_used_total": snapshot.tokens_used_total,
        "tokens_remaining": snapshot.tokens_remaining,
        "tokens_used_by_app": snapshot.tokens_used_by_app,
        "tokens_used_elsewhere": snapshot.tokens_used_elsewhere,
        "reset_at": snapshot.reset_at,
        "checked_at": snapshot.checked_at,
        "ok": snapshot.ok,
        "confidence": snapshot.confidence,
        "status": snapshot.status,
        "error": snapshot.error,
        "usage_windows_json": json.dumps(snapshot.usage_windows, sort_keys=True),
        "raw_json": json.dumps(snapshot.raw, sort_keys=True),
    }


def snapshot_id(service_id: str, account_id: str, profile_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.@:-]+", "_", f"{service_id}:{account_id}:{profile_id}")
    return clean[:240]


def _probe_source(source: SubscriptionSource, app_tokens: int, settings: Settings) -> SubscriptionSnapshot:
    checked_at = utc_now()
    direct = _probe_direct_source(source, app_tokens, checked_at)
    if direct is not None:
        return direct

    commands = _candidate_commands(source)
    if not commands:
        return _unproved(source, app_tokens, checked_at, "no CLI command configured or executable not found")

    errors: list[str] = []
    for command in commands:
        env = os.environ.copy()
        for key, value in source.extra_env:
            env.setdefault(key, value)
        try:
            result = subprocess.run(
                command,
                cwd=str(settings.repo_root),
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{' '.join(command)}: {exc}")
            continue

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        parsed = parse_usage_output(stdout)
        account = parsed.get("account_id") or os.environ.get(source.account_env or "") or "unproved-account"
        profile = parsed.get("profile_id") or os.environ.get(source.profile_env or "") or "default"
        plan = parsed.get("plan_name")

        if result.returncode != 0:
            errors.append(f"{' '.join(command)}: {_short_error(stderr or stdout or f'CLI exited {result.returncode}')}")
            continue

        return _snapshot_from_parsed(source, app_tokens, checked_at, command, parsed, account, profile, plan, stdout, stderr, result.returncode)

    return _unproved(source, app_tokens, checked_at, " | ".join(errors[-3:]) or "all CLI candidates failed", commands[-1])


def _probe_direct_source(source: SubscriptionSource, app_tokens: int, checked_at: str) -> SubscriptionSnapshot | None:
    probes = {
        "openai_chatgpt": _probe_codex_usage,
        "anthropic_claude": _probe_claude_usage,
        "google_gemini": _probe_gemini_usage,
        "github_copilot": _probe_copilot_usage,
        "nous_hermes": _probe_hermes_usage,
    }
    probe = probes.get(source.service_id)
    if probe is None:
        return None
    return probe(source, app_tokens, checked_at)


def _probe_codex_usage(source: SubscriptionSource, app_tokens: int, checked_at: str) -> SubscriptionSnapshot:
    auth_path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
    auth = _load_json(auth_path)
    token = _dig(auth, "tokens", "access_token")
    account_id = str(_dig(auth, "tokens", "account_id") or "codex-chatgpt")
    if not token:
        return _auth_required(source, app_tokens, checked_at, "Codex auth.json does not contain a ChatGPT access token", str(auth_path))

    status, data = _http_json(
        "https://chatgpt.com/backend-api/wham/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "CodexBar",
            "ChatGPT-Account-Id": account_id,
        },
    )
    if status != 200:
        return _unproved(source, app_tokens, checked_at, f"ChatGPT usage endpoint returned HTTP {status}", ["codex", "login", "status"])

    windows: list[dict[str, Any]] = []
    rate_limit = data.get("rate_limit") if isinstance(data, dict) else {}
    if isinstance(rate_limit, dict):
        for key in ("primary_window", "secondary_window"):
            window = rate_limit.get(key)
            if not isinstance(window, dict):
                continue
            used = _as_float(window.get("used_percent"))
            reset_at = _unix_to_iso(window.get("reset_at"))
            windows.append(
                UsageWindow(
                    label=key.replace("_", " "),
                    used_percent=used,
                    remaining_percent=_remaining_from_used(used),
                    reset_at=reset_at,
                    limit_window_seconds=_as_int(window.get("limit_window_seconds")),
                    source="chatgpt_wham_usage",
                ).to_dict()
            )

    if not windows:
        return _unproved(source, app_tokens, checked_at, "ChatGPT usage endpoint did not include rate-limit windows", ["codex", "login", "status"])

    return _snapshot_from_windows(
        source,
        app_tokens,
        checked_at,
        account_id=str(data.get("email") or data.get("account_id") or account_id),
        profile_id=str(auth_path.parent),
        plan_name=str(data.get("plan") or "ChatGPT"),
        source_type="provider_endpoint",
        source_command="GET chatgpt.com/backend-api/wham/usage",
        confidence="provider_usage_window",
        status="ok",
        usage_windows=windows,
        raw={"http_status": status, "plan": data.get("plan"), "window_count": len(windows), "account_id": data.get("account_id")},
    )


def _probe_claude_usage(source: SubscriptionSource, app_tokens: int, checked_at: str) -> SubscriptionSnapshot:
    cred_path = Path.home() / ".claude" / ".credentials.json"
    credentials = _load_json(cred_path)
    token = _dig(credentials, "claudeAiOauth", "accessToken")
    if not token:
        return _auth_required(source, app_tokens, checked_at, "Claude credentials do not contain an OAuth access token", str(cred_path))

    account, plan = _claude_auth_status()
    status, data = _http_json(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "openclaw",
            "Accept": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    if status != 200:
        return _unproved(source, app_tokens, checked_at, f"Claude OAuth usage endpoint returned HTTP {status}", ["claude", "auth", "status"])

    windows: list[dict[str, Any]] = []
    for key, value in data.items() if isinstance(data, dict) else []:
        if not isinstance(value, dict):
            continue
        used = _as_float(value.get("utilization"))
        if used is None and value.get("remaining_percent") is not None:
            used = 100 - float(value["remaining_percent"])
        windows.append(
            UsageWindow(
                label=key.replace("_", " "),
                used_percent=used,
                remaining_percent=_remaining_from_used(used),
                reset_at=_first_text_value(value, ("resets_at", "reset_at")),
                source="anthropic_oauth_usage",
            ).to_dict()
        )

    if not windows:
        return _unproved(source, app_tokens, checked_at, "Claude OAuth usage endpoint did not include utilization windows", ["claude", "auth", "status"])

    return _snapshot_from_windows(
        source,
        app_tokens,
        checked_at,
        account_id=account or "claude-authenticated",
        profile_id=str(cred_path.parent),
        plan_name=plan or "Claude",
        source_type="provider_endpoint",
        source_command="GET api.anthropic.com/api/oauth/usage",
        confidence="provider_usage_window",
        status="ok",
        usage_windows=windows,
        raw={"http_status": status, "window_count": len(windows), "keys": sorted(data.keys()) if isinstance(data, dict) else []},
    )


def _probe_gemini_usage(source: SubscriptionSource, app_tokens: int, checked_at: str) -> SubscriptionSnapshot:
    gemini_dir = Path(os.environ.get("GEMINI_CONFIG_DIR") or Path.home() / ".gemini")
    cred_path = gemini_dir / "oauth_creds.json"
    account_path = gemini_dir / "google_accounts.json"
    credentials = _load_json(cred_path)
    token = credentials.get("access_token") if isinstance(credentials, dict) else None
    if not token:
        return _auth_required(source, app_tokens, checked_at, "Gemini oauth_creds.json does not contain an access token", str(cred_path))

    status, data = _gemini_quota_request(str(token))
    if status == 401 and credentials.get("refresh_token"):
        refreshed = _refresh_gemini_token(str(credentials["refresh_token"]))
        if refreshed.get("access_token"):
            credentials.update(refreshed)
            _write_json(cred_path, credentials)
            status, data = _gemini_quota_request(str(refreshed["access_token"]))
    if status != 200:
        return _unproved(source, app_tokens, checked_at, f"Gemini quota endpoint returned HTTP {status}", ["gemini", "--version"])

    buckets = _gemini_buckets(data)
    windows: list[dict[str, Any]] = []
    for bucket in buckets:
        model = str(bucket.get("modelId") or bucket.get("model_id") or bucket.get("id") or "Gemini bucket")
        remaining_fraction = _as_float(bucket.get("remainingFraction") or bucket.get("remaining_fraction"))
        remaining_percent = remaining_fraction * 100 if remaining_fraction is not None and remaining_fraction <= 1 else remaining_fraction
        used_percent = _remaining_to_used(remaining_percent)
        windows.append(
            UsageWindow(
                label=model,
                used_percent=used_percent,
                remaining_percent=remaining_percent,
                reset_at=_first_text_value(bucket, ("resetTime", "reset_time", "reset_at")),
                source="google_cloudcode_quota",
            ).to_dict()
        )

    if not windows:
        return _unproved(source, app_tokens, checked_at, "Gemini quota endpoint did not include quota buckets", ["gemini", "--version"])

    account = _gemini_active_account(account_path) or "gemini-authenticated"
    return _snapshot_from_windows(
        source,
        app_tokens,
        checked_at,
        account_id=account,
        profile_id=str(gemini_dir),
        plan_name="Gemini CLI quota",
        source_type="provider_endpoint",
        source_command="POST cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota",
        confidence="provider_usage_window",
        status="ok",
        usage_windows=windows,
        raw={"http_status": status, "bucket_count": len(windows), "active_account": account},
    )


def _probe_copilot_usage(source: SubscriptionSource, app_tokens: int, checked_at: str) -> SubscriptionSnapshot:
    account = _github_account() or _vscode_github_account() or "github-authenticated"
    sku = _vscode_copilot_sku()
    token_source, copilot_token, token_error = _copilot_token()
    if not copilot_token:
        session_usage = _copilot_latest_cli_session_usage()
        if session_usage:
            return _snapshot_from_windows(
                source,
                app_tokens,
                checked_at,
                account_id=account,
                profile_id=str(Path.home() / ".copilot"),
                plan_name=sku or "GitHub Copilot",
                source_type="local_cli_receipt",
                source_command="read ~/.copilot/session-state/*/events.jsonl",
                confidence="session_consumption_only",
                status="partial",
                usage_windows=[session_usage],
                raw={"token_error": token_error, "token_source": token_source, "session_usage_source": session_usage.get("source")},
                error=f"Monthly remaining quota requires Copilot internal token; token exchange returned {token_error or 'no usable cached token'}.",
            )
        return _auth_required(
            source,
            app_tokens,
            checked_at,
            f"GitHub auth did not mint a Copilot internal token ({token_error or 'no usable cached token'})",
            token_source or "gh copilot_internal/v2/token",
            account_id=account,
            plan_name=sku,
            confidence="account_only",
        )

    status, data = _http_json(
        "https://api.github.com/copilot_internal/user",
        headers={
            "Authorization": f"token {copilot_token}",
            "Accept": "application/json",
            "Editor-Version": "vscode/1.96.2",
            "User-Agent": "GitHubCopilotChat/0.26.7",
            "X-Github-Api-Version": "2025-04-01",
        },
    )
    if status != 200:
        return _unproved(source, app_tokens, checked_at, f"Copilot usage endpoint returned HTTP {status}", ["gh", "auth", "status"])

    windows: list[dict[str, Any]] = []
    snapshots = data.get("quota_snapshots") if isinstance(data, dict) else None
    if isinstance(snapshots, dict):
        for key, value in snapshots.items():
            if not isinstance(value, dict):
                continue
            remaining_percent = _as_float(value.get("percent_remaining"))
            windows.append(
                UsageWindow(
                    label=key.replace("_", " "),
                    used_percent=_remaining_to_used(remaining_percent),
                    remaining_percent=remaining_percent,
                    reset_at=_first_text_value(value, ("reset_date", "reset_at", "resets_at")),
                    source="github_copilot_internal_user",
                ).to_dict()
            )

    if not windows:
        return _unproved(source, app_tokens, checked_at, "Copilot usage endpoint did not include quota snapshots", ["gh", "auth", "status"])

    return _snapshot_from_windows(
        source,
        app_tokens,
        checked_at,
        account_id=account,
        profile_id=token_source or "github-copilot",
        plan_name=str(data.get("copilot_plan") or sku or "GitHub Copilot"),
        source_type="provider_endpoint",
        source_command="GET api.github.com/copilot_internal/user",
        confidence="provider_usage_window",
        status="ok",
        usage_windows=windows,
        raw={"http_status": status, "window_count": len(windows), "token_source": token_source, "plan": data.get("copilot_plan")},
    )


def _probe_hermes_usage(source: SubscriptionSource, app_tokens: int, checked_at: str) -> SubscriptionSnapshot:
    commands = _candidate_commands(source)
    command = commands[0] if commands else None
    env = os.environ.copy()
    for key, value in source.extra_env:
        env.setdefault(key, value)
    if not command:
        return _auth_required(source, app_tokens, checked_at, "Hermes CLI not found", "hermes")
    try:
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return _unproved(source, app_tokens, checked_at, f"Hermes portal status failed: {exc}", command)

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    parsed = parse_usage_output(stdout)
    account = parsed.get("account_id") or "nous-portal"
    if "not logged in" in stdout.lower() or "not logged in" in stderr.lower():
        auth_error = _hermes_last_auth_error(env.get("HERMES_HOME"))
        detail = "Hermes reports Nous Portal is not logged in; run hermes auth add nous --type oauth in an owner-approved login lane."
        if auth_error:
            detail = f"{detail} Last Hermes diagnostic: {auth_error}"
        return _auth_required(
            source,
            app_tokens,
            checked_at,
            detail,
            " ".join(command),
            account_id=str(account),
            plan_name=parsed.get("plan_name"),
        )

    return _snapshot_from_parsed(source, app_tokens, checked_at, command, parsed, account, env.get("HERMES_HOME") or "default", parsed.get("plan_name"), stdout, stderr, result.returncode)


def _snapshot_from_windows(
    source: SubscriptionSource,
    app_tokens: int,
    checked_at: str,
    account_id: str,
    profile_id: str,
    plan_name: str | None,
    source_type: str,
    source_command: str,
    confidence: str,
    status: str,
    usage_windows: list[dict[str, Any]],
    raw: dict[str, Any],
    error: str | None = None,
) -> SubscriptionSnapshot:
    reset_at = _earliest_reset(usage_windows)
    return SubscriptionSnapshot(
        service_id=source.service_id,
        account_id=account_id,
        profile_id=profile_id,
        subscription_name=source.subscription_name,
        plan_name=plan_name,
        source_type=source_type,
        source_command=source_command,
        tokens_limit=None,
        tokens_used_total=None,
        tokens_remaining=None,
        tokens_used_by_app=app_tokens,
        tokens_used_elsewhere=None,
        reset_at=reset_at,
        checked_at=checked_at,
        ok=True,
        confidence=confidence,
        status=status,
        error=error,
        raw=raw,
        usage_windows=usage_windows,
    )


def _snapshot_from_parsed(
    source: SubscriptionSource,
    app_tokens: int,
    checked_at: str,
    command: list[str],
    parsed: dict[str, Any],
    account: Any,
    profile: Any,
    plan: Any,
    stdout: str,
    stderr: str,
    returncode: int,
) -> SubscriptionSnapshot:
    limit = _as_int(parsed.get("tokens_limit"))
    used_total = _as_int(parsed.get("tokens_used_total"))
    remaining = _as_int(parsed.get("tokens_remaining"))
    if limit is None and used_total is not None and remaining is not None:
        limit = used_total + remaining
    if used_total is None and limit is not None and remaining is not None:
        used_total = max(limit - remaining, 0)
    if remaining is None and limit is not None and used_total is not None:
        remaining = max(limit - used_total, 0)
    elsewhere = max(used_total - app_tokens, 0) if used_total is not None else None
    proved_account = str(account) not in ("", "unproved-account")
    confidence = "cli_exact" if (limit is not None or used_total is not None or remaining is not None) else "account_only" if proved_account else "unproved"
    error = None
    status = "ok" if confidence == "cli_exact" else "account_only" if confidence == "account_only" else "unproved"
    if confidence == "account_only":
        error = "CLI proved account/profile but did not expose token usage totals"
    elif confidence == "unproved":
        error = "CLI returned without proving the logged-in account or token usage totals"
    return SubscriptionSnapshot(
        service_id=source.service_id,
        account_id=str(account),
        profile_id=str(profile),
        subscription_name=source.subscription_name,
        plan_name=str(plan) if plan else None,
        source_type="cli",
        source_command=" ".join(command),
        tokens_limit=limit,
        tokens_used_total=used_total,
        tokens_remaining=remaining,
        tokens_used_by_app=app_tokens,
        tokens_used_elsewhere=elsewhere,
        reset_at=parsed.get("reset_at"),
        checked_at=checked_at,
        ok=confidence != "unproved",
        confidence=confidence,
        status=status,
        error=error,
        raw={"stdout": stdout[-4000:], "stderr": stderr[-1000:], "returncode": returncode, "parsed": parsed},
    )


def _candidate_commands(source: SubscriptionSource) -> list[list[str]]:
    override = os.environ.get(source.env_command or "")
    if override:
        return [override.split()]
    exe = shutil.which(source.executable)
    if not exe:
        return []
    return [[exe, *args] for args in source.command_candidates]


def _auth_required(
    source: SubscriptionSource,
    app_tokens: int,
    checked_at: str,
    error: str,
    command: str,
    account_id: str | None = None,
    plan_name: str | None = None,
    confidence: str = "auth_required",
) -> SubscriptionSnapshot:
    account = account_id or os.environ.get(source.account_env or "") or "auth-required"
    profile = os.environ.get(source.profile_env or "") or "default"
    return SubscriptionSnapshot(
        service_id=source.service_id,
        account_id=account,
        profile_id=profile,
        subscription_name=source.subscription_name,
        plan_name=plan_name,
        source_type="provider_endpoint",
        source_command=command,
        tokens_limit=None,
        tokens_used_total=None,
        tokens_remaining=None,
        tokens_used_by_app=app_tokens,
        tokens_used_elsewhere=None,
        reset_at=None,
        checked_at=checked_at,
        ok=False,
        confidence=confidence,
        status="auth_required",
        error=error,
        raw={"error": error},
    )


def _unproved(source: SubscriptionSource, app_tokens: int, checked_at: str, error: str, command: list[str] | None = None) -> SubscriptionSnapshot:
    account = os.environ.get(source.account_env or "") or "unproved-account"
    profile = os.environ.get(source.profile_env or "") or "default"
    return SubscriptionSnapshot(
        service_id=source.service_id,
        account_id=account,
        profile_id=profile,
        subscription_name=source.subscription_name,
        plan_name=None,
        source_type="cli",
        source_command=" ".join(command) if command else None,
        tokens_limit=None,
        tokens_used_total=None,
        tokens_remaining=None,
        tokens_used_by_app=app_tokens,
        tokens_used_elsewhere=None,
        reset_at=None,
        checked_at=checked_at,
        ok=False,
        confidence="unproved",
        status="unproved",
        error=error,
        raw={"error": error},
    )


def parse_usage_output(text: str) -> dict[str, Any]:
    if not text:
        return {}
    parsed = _parse_json_usage(text)
    if parsed:
        return parsed
    return _parse_text_usage(text)


def _parse_json_usage(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", text, flags=re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
    flat = _flatten_json(data)
    return {
        "account_id": _first_text(flat, ("email", "account.email", "account", "username", "login", "user.email", "user.login")),
        "profile_id": _first_text(flat, ("profile", "profile_id", "home", "config_dir", "organization", "org")),
        "plan_name": _first_text(flat, ("plan", "plan_name", "subscription", "tier", "entitlement")),
        "tokens_limit": _first_number(flat, ("tokens_limit", "token_limit", "monthly_token_limit", "limit", "quota.limit", "usage.limit", "subscription.limit")),
        "tokens_used_total": _first_number(flat, ("tokens_used", "tokens_used_total", "used", "usage.used", "quota.used", "current_usage", "consumed")),
        "tokens_remaining": _first_number(flat, ("tokens_remaining", "remaining", "available", "available_tokens", "quota.remaining", "usage.remaining")),
        "reset_at": _first_text(flat, ("reset_at", "resets_at", "reset", "renewal_at", "quota.reset_at")),
    }


def _parse_text_usage(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if email:
        result["account_id"] = email.group(0)
    account = re.search(r"\baccount\s+([A-Za-z0-9_.-]+)\b", text, flags=re.I)
    if account and "account_id" not in result:
        result["account_id"] = account.group(1)
    plan = re.search(r"\b(?:plan|tier|subscription)\s*[:=]\s*([^\r\n]+)", text, flags=re.I)
    if plan:
        result["plan_name"] = plan.group(1).strip()
    result["tokens_remaining"] = _text_number(text, r"(?:tokens?\s*)?remaining\s*[:=]?\s*([0-9][0-9,._ ]*)")
    result["tokens_limit"] = _text_number(text, r"(?:tokens?\s*)?(?:limit|quota)\s*[:=]?\s*([0-9][0-9,._ ]*)")
    result["tokens_used_total"] = _text_number(text, r"(?:tokens?\s*)?(?:used|consumed)\s*[:=]?\s*([0-9][0-9,._ ]*)")
    reset = re.search(r"\breset(?:s|_at)?\s*[:=]\s*([^\r\n]+)", text, flags=re.I)
    if reset:
        result["reset_at"] = reset.group(1).strip()
    return {k: v for k, v in result.items() if v not in (None, "")}


def _http_json(url: str, headers: dict[str, str], method: str = "GET", body: bytes | None = None) -> tuple[int, dict[str, Any]]:
    request = Request(url, headers=headers, data=body, method=method)
    try:
        with urlopen(request, timeout=25) as response:
            payload = response.read().decode("utf-8")
            return int(response.status), json.loads(payload or "{}")
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), {"error": text[:500]}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return 0, {"error": str(exc)}


def _gemini_quota_request(token: str) -> tuple[int, dict[str, Any]]:
    return _http_json(
        "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
        body=b"{}",
    )


def _refresh_gemini_token(refresh_token: str) -> dict[str, Any]:
    client_id, client_secret = _gemini_cli_oauth_client()
    if not client_id or not client_secret:
        return {}
    payload = json.dumps(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    status, data = _http_json(
        "https://oauth2.googleapis.com/token",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
        body=payload,
    )
    return data if status == 200 else {}


def _gemini_cli_oauth_client() -> tuple[str | None, str | None]:
    env_id = os.environ.get("GEMINI_CLI_OAUTH_CLIENT_ID", "").strip()
    env_secret = os.environ.get("GEMINI_CLI_OAUTH_CLIENT_SECRET", "").strip()
    if env_secret:
        return env_id or DEFAULT_GEMINI_CLI_OAUTH_CLIENT_ID, env_secret

    for path in _gemini_cli_bundle_candidates():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        client_id = _first_regex(text, r"OAUTH_CLIENT_ID\s*=\s*[\"']([^\"']+)[\"']")
        client_secret = _first_regex(text, r"OAUTH_CLIENT_SECRET\s*=\s*[\"']([^\"']+)[\"']")
        if client_id and client_secret:
            return client_id, client_secret
    return env_id or DEFAULT_GEMINI_CLI_OAUTH_CLIENT_ID, None


def _gemini_cli_bundle_candidates() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.extend((Path(appdata) / "npm" / "node_modules" / "@google" / "gemini-cli" / "bundle").glob("chunk-*.js"))

    executable = shutil.which("gemini")
    if executable:
        path = Path(executable)
        candidates.extend((path.parent / "node_modules" / "@google" / "gemini-cli" / "bundle").glob("chunk-*.js"))
        candidates.extend((path.parent / "node_modules" / "@google" / "gemini-cli" / "bundle").glob("*.js"))

    return sorted({path.resolve() for path in candidates if path.exists()}, key=lambda item: item.stat().st_mtime, reverse=True)


def _first_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _gemini_buckets(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("quotaBuckets", "quota_buckets", "buckets", "modelQuotas", "model_quotas"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(data.get("quota"), dict):
        quota = data["quota"]
        for key in ("buckets", "quotaBuckets"):
            if isinstance(quota.get(key), list):
                return [item for item in quota[key] if isinstance(item, dict)]
    return []


def _copilot_token() -> tuple[str | None, str | None, str | None]:
    cached_source, cached_token = _cached_copilot_token()
    if cached_token:
        return cached_source, cached_token, None
    gh_source, gh_token = _copilot_raw_github_token()
    if not gh_token:
        return gh_source, None, "COPILOT_GITHUB_TOKEN/GH_TOKEN/GITHUB_TOKEN and gh auth token unavailable"
    status, data = _http_json(
        "https://api.github.com/copilot_internal/v2/token",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {gh_token}",
            "Editor-Version": "vscode/1.96.2",
            "User-Agent": "GitHubCopilotChat/0.26.7",
            "X-Github-Api-Version": "2025-04-01",
        },
    )
    if status == 200 and isinstance(data.get("token"), str):
        return f"{gh_source}:github_copilot_token_exchange", str(data["token"]), None
    return f"{gh_source}:github_copilot_token_exchange", None, f"HTTP {status}; set COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN to a GitHub token that can access Copilot Requests"


def _copilot_latest_cli_session_usage() -> dict[str, Any] | None:
    state_dir = Path.home() / ".copilot" / "session-state"
    if not state_dir.exists():
        return None
    event_files = sorted(state_dir.glob("*/events.jsonl"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    for path in event_files[:20]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "session.shutdown":
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            premium = _as_float(data.get("totalPremiumRequests"))
            tokens = _as_int(data.get("currentTokens"))
            if premium is None and tokens is None:
                continue
            return UsageWindow(
                label="latest CLI session",
                unit="premium_requests",
                current=premium,
                limit=None,
                remaining=None,
                used_percent=None,
                remaining_percent=None,
                reset_at=None,
                source=str(path),
            ).to_dict() | {"tokens_current": tokens}
    return None


def _cached_copilot_token() -> tuple[str | None, str | None]:
    path = Path(os.environ.get("OPENCLAW_STATE_DIR") or Path.home() / ".openclaw") / "credentials" / "github-copilot.token.json"
    data = _load_json(path)
    token = data.get("token") if isinstance(data, dict) else None
    expires_at = _as_float(data.get("expiresAt")) if isinstance(data, dict) else None
    if token and expires_at and expires_at - datetime.now(timezone.utc).timestamp() * 1000 > 300_000:
        return str(path), str(token)
    return (str(path) if path.exists() else None), None


def _gh_auth_token() -> str | None:
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(["gh", "auth", "token"], text=True, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    token = (result.stdout or "").strip()
    return token if result.returncode == 0 and token else None


def _copilot_raw_github_token() -> tuple[str, str | None]:
    for key in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(key, "").strip()
        if token:
            return key, token
    return "gh auth token", _gh_auth_token()


def _github_account() -> str | None:
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(["gh", "api", "user", "--jq", ".login"], text=True, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    login = (result.stdout or "").strip()
    return login or None


def _vscode_github_account() -> str | None:
    value = _vscode_state_value("github-sofakingmc0804")
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return str(parsed.get("accountName") or parsed.get("login") or "") or None
    return None


def _vscode_copilot_sku() -> str | None:
    value = _vscode_state_value("extensionsAssignmentFilterProvider.copilotSku")
    if not value:
        return None
    return value.strip('"')


def _vscode_state_value(key: str) -> str | None:
    db_path = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Code" / "User" / "globalStorage" / "state.vscdb"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def _claude_auth_status() -> tuple[str | None, str | None]:
    if not shutil.which("claude"):
        return None, None
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
            env=oauth_only_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    parsed = parse_usage_output(result.stdout or "")
    plan = parsed.get("plan_name")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        raw = {}
    account = parsed.get("account_id") or (raw.get("emailAddress") if isinstance(raw, dict) else None)
    plan = plan or (raw.get("subscriptionType") if isinstance(raw, dict) else None)
    return (str(account) if account else None), (str(plan) if plan else None)


def _gemini_active_account(account_path: Path) -> str | None:
    data = _load_json(account_path)
    if not isinstance(data, dict):
        return None
    active = data.get("active") or data.get("activeAccount")
    if isinstance(active, str) and active:
        return active
    for key in ("accounts", "users"):
        accounts = data.get(key)
        if isinstance(accounts, list):
            for item in accounts:
                if isinstance(item, dict) and item.get("active"):
                    return str(item.get("email") or item.get("account") or "") or None
    return None


def _hermes_last_auth_error(hermes_home: str | None) -> str | None:
    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    data = _load_json(home / "auth.json")
    error = _dig(data, "providers", "nous", "last_auth_error")
    if not isinstance(error, dict):
        return None
    message = str(error.get("message") or "").strip()
    reason = str(error.get("reason") or "").strip()
    if not message:
        return None
    prefix = f"{reason}: " if reason else ""
    return f"{prefix}{message}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _dig(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten_json(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(_flatten_json(item, f"{prefix}.{index}" if prefix else str(index)))
    else:
        out[prefix] = value
        if prefix:
            out[prefix.split(".")[-1]] = value
    return out


def _first_text(flat: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = flat.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _first_text_value(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _first_number(flat: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _as_int(flat.get(key))
        if value is not None:
            return value
    return None


def _text_number(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, flags=re.I)
    if not match:
        return None
    return _as_int(match.group(1))


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return int(number) if number is not None else None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.]", "", str(value))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _remaining_from_used(used_percent: float | None) -> float | None:
    if used_percent is None:
        return None
    return max(0.0, min(100.0, 100.0 - used_percent))


def _remaining_to_used(remaining_percent: float | None) -> float | None:
    if remaining_percent is None:
        return None
    return max(0.0, min(100.0, 100.0 - remaining_percent))


def _unix_to_iso(value: Any) -> str | None:
    timestamp = _as_float(value)
    if timestamp is None:
        return None
    if timestamp > 1e12:
        timestamp = timestamp / 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


def _earliest_reset(windows: list[dict[str, Any]]) -> str | None:
    values = [str(window.get("reset_at")) for window in windows if window.get("reset_at")]
    return sorted(values)[0] if values else None


def _short_error(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value[:500] if value else "unknown CLI failure"


def _subscription_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [row for row in rows if row.get("tokens_used_total") is not None or row.get("tokens_remaining") is not None]
    windows = [row for row in rows if _row_windows(row)]
    return {
        "subscriptions": len(rows),
        "proved_usage": len(exact) + len(windows),
        "proved_windows": sum(len(_row_windows(row)) for row in rows),
        "auth_required": sum(1 for row in rows if row.get("status") == "auth_required"),
        "app_tokens": sum(int(row.get("tokens_used_by_app") or 0) for row in rows),
        "total_used": sum(int(row.get("tokens_used_total") or 0) for row in exact),
        "remaining": sum(int(row.get("tokens_remaining") or 0) for row in exact),
    }


def _row_windows(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("usage_windows_json") or "[]"
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
