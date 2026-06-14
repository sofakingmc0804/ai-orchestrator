(function () {
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
      },
      null,
      2,
    );
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
      } catch (error) {
        result.textContent = error.stack || String(error);
      } finally {
        submit.disabled = false;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", bindRoutePanel);
})();
