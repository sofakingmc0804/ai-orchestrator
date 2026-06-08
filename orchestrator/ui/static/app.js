async function getJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return await res.json();
}

function renderRows(id, rows, fields) {
  const el = document.getElementById(id);
  el.innerHTML = "";
  const table = document.createElement("table");
  const head = document.createElement("tr");
  fields.forEach(f => {
    const th = document.createElement("th");
    th.textContent = f;
    head.appendChild(th);
  });
  table.appendChild(head);
  rows.forEach(row => {
    const tr = document.createElement("tr");
    fields.forEach(f => {
      const td = document.createElement("td");
      td.textContent = row[f] ?? "";
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
  el.appendChild(table);
}

function payloadValue(row, field) {
  if (field !== "payload") return row[field] ?? "";
  const payload = row.payload || {};
  return payload.path || payload.name || payload.url || payload.text || JSON.stringify(payload);
}

function renderSelectionRows(rows) {
  const el = document.getElementById("selections");
  el.innerHTML = "";
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["kind", "payload", "added_at"].forEach(f => {
    const th = document.createElement("th");
    th.textContent = f;
    head.appendChild(th);
  });
  table.appendChild(head);
  rows.forEach(row => {
    const tr = document.createElement("tr");
    ["kind", "payload", "added_at"].forEach(f => {
      const td = document.createElement("td");
      td.textContent = payloadValue(row, f);
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
  el.appendChild(table);
}

function renderApprovals(rows) {
  const el = document.getElementById("approvals");
  el.innerHTML = "";
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No pending approvals.";
    el.appendChild(empty);
    return;
  }
  rows.forEach(row => {
    const item = document.createElement("div");
    item.className = "approval";
    const title = document.createElement("strong");
    title.textContent = `${row.consequence_tier || "unknown"} ${row.intent_id}`;
    const body = document.createElement("p");
    body.textContent = row.raw_text || row.body || "";
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    ["approve", "reject"].forEach(action => {
      const button = document.createElement("button");
      button.textContent = action;
      button.addEventListener("click", async () => {
        const result = await getJson(`/api/approvals/${row.intent_id}/${action}`, {method: "POST"});
        document.getElementById("result").textContent = JSON.stringify(result, null, 2);
        await refreshAll();
      });
      actions.appendChild(button);
    });
    item.appendChild(title);
    item.appendChild(body);
    item.appendChild(actions);
    el.appendChild(item);
  });
}

function renderWorkingMemory(rows) {
  const mapped = rows.map(row => ({
    project: row.project_name || row.project_id,
    in_flight: (row.in_flight_intents || []).length,
    outputs: (row.recent_outputs || []).length,
    approvals: (row.pending_approvals || []).length,
    last_activity_at: row.last_activity_at,
  }));
  renderRows("working-memory", mapped, ["project", "in_flight", "outputs", "approvals", "last_activity_at"]);
}

function renderQuotaState(state) {
  const rows = Object.values(state || {});
  renderRows("quota-state", rows, ["provider", "remaining", "units_limit", "percent_remaining", "window_start"]);
}

function renderSpecStatus(status) {
  const targets = (status.targets || []).map(row => ({
    id: row.id,
    status: row.status,
    title: row.title,
    evidence: (row.evidence || []).join("; "),
    next: row.next_action || "",
  }));
  renderRows("spec-status", targets, ["id", "status", "title", "evidence", "next"]);
}

function renderAdapterProofOptions(caps) {
  const adapterSelect = document.getElementById("proof-adapter");
  const capabilitySelect = document.getElementById("proof-capability");
  const previousAdapter = adapterSelect.value;
  const previousCapability = capabilitySelect.value;
  const adapters = [...new Set((caps || []).map(row => row.adapter_name).filter(Boolean))].sort();
  adapterSelect.innerHTML = "";
  adapters.forEach(adapter => {
    const option = document.createElement("option");
    option.value = adapter;
    option.textContent = adapter;
    adapterSelect.appendChild(option);
  });
  if (adapters.includes(previousAdapter)) adapterSelect.value = previousAdapter;
  const selectedAdapter = adapterSelect.value || adapters[0] || "";
  const capabilities = (caps || [])
    .filter(row => row.adapter_name === selectedAdapter)
    .map(row => row.capability_id)
    .filter(Boolean)
    .sort();
  capabilitySelect.innerHTML = "";
  capabilities.forEach(capability => {
    const option = document.createElement("option");
    option.value = capability;
    option.textContent = capability;
    capabilitySelect.appendChild(option);
  });
  if (capabilities.includes(previousCapability)) capabilitySelect.value = previousCapability;
}

function renderSchedulerTasks(rows) {
  const el = document.getElementById("scheduler-tasks");
  el.innerHTML = "";
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["name", "enabled", "review", "schedule", "last_run_at", "next_run_at", "actions"].forEach(f => {
    const th = document.createElement("th");
    th.textContent = f;
    head.appendChild(th);
  });
  table.appendChild(head);
  rows.forEach(row => {
    const tr = document.createElement("tr");
    const schedule = row.schedule_kind === "interval" ? `${row.interval_seconds}s` : row.schedule_kind;
    [row.name, row.enabled, row.review_state || "", schedule, row.last_run_at || "", row.next_run_at || ""].forEach(value => {
      const td = document.createElement("td");
      td.textContent = value ?? "";
      tr.appendChild(td);
    });
    const actions = document.createElement("td");
    const group = document.createElement("div");
    group.className = "table-actions";
    const actionSpecs = [["trigger", "Trigger"], ["edit", "Edit"]];
    if (row.enabled) {
      actionSpecs.splice(1, 0, ["pause", "Pause"]);
    } else if (row.task_type === "legacy_claude_scheduled_task" && row.review_state !== "owner_approved") {
      actionSpecs.splice(1, 0, ["activate", "Activate"], ["reject", "Reject"]);
    } else {
      actionSpecs.splice(1, 0, ["resume", "Resume"]);
    }
    actionSpecs.forEach(([action, label]) => {
      const button = document.createElement("button");
      button.textContent = label;
      button.addEventListener("click", async () => {
        if (action === "edit") {
          document.getElementById("scheduler-task-id").value = row.id;
          document.getElementById("scheduler-name").value = row.name || "";
          document.getElementById("scheduler-folder").value = (row.payload || {}).folder_path || "";
          document.getElementById("scheduler-interval").value = row.interval_seconds || 0;
          document.getElementById("scheduler-enabled").checked = Boolean(row.enabled);
          return;
        }
        const body = action === "activate"
          ? {action, note: "Owner approved activation from Orchestrator UI."}
          : action === "reject"
            ? {action, note: "Owner rejected activation from Orchestrator UI."}
            : {action};
        const result = await getJson(`/api/scheduler/tasks/${row.id}`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body),
        });
        document.getElementById("result").textContent = JSON.stringify(result, null, 2);
        await refreshAll();
      });
      group.appendChild(button);
    });
    actions.appendChild(group);
    tr.appendChild(actions);
    table.appendChild(tr);
  });
  el.appendChild(table);
}

function renderLegacyReview(rows) {
  const legacy = rows
    .filter(row => row.task_type === "legacy_claude_scheduled_task")
    .map(row => {
      const payload = row.payload || {};
      return {
        id: row.id,
        task: row.target_ref,
        review: row.review_state || "",
        enabled: row.enabled,
        risk: payload.risk_level || "",
        flags: (payload.risk_flags || []).join(", "),
        cron: payload.cron_expression || "",
        file: payload.file_path || "",
      };
    });
  renderRows("legacy-review", legacy.slice(0, 40), ["task", "review", "enabled", "risk", "flags", "cron", "file"]);
}

async function addSelection(kind, payload) {
  await getJson("/api/selections", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({kind, payload}),
  });
  await refreshAll();
}

async function refreshAll() {
  const [services, caps, auth, selections, approvals, quotaLedger, quotaState, repairQueue, dispatches, dispatchAttempts, standingOrders, schedulerTasks, workingMemory, auditLog, specStatus] = await Promise.all([
    getJson("/api/services"),
    getJson("/api/capabilities"),
    getJson("/api/auth-quota"),
    getJson("/api/selections"),
    getJson("/api/approvals"),
    getJson("/api/quota-ledger"),
    getJson("/api/quota-state"),
    getJson("/api/repair-queue"),
    getJson("/api/dispatches"),
    getJson("/api/dispatch-attempts"),
    getJson("/api/standing-orders"),
    getJson("/api/scheduler/tasks"),
    getJson("/api/working-memory"),
    getJson("/api/audit-log"),
    getJson("/api/spec-status"),
  ]);
  const [projects, activity] = await Promise.all([
    getJson("/api/projects"),
    getJson("/api/activity"),
  ]);
  renderSelectionRows(selections.slice(0, 50));
  renderApprovals(approvals.slice(0, 20));
  renderRows("services", services, ["name", "adapter_name", "protocol", "health_state"]);
  renderRows("capabilities", caps, ["adapter_name", "capability_id", "billing_class", "latency_band"]);
  renderAdapterProofOptions(caps);
  renderSpecStatus(specStatus);
  renderRows("projects", projects.slice(0, 40), ["name", "domain", "consequence_tier", "root_path"]);
  renderRows("quota-ledger", quotaLedger.slice(0, 20), ["provider", "units_consumed", "units_limit", "window_start"]);
  renderQuotaState(quotaState);
  renderRows("repair-queue", repairQueue.slice(0, 20), ["created_at", "failure_source", "failure_detail", "suggested_action"]);
  renderRows("activity", activity.slice(0, 50), ["ts", "kind", "action", "target"]);
  renderRows("dispatches", dispatches.slice(0, 30), ["started_at", "state", "adapter_name", "intent_id", "error"]);
  renderRows("dispatch-attempts", dispatchAttempts.slice(0, 50), ["started_at", "adapter_name", "attempt_number", "state", "error"]);
  renderRows("standing-orders", standingOrders.slice(0, 30), ["folder_path", "enabled", "last_fired_at"]);
  renderSchedulerTasks(schedulerTasks.slice(0, 30));
  renderLegacyReview(schedulerTasks);
  renderWorkingMemory(workingMemory.slice(0, 30));
  renderRows("audit-log", auditLog.slice(0, 50), ["ts", "actor", "action", "target"]);
  document.getElementById("auth").textContent = JSON.stringify(auth.github_copilot_summary || auth, null, 2);
}

document.getElementById("refresh").addEventListener("click", async () => {
  await getJson("/api/refresh", {method: "POST"});
  await refreshAll();
});

document.getElementById("repair-services").addEventListener("click", async () => {
  const result = await getJson("/api/repair-services", {method: "POST"});
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("retry-repairs").addEventListener("click", async () => {
  const result = await getJson("/api/retry-repairs", {method: "POST"});
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("notify-once").addEventListener("click", async () => {
  const result = await getJson("/api/notify-once", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({include_tray: false}),
  });
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("run-scheduler").addEventListener("click", async () => {
  const result = await getJson("/api/scheduler/run-once", {method: "POST"});
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("migrate-legacy-scheduler").addEventListener("click", async () => {
  const result = await getJson("/api/scheduler/migrate-legacy", {method: "POST"});
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("save-scheduler-task").addEventListener("click", async () => {
  const folder = document.getElementById("scheduler-folder").value.trim();
  if (!folder) return;
  const payload = {
    id: document.getElementById("scheduler-task-id").value.trim() || undefined,
    name: document.getElementById("scheduler-name").value.trim() || undefined,
    folder_path: folder,
    policy_yaml: "autopilot: enabled",
    interval_seconds: Number(document.getElementById("scheduler-interval").value || 0),
    enabled: document.getElementById("scheduler-enabled").checked,
  };
  const result = await getJson("/api/scheduler/tasks", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  document.getElementById("scheduler-task-id").value = "";
  await refreshAll();
});

document.getElementById("benchmark-latency").addEventListener("click", async () => {
  const result = await getJson("/api/benchmark-latency", {method: "POST"});
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("dispatch").addEventListener("click", async () => {
  const text = document.getElementById("intent").value;
  const result = await getJson("/api/dispatch", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text}),
  });
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("proof-adapter").addEventListener("change", async () => {
  const caps = await getJson("/api/capabilities");
  renderAdapterProofOptions(caps);
});

document.getElementById("prove-adapter").addEventListener("click", async () => {
  const payload = {
    adapter_name: document.getElementById("proof-adapter").value,
    capability: document.getElementById("proof-capability").value || undefined,
    prompt: document.getElementById("proof-prompt").value || "Adapter proof: answer with OK.",
    allow_subscription: document.getElementById("proof-allow-subscription").checked,
  };
  const result = await getJson("/api/prove-adapter", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

document.getElementById("add-selection").addEventListener("click", async () => {
  const rawPath = document.getElementById("selection-path").value.trim();
  const kind = document.getElementById("selection-kind").value;
  if (!rawPath) return;
  const key = kind === "url" ? "url" : kind === "text" ? "text" : "path";
  await addSelection(kind, {[key]: rawPath, source: "ui"});
  document.getElementById("selection-path").value = "";
});

document.getElementById("autopilot-scan").addEventListener("click", async () => {
  const root = document.getElementById("selection-path").value.trim();
  if (!root) return;
  const result = await getJson("/api/autopilot-scan", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({roots: [root]}),
  });
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refreshAll();
});

const dropzone = document.getElementById("dropzone");
["dragenter", "dragover"].forEach(name => {
  dropzone.addEventListener(name, event => {
    event.preventDefault();
    dropzone.classList.add("active");
  });
});
["dragleave", "drop"].forEach(name => {
  dropzone.addEventListener(name, event => {
    event.preventDefault();
    dropzone.classList.remove("active");
  });
});
dropzone.addEventListener("drop", async event => {
  const files = Array.from(event.dataTransfer.files || []);
  for (const file of files) {
    await addSelection("file", {name: file.name, size: file.size, source: "browser-drop"});
  }
});

refreshAll().catch(err => {
  document.getElementById("result").textContent = err.stack || String(err);
});
