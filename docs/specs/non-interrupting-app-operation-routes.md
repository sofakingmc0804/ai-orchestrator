# Non-Interrupting App Operation Routes

Authority: current Windows app-operation research plus local Claude/Codex runtime evidence.

The default route for app work must protect the user's active desktop surface. Visible mouse, keyboard, foreground-window, and full Computer Use control are last-resort routes, not the first competent path.

## Route Order

1. Connector, API, export, or local authority artifact.
2. Custom URI or protocol activation into the target app.
3. App-local session artifacts and app-owned transcripts.
4. Headless or isolated browser profile when browser authority is enough.
5. Targeted Windows UI Automation `InvokePattern` on a named control.
6. Visible Computer Use only inside a scoped lease or confirmed idle window.
7. Operator packet only when no authority-equivalent route exists.

## Source Anchors

- Microsoft UI Automation `InvokePattern` supports invoking a control's default action without modeling raw mouse movement.
- Microsoft URI/protocol activation is the documented route for launching apps into a specific URI target.
- Microsoft UI Automation `IsOffscreen` is relevant evidence for whether a UI element is visible to the user, but visibility is not the same as permission to take over the active desktop.
- Electron custom protocol handling is the relevant app-framework lane when a desktop app exposes a protocol such as `claude://`.

## Completion Evidence

For Claude Desktop, acceptable non-interrupting runtime evidence includes a fresh local-agent session transcript, app-local artifact readback, Stop-hook numeric output, and a trace marker containing `non_foreground local_agent_mode_session app_transcript`.

Screenshots, local unit tests, logs without app transcript linkage, or assistant prose are supporting evidence only.
