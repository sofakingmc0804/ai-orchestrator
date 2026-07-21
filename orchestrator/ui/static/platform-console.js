(function () {
  const endpoint = document.querySelector("main")?.dataset.endpoint || "/api/platform-console";
  const number = (value) => Number(value || 0).toLocaleString();
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  }

  function renderList(id, rows, render, empty) {
    const node = document.getElementById(id);
    if (!node) return;
    node.innerHTML = rows.length ? rows.map(render).join("") : `<li>${esc(empty)}</li>`;
  }

  function render(payload) {
    const fleet = payload.service_summary || {};
    const meter = payload.metering || [];
    const models = payload.model_cards || [];
    const repairs = payload.repair_summary || {};
    const receipts = payload.receipt_summary || {};
    const totalTokens = meter.reduce((total, row) => total + Number(row.tokens_total || 0), 0);
    setText("fleetHealthy", `${number(fleet.healthy)} / ${number(fleet.total)}`);
    setText("fleetDetail", `${number(fleet.non_healthy)} non-healthy services`);
    setText("tokenTotal", number(totalTokens));
    setText("meterDetail", `${number(meter.length)} provider/model routes with source-labeled quota`);
    setText("repairTotal", number(repairs.open_count));
    setText("receiptDetail", `${number(receipts.successful)} successful of ${number(receipts.total)} aggregate receipts`);
    renderList("meterList", meter.slice(0, 8), (row) => `<li><span>${esc(row.provider)} / ${esc(row.model)}</span><span class="right">${number(row.tokens_total)} tokens · ${row.actual_cost_usd == null ? "cost unknown" : `$${Number(row.actual_cost_usd).toFixed(4)}`} · quota ${esc((row.quota_sources || ["unknown"]).join(", "))} · ${esc((row.measurement_sources || ["unknown"]).join(", "))}</span></li>`, "No resource events recorded yet.");
    renderList("modelList", models.slice(0, 5), (row) => {
      const evidence = row.specialization_evidence || {};
      const score = evidence.average_score == null ? "no scored episodes" : `score ${Number(evidence.average_score).toFixed(2)}`;
      return `<li><span>${esc(row.model)}</span><span class="right">${number(evidence.episode_count)} episodes · ${score}</span></li>`;
    }, "No model evidence recorded yet.");
    setText("consoleStatus", "Updated from the system-only resource ledger.");
  }

  async function load() {
    try {
      const response = await fetch(endpoint, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || payload.scope !== "system") throw new Error("System-only console payload unavailable.");
      render(payload);
    } catch (error) {
      const status = document.getElementById("consoleStatus");
      if (status) { status.className = "notice error"; status.textContent = error.message || String(error); }
    }
  }

  load();
  window.setInterval(load, 15000);
})();
