// Phase 10 (Implementation-Guide.md Phase 10 item 2, PRD.md S5.3) --
// Local Authority dashboard. Four sections: Home (summary), Area
// Analysis, Projects (Completed/Ongoing, sortable, risk-ordered), and My
// Flags (first-line review queue -- click a work to open its full review
// detail: tier, escalation track, thread, comment/clear/escalate).
//
// Everything here reads from server-side, jurisdiction-scoped endpoints
// only (GET /projects/mine, GET /flags/mine, both already filtered on the
// (district, loc) compound key by auth/permissions.py -- Rules.md). The
// tier/driving-signal enrichment on top of /projects/mine is a client-side
// join against ALREADY-PUBLIC data (GET /flags/public), same pattern
// registry.js used in Phase 5 -- see dashboard_kit.js's
// fetchScopedRiskRows() docstring.

import { apiFetch } from "../api.js";
import {
  formatCrore,
  renderStatCards,
  renderDashboardTabs,
  renderProjectListSection,
  renderRiskAnalysisSection,
  fetchScopedRiskRows,
  openWorkDetail,
  tierBadge,
} from "./shared/dashboard_kit.js";

async function renderHome(panel, me, rows) {
  const total = rows.length;
  const sanctioned = rows.reduce((s, r) => s + (Number(r.sanctioned_amount) || 0), 0);
  const flagged = rows.filter((r) => r.tier).length;
  const completed = rows.filter((r) => r.status === "Completed").length;

  panel.innerHTML = `
    <p class="subtle">Jurisdiction: ${me.jurisdiction.district}, Ward/Block: ${me.jurisdiction.loc}</p>
    ${renderStatCards([
      { value: total, label: "Works in your area" },
      { value: formatCrore(sanctioned), label: "Sanctioned amount" },
      { value: flagged, label: "Flagged by AI" },
      { value: completed, label: "Completed" },
    ])}
  `;
}

async function renderAreaAnalysis(panel, rows) {
  renderRiskAnalysisSection(panel, rows);
}

function renderProjects(panel, rows, me) {
  renderProjectListSection(panel, rows, {
    onRowClick: (workId) => {
      const detailEl = document.createElement("div");
      panel.appendChild(detailEl);
      openWorkDetail(detailEl, workId, me, () => renderProjects(panel, rows, me));
    },
  });
}

async function renderMyFlags(panel, me) {
  panel.innerHTML = `<p><em>Loading your flags...</em></p>`;
  let myFlags;
  try {
    myFlags = await apiFetch("/flags/mine");
  } catch (err) {
    panel.innerHTML = `<p class="error">Could not load flags: ${err.message}</p>`;
    return;
  }
  if (myFlags.length === 0) {
    panel.innerHTML = `<p><em>Nothing waiting on your review right now.</em></p>`;
    return;
  }
  panel.innerHTML = `
    <p class="subtle">Flags currently awaiting first-line (Local Authority) review.</p>
    <ul class="work-list">
      ${myFlags
        .map((f) => `<li data-work-id="${f.work_id}" class="work-item clickable">${tierBadge(f)} ${f.work_id}</li>`)
        .join("")}
    </ul>
    <div id="flag-detail"></div>
  `;
  panel.querySelectorAll(".work-item").forEach((li) => {
    li.addEventListener("click", () => {
      openWorkDetail(panel.querySelector("#flag-detail"), li.dataset.workId, me, () => renderMyFlags(panel, me));
    });
  });
}

export async function renderLocalDashboard(container, me) {
  container.innerHTML = `<p><em>Loading your dashboard...</em></p>`;
  let rows;
  try {
    rows = await fetchScopedRiskRows("/projects/mine");
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load dashboard: ${err.message}</p>`;
    return;
  }

  container.innerHTML = `<h2>Local Authority Dashboard -- ${me.name}</h2>`;
  const tabRoot = document.createElement("div");
  container.appendChild(tabRoot);

  renderDashboardTabs(tabRoot, [
    { key: "home", label: "Home", render: (p) => renderHome(p, me, rows) },
    { key: "area", label: "Area Analysis", render: (p) => renderAreaAnalysis(p, rows) },
    { key: "projects", label: "Projects", render: (p) => renderProjects(p, rows, me) },
    { key: "flags", label: "My Flags", render: (p) => renderMyFlags(p, me) },
  ]);
}
