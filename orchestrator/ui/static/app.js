(function () {
  async function getJson(url) {
    const response = await fetch(url);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || `${response.status} ${response.statusText}`);
    }
    return body;
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || `${response.status} ${response.statusText}`);
    }
    return body;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function fmtNumber(value) {
    if (value == null || value === "") return "--";
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString() : esc(value);
  }

  function reserveClass(ratio) {
    if (ratio == null || Number.isNaN(Number(ratio))) return "warn";
    if (Number(ratio) <= 0.2) return "bad";
    if (Number(ratio) <= 0.35) return "warn";
    return "good";
  }

  function renderRoute(payload) {
    const decision = payload.decision || {};
    const chosen = decision.chosen_adapter || "none";
    const candidates = decision.candidates_considered || [];
    const top = candidates[0] || {};
    return JSON.stringify(
      {
        state: payload.state,
        job_class: payload.job_class,
        chosen_adapter: chosen,
        worker_id: top.worker_id || null,
        score: top.composite_score || null,
        budget_source: top.budget_source || null,
        quality_source: top.quality_source || null,
        reasoning: decision.reasoning || "",
        error: payload.error || decision.error || null,
      },
      null,
      2,
    );
  }

  function renderRouteLadder(payload) {
    const list = document.getElementById("routeLadderList");
    if (!list) return;
    const rows = payload.ranked_ladder || (payload.decision || {}).candidates_considered || [];
    if (!rows.length) {
      const detail = payload.error ? `<div class="meter-detail">${esc(payload.error)}</div>` : "";
      list.innerHTML = `<li class="meter-item"><div><div class="meter-name">No route ladder available</div>${detail}</div></li>`;
      return;
    }
    list.innerHTML = rows.slice(0, 6).map((row, index) => {
      const budget = row.budget_score == null ? "--" : Number(row.budget_score).toFixed(2);
      const quality = row.measured_quality_score == null ? "--" : Number(row.measured_quality_score).toFixed(2);
      return `
        <li class="meter-item">
          <div>
            <div class="meter-name">${index + 1}. ${esc(row.worker_id || row.adapter_name || "worker")}</div>
            <div class="meter-detail">health ${esc(row.health_state || "unknown")}, quota ${budget}, quality ${quality}, ${esc(row.contract_type || "unknown")}</div>
          </div>
          <div class="meter-value">${fmtNumber(row.composite_score == null ? row.score : row.composite_score)}</div>
        </li>
      `;
    }).join("");
  }

  function bindRoutePanel() {
    const text = document.getElementById("routeText");
    const jobClass = document.getElementById("routeJobClass");
    const submit = document.getElementById("routeSubmit");
    const result = document.getElementById("routeResult");
    if (!text || !jobClass || !submit || !result) return;

    submit.addEventListener("click", async () => {
      const directive = text.value.trim();
      if (!directive) {
        result.textContent = "No directive supplied.";
        return;
      }
      submit.disabled = true;
      result.textContent = "Routing...";
      try {
        const payload = {
          text: directive,
          job_class: jobClass.value || null,
        };
        const route = await postJson("/api/route", payload);
        result.textContent = renderRoute(route);
        renderRouteLadder(route);
      } catch (error) {
        result.textContent = error.stack || String(error);
      } finally {
        submit.disabled = false;
      }
    });
  }

  async function loadRouteJobClasses() {
    const jobClass = document.getElementById("routeJobClass");
    if (!jobClass) return;
    const payload = await getJson("/api/job-classes");
    const values = Array.isArray(payload.job_classes) ? payload.job_classes : [];
    const selected = jobClass.value;
    jobClass.innerHTML = `<option value="">Auto</option>${values.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("")}`;
    if (values.includes(selected)) jobClass.value = selected;
  }

  function renderQuota(payload) {
    const list = document.getElementById("quotaTrafficList");
    if (!list) return;
    const rows = ((payload.subscriptions || {}).subscriptions || []).slice(0, 8);
    if (!rows.length) {
      list.innerHTML = `<li class="meter-item"><div><div class="meter-name">No live subscription snapshots</div><div class="meter-detail">Run subscription refresh to prove quota.</div></div></li>`;
      return;
    }
    list.innerHTML = rows.map((row) => {
      const limit = Number(row.tokens_limit || 0);
      const remaining = Number(row.tokens_remaining || 0);
      const ratio = limit > 0 ? remaining / limit : null;
      const pct = ratio == null ? "--" : `${Math.round(ratio * 100)}%`;
      return `
        <li class="meter-item">
          <div>
            <div class="meter-name">${esc(row.subscription_name || row.service_id || "subscription")}</div>
            <div class="meter-detail">${fmtNumber(row.tokens_used_by_app || 0)} app tokens, ${esc(row.confidence || "unknown")} proof</div>
          </div>
          <div class="meter-value ${reserveClass(ratio)}">${pct}</div>
        </li>
      `;
    }).join("");
  }

  function renderQuality(payload) {
    const list = document.getElementById("qualityLeaderboardList");
    if (!list) return;
    const rows = [];
    for (const board of payload.leaderboards || []) {
      for (const worker of (board.workers || []).slice(0, 3)) {
        rows.push({domain: board.operation_domain, ...worker});
      }
    }
    if (!rows.length) {
      list.innerHTML = `<li class="meter-item"><div><div class="meter-name">No live comparative scores</div><div class="meter-detail">Run an operation tournament to rank workers.</div></div></li>`;
      return;
    }
    list.innerHTML = rows.slice(0, 8).map((row) => `
      <li class="meter-item">
        <div>
          <div class="meter-name">${esc(row.domain)}: ${esc(row.worker_id)}</div>
          <div class="meter-detail">${fmtNumber(row.samples)} live samples, latest ${esc(row.latest_dispatch_id || "--")}</div>
        </div>
        <div class="meter-value">${Number(row.avg_quality_score || 0).toFixed(2)}</div>
      </li>
    `).join("");
  }

  function renderFailover(payload) {
    const ladder = document.getElementById("failoverLadderList");
    const events = document.getElementById("failoverEventsList");
    if (ladder) {
      const order = payload.ladder_order || [];
      ladder.innerHTML = order.map((name, index) => `
        <li class="meter-item">
          <div>
            <div class="meter-name">${index + 1}. ${esc(name.replaceAll("_", " "))}</div>
          </div>
        </li>
      `).join("");
    }
    if (!events) return;
    const rows = payload.recent_failover_events || [];
    if (!rows.length) {
      events.innerHTML = `<li class="meter-item"><div><div class="meter-name">No recent failover events</div></div></li>`;
      return;
    }
    events.innerHTML = rows.slice(0, 5).map((row) => `
      <li class="meter-item">
        <div>
          <div class="meter-name">${esc(row.dispatch_id)}</div>
          <div class="meter-detail">${fmtNumber(row.failed_attempts)} failed of ${fmtNumber(row.attempts)} attempts, final ${esc(row.final_state || "unknown")}</div>
        </div>
        <div class="meter-value">${fmtNumber((row.adapters || []).length)}</div>
      </li>
    `).join("");
  }

  function renderGovernance(payload) {
    const list = document.getElementById("governanceReceiptList");
    if (!list) return;
    const rows = payload.receipts || [];
    if (!rows.length) {
      list.innerHTML = `<li class="meter-item"><div><div class="meter-name">No governance receipts recorded</div></div></li>`;
      return;
    }
    list.innerHTML = rows.slice(0, 6).map((row) => `
      <li class="meter-item">
        <div>
          <div class="meter-name">${esc(row.hook_event_name || "hook")} / ${esc(row.decision || "decision")}</div>
          <div class="meter-detail">${esc(row.terminal_state_requirement || "terminal state unspecified")}</div>
        </div>
        <div class="meter-value">${esc(row.confirmation_state || "n/a")}</div>
      </li>
    `).join("");
  }

  async function loadLivingDashboard() {
    const jobs = [
      getJson("/api/budget").then(renderQuota),
      getJson("/api/quality-leaderboard").then(renderQuality),
      getJson("/api/failover-events").then(renderFailover),
      getJson("/api/governance-receipts").then(renderGovernance),
    ];
    await Promise.allSettled(jobs);
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindRoutePanel();
    loadRouteJobClasses().catch(() => {});
    loadLivingDashboard();
    setInterval(loadLivingDashboard, 60000);
  });
})();
