// Phase 10 (Implementation-Guide.md Phase 10 item 4, PRD.md S5.5) --
// Nodal State Authority dashboard. Seven sections: Home, Network
// (all-districts graph analysis + reasoned suspicious-agency section,
// reusing GET /agencies/network wholesale), District Drill-down
// (per-district flagged counts, risk-descending), Highest-Risk (state-wide
// top-risk works), Collusion Alerts (Phase 9 -- folded in here per
// Implementation-Guide.md Phase 9 item 5's own note that this belonged in
// a future state_dashboard.js), Projects (full state-wide list), and My
// Flags (third-tier review queue).

import { apiFetch } from "../api.js";
import { renderNetworkView } from "./network.js";
import { renderCollusionView } from "./collusion.js";
import { renderBarChart } from "./shared/charts.js";
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

const TIER_RANK = { Critical: 4, High: 3, Medium: 2, Low: 1, Normal: 0 };

async function renderHome(panel, me, rows) {
  const sanctioned = rows.reduce((s, r) => s + (Number(r.sanctioned_amount) || 0), 0);
  const flagged = rows.filter((r) => r.tier).length;
  const critHigh = rows.filter((r) => r.tier === "Critical" || r.tier === "High").length;

  panel.innerHTML = `
    <p class="subtle">Jurisdiction: ${me.jurisdiction.state}</p>
    ${renderStatCards([
      { value: rows.length, label: "Total works (statewide)" },
      { value: formatCrore(sanctioned), label: "Sanctioned amount" },
      { value: flagged, label: "Flagged by AI" },
      { value: critHigh, label: "Critical / High" },
    ])}
  `;
}

function renderDistrictDrilldown(panel, rows, me) {
  const byDistrict = {};
  for (const r of rows) {
    const d = r.implementing_district ?? "Unknown";
    const entry = (byDistrict[d] ??= { district: d, total: 0, flagged: 0 });
    entry.total += 1;
    if (r.tier) entry.flagged += 1;
  }
  const districts = Object.values(byDistrict).sort((a, b) => b.flagged - a.flagged);

  panel.innerHTML = `
    <p class="subtle">Per-district flagged counts across ${me.jurisdiction.state}, highest first.</p>
    <div id="district-chart"></div>
    <ul class="drilldown-list">
      ${districts
        .map(
          (d) => `<li class="drilldown-item" data-district="${d.district}">
            <div class="drilldown-title">${d.district}</div>
            <div class="subtle">${d.flagged} flagged of ${d.total} works</div>
          </li>`
        )
        .join("")}
    </ul>
    <div id="district-detail"></div>
  `;
  renderBarChart(
    panel.querySelector("#district-chart"),
    districts.map((d) => ({ label: d.district, value: d.flagged, color: "#d9730d" }))
  );
  panel.querySelectorAll(".drilldown-item").forEach((li) => {
    li.addEventListener("click", () => {
      const detailEl = panel.querySelector("#district-detail");
      const districtRows = rows.filter((r) => r.implementing_district === li.dataset.district);
      detailEl.innerHTML = `<h3>${li.dataset.district}</h3>`;
      const sub = document.createElement("div");
      detailEl.appendChild(sub);
      renderProjectListSection(sub, districtRows, {
        onRowClick: (workId) => {
          const wd = document.createElement("div");
          detailEl.appendChild(wd);
          openWorkDetail(wd, workId, me, () => renderDistrictDrilldown(panel, rows, me));
        },
      });
    });
  });
}

function renderHighestRisk(panel, rows, me) {
  const flagged = rows
    .filter((r) => r.tier)
    .sort((a, b) => (TIER_RANK[b.tier] ?? -1) - (TIER_RANK[a.tier] ?? -1))
    .slice(0, 25);

  if (flagged.length === 0) {
    panel.innerHTML = `<p><em>No flagged works statewide yet.</em></p>`;
    return;
  }
  panel.innerHTML = `
    <p class="subtle">Top ${flagged.length} highest-risk works statewide.</p>
    <ul class="work-list">
      ${flagged
        .map(
          (r) => `<li data-work-id="${r.work_id}" class="work-item clickable">
            ${tierBadge(r)} ${r.work_id} <span class="subtle">(${r.implementing_district ?? ""})</span>
          </li>`
        )
        .join("")}
    </ul>
    <div id="risk-detail"></div>
  `;
  panel.querySelectorAll(".work-item").forEach((li) => {
    li.addEventListener("click", () => {
      openWorkDetail(panel.querySelector("#risk-detail"), li.dataset.workId, me, () => renderHighestRisk(panel, rows, me));
    });
  });
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
    panel.innerHTML = `<p><em>Nothing escalated to Nodal State Authority right now.</em></p>`;
    return;
  }
  panel.innerHTML = `
    <p class="subtle">Flags currently escalated to State Authority review.</p>
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

export async function renderStateDashboard(container, me) {
  container.innerHTML = `<p><em>Loading your dashboard...</em></p>`;
  let rows;
  try {
    rows = await fetchScopedRiskRows("/projects/mine");
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load dashboard: ${err.message}</p>`;
    return;
  }

  container.innerHTML = `<h2>Nodal State Authority Dashboard -- ${me.name}</h2>`;
  const tabRoot = document.createElement("div");
  container.appendChild(tabRoot);

  renderDashboardTabs(tabRoot, [
    { key: "home", label: "Home", render: (p) => renderHome(p, me, rows) },
    { key: "analysis", label: "Analysis", render: (p) => renderRiskAnalysisSection(p, rows) },
    { key: "network", label: "Network / Suspicious Agencies", render: (p) => renderNetworkView(p) },
    { key: "districts", label: "District Drill-down", render: (p) => renderDistrictDrilldown(p, rows, me) },
    { key: "highrisk", label: "Highest-Risk", render: (p) => renderHighestRisk(p, rows, me) },
    { key: "collusion", label: "Collusion Alerts", render: (p) => renderCollusionView(p) },
    { key: "projects", label: "Projects", render: (p) => renderProjects(p, rows, me) },
    { key: "flags", label: "My Flags", render: (p) => renderMyFlags(p, me) },
  ]);
}
