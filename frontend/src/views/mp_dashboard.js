// Phase 10 (Implementation-Guide.md Phase 10 items 5-6, PRD.md S5.6) --
// the MP role gets TWO dashboards, switched via a top-level sub-tab
// (Build-Log.md #18: "two separate dashboards: one a-to-z own-constituency
// view, one cross-MP risk-comparison view"):
//
//   MP Dashboard 1 -- own constituency, apex/a-to-z access: Home (summary
//     + a national-context line), category-count cards (click-to-filter),
//     a district drill-down, Suspicious Agencies (network), My Flags
//     (apex review queue -- final tier, PRD.md S3).
//
//   MP Dashboard 2 -- cross-MP risk comparison, backed by the new Phase 10
//     endpoint GET /mp/compare (routers/mp_compare.py).
//
// SCOPING DESIGN DECISION FLAGGED (not spelled out in PRD.md, same pattern
// routers/agencies.py already uses for its own flagged scoping calls):
// Implementation-Guide.md's Phase 10 item 5 describes MP Dashboard 1's
// drill-down as "state->district ... with full a-to-z access", but
// auth/permissions.py's ROLE_MP jurisdiction is scoped to the caller's own
// CONSTITUENCY only (a constituency can span more than one
// implementing_district, per Demo-Credentials.md/seed_users.py -- it does
// NOT span other states or other MPs' constituencies). "A-to-z access"
// is read here as "full detail/reasoning visibility over every work in
// their own constituency" (as opposed to a restricted field set), not as
// state-wide or cross-constituency access -- granting the latter would
// contradict Rules.md's jurisdiction-filtering rule and Phase 3's already-
// validated cross-MP 403 check. So the drill-down below is BY DISTRICT,
// scoped to the districts that make up the caller's own constituency, and
// nothing wider. Flagging this for the team to confirm before the pitch.

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

  let national = null;
  try {
    national = await apiFetch("/stats/public-summary");
  } catch {
    // National context is a nice-to-have overlay, not required for this
    // dashboard's own jurisdiction-scoped figures -- fail quietly.
  }

  panel.innerHTML = `
    <p class="subtle">Constituency: ${me.jurisdiction.constituency}</p>
    ${renderStatCards([
      { value: rows.length, label: "Works in your constituency" },
      { value: formatCrore(sanctioned), label: "Sanctioned amount" },
      { value: flagged, label: "Flagged by AI" },
    ])}
    ${
      national
        ? `<p class="subtle" style="margin-top:0.75rem;">For national context: ${national.total_works} works
           and ${formatCrore(national.sanctioned_amount_total)} sanctioned across all of MPLADS.</p>`
        : ""
    }
  `;
}

function renderCategories(panel, rows, me) {
  const byCategory = {};
  for (const r of rows) {
    const c = r.work_category ?? "Uncategorized";
    byCategory[c] = (byCategory[c] || 0) + 1;
  }
  const categories = Object.entries(byCategory).sort((a, b) => b[1] - a[1]);

  panel.innerHTML = `
    <p class="subtle">Click a category to filter your constituency's project list.</p>
    <div class="category-cards">
      ${categories
        .map(
          ([cat, count]) => `<div class="category-card" data-category="${cat}">
            <div class="stat-value">${count}</div><div class="stat-label">${cat}</div>
          </div>`
        )
        .join("")}
    </div>
    <div id="category-results"></div>
  `;
  panel.querySelectorAll(".category-card").forEach((card) => {
    card.addEventListener("click", () => {
      panel.querySelectorAll(".category-card").forEach((c) => c.classList.remove("active"));
      card.classList.add("active");
      const filtered = rows.filter((r) => (r.work_category ?? "Uncategorized") === card.dataset.category);
      const resultsEl = panel.querySelector("#category-results");
      resultsEl.innerHTML = "";
      renderProjectListSection(resultsEl, filtered, {
        onRowClick: (workId) => {
          const detailEl = document.createElement("div");
          resultsEl.appendChild(detailEl);
          openWorkDetail(detailEl, workId, me, () => card.click());
        },
      });
    });
  });
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
    <p class="subtle">Districts making up ${me.jurisdiction.constituency} -- full detail access, scoped to your
    own constituency only.</p>
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
    panel.innerHTML = `<p><em>Nothing escalated to your apex review right now.</em></p>`;
    return;
  }
  panel.innerHTML = `
    <p class="subtle">Flags escalated to the apex (MP) tier -- everything Local/District/State couldn't resolve.</p>
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

async function renderOwnConstituencyDashboard(panel, me) {
  panel.innerHTML = `<p><em>Loading your constituency...</em></p>`;
  let rows;
  try {
    rows = await fetchScopedRiskRows("/projects/mine");
  } catch (err) {
    panel.innerHTML = `<p class="error">Could not load dashboard: ${err.message}</p>`;
    return;
  }
  const tabRoot = document.createElement("div");
  panel.innerHTML = "";
  panel.appendChild(tabRoot);
  renderDashboardTabs(tabRoot, [
    { key: "home", label: "Home", render: (p) => renderHome(p, me, rows) },
    { key: "analysis", label: "Analysis", render: (p) => renderRiskAnalysisSection(p, rows) },
    { key: "categories", label: "Categories", render: (p) => renderCategories(p, rows, me) },
    { key: "districts", label: "Districts", render: (p) => renderDistrictDrilldown(p, rows, me) },
    { key: "network", label: "Suspicious Agencies", render: (p) => renderNetworkView(p) },
    { key: "flags", label: "My Flags", render: (p) => renderMyFlags(p, me) },
  ]);
}

async function renderCrossMpComparison(panel) {
  panel.innerHTML = `<p><em>Loading cross-MP comparison...</em></p>`;
  let rows;
  try {
    rows = await apiFetch("/mp/compare");
  } catch (err) {
    panel.innerHTML = `<p class="error">Could not load comparison: ${err.message}</p>`;
    return;
  }

  panel.innerHTML = `
    <p class="subtle">Every constituency's risk profile, side by side (aggregate counts only -- no individual
    work IDs, review text, or reviewer identity).</p>
    <table class="data-table">
      <thead>
        <tr>
          <th>Constituency</th><th>State</th><th>Total works</th><th>Sanctioned</th>
          <th>Flagged</th><th>Critical/High</th><th>Risk rate</th><th>Resolution rate</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (r) => `
          <tr>
            <td>${r.constituency}</td>
            <td>${r.state ?? ""}</td>
            <td>${r.total_works}</td>
            <td>${formatCrore(r.sanctioned_amount_total)}</td>
            <td>${r.flagged_count}</td>
            <td>${r.critical_high_count}</td>
            <td>${(r.risk_rate * 100).toFixed(1)}%</td>
            <td>${r.resolution_rate != null ? (r.resolution_rate * 100).toFixed(1) + "%" : "—"}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>
  `;
}

export async function renderMpDashboard(container, me) {
  container.innerHTML = `
    <h2>MP Dashboard -- ${me.name}</h2>
    <div class="sub-tabs">
      <button class="sub-tab active" data-tab="own">My Constituency</button>
      <button class="sub-tab" data-tab="compare">Compare Constituencies</button>
    </div>
    <div id="mp-sub-panel"></div>
  `;
  const subPanel = container.querySelector("#mp-sub-panel");
  const subButtons = [...container.querySelectorAll(".sub-tab")];

  function activate(tab) {
    subButtons.forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    if (tab === "own") renderOwnConstituencyDashboard(subPanel, me);
    else renderCrossMpComparison(subPanel);
  }
  subButtons.forEach((b) => b.addEventListener("click", () => activate(b.dataset.tab)));
  activate("own");
}
