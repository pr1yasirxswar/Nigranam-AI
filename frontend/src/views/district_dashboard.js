// Phase 10 (Implementation-Guide.md Phase 10 item 3, PRD.md S5.4) --
// District Authority dashboard. Four sections: Home, Suspicious Agencies
// (the network/graph analysis + reasoned suspicious-agency section --
// Phase 8 item 3's work, reused wholesale via GET /agencies/network),
// Projects (full district-wide list; clicking a work opens its detail,
// which already shows the FULL review thread -- reviews.py's visibility
// cascade means District already sees Local's conversation with the
// agency, so "Local<->Agency interaction history" needs no separate
// endpoint), and My Flags (second-tier review queue).

import { apiFetch } from "../api.js";
import { renderNetworkView } from "./network.js";
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
  const sanctioned = rows.reduce((s, r) => s + (Number(r.sanctioned_amount) || 0), 0);
  const flagged = rows.filter((r) => r.tier).length;
  const critHigh = rows.filter((r) => r.tier === "Critical" || r.tier === "High").length;

  panel.innerHTML = `
    <p class="subtle">Jurisdiction: ${me.jurisdiction.district}</p>
    ${renderStatCards([
      { value: rows.length, label: "Total works" },
      { value: formatCrore(sanctioned), label: "Sanctioned amount" },
      { value: flagged, label: "Flagged by AI" },
      { value: critHigh, label: "Critical / High" },
    ])}
  `;
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

// Phase 11 item 3 -- "District Authority action 'assign work to agency'
// generates a Work ID + password pair" (POST /agencies/issue-credentials).
// Simple form + a one-time result box: the password is shown exactly
// once, matching auth/agency_auth.py's "never displays it again" design.
function renderIssueCredentials(panel, me) {
  panel.innerHTML = `
    <p class="subtle">Assign work to an implementing agency in ${me.jurisdiction.district}. This generates a new
    Work ID + password for that agency to log in with -- shown once below, so relay it to the agency directly.</p>
    <div class="verify-box">
      <input type="text" id="issue-agency-name" placeholder="Executing agency (e.g. PWD, Rural Engineering Services)" />
      <input type="text" id="issue-display-name" placeholder="Display name (optional)" />
      <button id="issue-submit" class="primary-button">Generate credentials</button>
    </div>
    <div id="issue-result"></div>
  `;

  panel.querySelector("#issue-submit").addEventListener("click", async () => {
    const executingAgency = panel.querySelector("#issue-agency-name").value.trim();
    const displayName = panel.querySelector("#issue-display-name").value.trim();
    const resultEl = panel.querySelector("#issue-result");
    if (!executingAgency) {
      resultEl.innerHTML = `<p class="error">Executing agency is required.</p>`;
      return;
    }
    resultEl.innerHTML = `<p><em>Generating...</em></p>`;
    try {
      const res = await apiFetch("/agencies/issue-credentials", {
        method: "POST",
        body: JSON.stringify({ executing_agency: executingAgency, display_name: displayName || null }),
      });
      resultEl.innerHTML = `
        <div class="stat-cards">
          <div class="stat-card"><div class="stat-value">${res.work_id}</div><div class="stat-label">Work ID</div></div>
          <div class="stat-card"><div class="stat-value">${res.password}</div><div class="stat-label">Password (shown once)</div></div>
        </div>
        <p class="subtle">${res.note}</p>
      `;
    } catch (err) {
      resultEl.innerHTML = `<p class="error">${err.message}</p>`;
    }
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
    panel.innerHTML = `<p><em>Nothing escalated to District Authority right now.</em></p>`;
    return;
  }
  panel.innerHTML = `
    <p class="subtle">Flags currently escalated to District Authority review.</p>
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

export async function renderDistrictDashboard(container, me) {
  container.innerHTML = `<p><em>Loading your dashboard...</em></p>`;
  let rows;
  try {
    rows = await fetchScopedRiskRows("/projects/mine");
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load dashboard: ${err.message}</p>`;
    return;
  }

  container.innerHTML = `<h2>District Authority Dashboard -- ${me.name}</h2>`;
  const tabRoot = document.createElement("div");
  container.appendChild(tabRoot);

  renderDashboardTabs(tabRoot, [
    { key: "home", label: "Home", render: (p) => renderHome(p, me, rows) },
    { key: "analysis", label: "Analysis", render: (p) => renderRiskAnalysisSection(p, rows) },
    { key: "network", label: "Suspicious Agencies", render: (p) => renderNetworkView(p) },
    { key: "projects", label: "Projects", render: (p) => renderProjects(p, rows, me) },
    { key: "flags", label: "My Flags", render: (p) => renderMyFlags(p, me) },
    { key: "issue-agency", label: "Assign Work to Agency", render: (p) => renderIssueCredentials(p, me) },
  ]);
}
