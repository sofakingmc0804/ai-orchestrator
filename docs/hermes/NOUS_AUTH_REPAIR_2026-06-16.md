# Nous Auth Repair

Date: 2026-06-16 local / 2026-06-17 UTC

## Consequence

Hermes was logged in at the app/account surface, but the CLI/runtime surface was not usable for Nous. Routing through the wrong surface would keep producing false negatives.

## Mechanism

There were two separate failures.

First, command resolution was stale. `hermes` resolved to the global Roaming Python install:

- `C:\Users\Couch\AppData\Roaming\Python\Python313\Scripts\hermes.exe`
- version `0.15.2`

The app-owned install is:

- `C:\Users\Couch\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe`
- version `0.16.0`

Second, the runtime credential file has no Nous access token or refresh token:

- `C:\Users\Couch\AppData\Local\hermes\auth.json`
- provider fields: `client_id`, `portal_base_url`, `inference_base_url`, `token_type`, `scope`, `tls`, `last_auth_error`
- access token present: `False`
- refresh token present: `False`
- last error: `invalid_grant`
- reason: `runtime_access_refresh_failure`
- timestamp: `2026-06-11T03:38:18.692394+00:00`

The recorded Hermes error says the refresh token was reused by another process and revoked. That matches the stale-governor risk: an external monitor or second install can spend a single-use refresh token without persisting the rotated replacement.

## Action Taken

- Retired `C:\Users\Couch\.ai-resource-governor` and removed its scheduled refresh.
- Removed `C:\Users\Couch\.ai-resource-governor\bin` from user PATH.
- Installed a user-owned command shim at `C:\Users\Couch\AppData\Local\Microsoft\WindowsApps\hermes.cmd`.
- The shim routes `hermes` to `C:\Users\Couch\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe`.
- Verified `hermes --version` now reports `Hermes Agent v0.16.0 (2026.6.5)`.
- Ran `hermes auth reset nous`; it reset `0` credentials because no usable Nous credential remains.

Persisted receipt:

- `.runtime\archives\ai-resource-governor-retirement-20260617T035028Z\hermes-command-shim-repair.json`

## Current Boundary

The CLI/runtime Nous token is still absent. The official remaining repair is `hermes portal login` or `hermes auth add nous --type oauth`, which uses a device-code approval flow. That flow requires the logged-in owner/app/browser session to approve the code. It cannot be completed by local file repair because no shared `nous_auth.json` exists and the app WebView session does not expose a reusable CLI token.

## Road Through

Keep the retired governor offline so it cannot burn refresh tokens again. Approve a fresh Nous device-code login from the already logged-in app/account surface, then verify:

```powershell
hermes auth status nous
hermes portal status
```

Expected terminal proof after approval: `hermes auth status nous` reports logged in and `C:\Users\Couch\AppData\Local\hermes\auth.json` contains Nous token fields.
