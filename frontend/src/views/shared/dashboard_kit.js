// Phase 10 (Implementation-Guide.md Phase 10) -- shared building blocks for
// the six per-role dashboards (agency/local/district/state/mp). Every
// PRD.md S5.2-S5.6 dashboard needs the same handful of pieces (a stat-card
// home summary, a sortable/filterable project list with a tier badge, a
// tabbed section layout, and a "click a work -> see its full review
// detail" panel) -- this file is that shared logic, so five dashboard
// files don't each reimplement it slightly differently. Follows the exact
// tier-badge / stat-card / data-table conventions dashboard.js/registry.js
// already established in Phase 5, rather than inventing new markup
// patterns.

import { apiFetch } from "../../api.js";
import { renderWorkDetail } from "../review.js";
import { renderBarChart, renderPieChart } from "./charts.js";

export function formatCrore(amount) {
  if (amount == null) return "—";
  return `₹${(amount / 1e7).toFixed(2)} Crore`;
}

// Same colors style.css already defines for .tier-* pill classes -- kept
// as one shared map so every chart's slice/bar color always matches what
// the tier pill elsewhere on the same page looks like.
export const TIER_COLORS = {
  Critical: "#b3261e",
  High: "#d9730d",
  Medium: "#c9a227",
  Low: "#4a7c59",
  Normal: "#8a8a8a",
  Clear: "#2c5c37",
  "Pending analysis": "#a9c8e0",
};

const STATUS_COLORS = {
  Completed: "#2c5c37",
  "In Progress": "#2f6db3",
  Sanctioned: "#c9a227",
  Recommended: "#8a8a8a",
  Rejected: "#b3261e",
};

/**
 * Phase 12 -- "graphical analysis": every dashboard's Analysis/Area
 * Analysis tab used to render tier/status breakdowns as stat-cards
 * (numbers in boxes) only, never an actual chart -- this is the shared
 * section every dashboard's tab now calls instead, so risk-tier and
 * status breakdowns look the same (and stay in sync) everywhere they
 * appear. Computed client-side from `rows` (already jurisdiction-scoped
 * by the server, per dashboard_kit.js's own fetchScopedRiskRows()) --
 * never re-fetches a separate nationwide stats endpoint, so a State/MP/
 * District view never risks pulling in another jurisdiction's numbers.
 */
export function renderRiskAnalysisSection(panel, rows) {
  const tierCounts = {};
  for (const r of rows) {
    const key = r.tier ?? (r.analyzed ? "Clear" : "Pending analysis");
    tierCounts[key] = (tierCounts[key] || 0) + 1;
  }
  const statusCounts = {};
  for (const r of rows) statusCounts[r.status ?? "Unknown"] = (statusCounts[r.status ?? "Unknown"] || 0) + 1;

  const tierOrder = ["Critical", "High", "Medium", "Low", "Normal", "Clear", "Pending analysis"];
  const tierData = tierOrder
    .filter((k) => tierCounts[k])
    .map((k) => ({ label: k, value: tierCounts[k], color: TIER_COLORS[k] || "#8a8a8a" }));

  const statusData = Object.entries(statusCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([label, value]) => ({ label, value, color: STATUS_COLORS[label] || "#2f6db3" }));

  panel.innerHTML = `
    <h3>Risk breakdown</h3>
    <div id="ra-pie"></div>
    <h3>By status</h3>
    <div id="ra-bar"></div>
  `;
  renderPieChart(panel.querySelector("#ra-pie"), tierData);
  renderBarChart(panel.querySelector("#ra-bar"), statusData);
}

export function tierBadge(row) {
  if (row.tier) return `<span class="tier-pill tier-${row.tier.toLowerCase()}">${row.tier}</span>`;
  if (row.analyzed) return `<span class="tier-pill tier-clear">Clear</span>`;
  return `<span class="tier-pill tier-pending">Pending analysis</span>`;
}

export function renderStatCards(cards) {
  return `
    <div class="stat-cards">
      ${cards
        .map(
          (c) => `<div class="stat-card"><div class="stat-value">${c.value}</div><div class="stat-label">${c.label}</div></div>`
        )
        .join("")}
    </div>`;
}

// Stage 3 -- the 6 summary cards match the official eSAKSHI portal's
// public dashboard exactly (label + figure). Every role's Home now shows
// these same national figures instead of its own jurisdiction-computed
// stat cards, per the user's explicit request that every home page show
// this same data. Single source of truth, shared by dashboard.js (public
// homepage) and all 5 role dashboards below.
export const ESAKSHI_SUMMARY_CARDS = [
  { label: "Allocated Limit for Hon'ble MPs", value: "₹ 8,349.39 Crore" },
  { label: "Amount consented for Calamity", value: "₹ 4.06 Crore" },
  { label: "Works Recommended", value: "No. 110562", sub: "₹ 5,927.53 Crore" },
  { label: "Works Sanctioned", value: "No. 82603", sub: "₹ 4,361.48 Crore" },
  { label: "Works Completed", value: "No. 36131", sub: "₹ 1,776.58 Crore" },
  { label: "Expenditure on Completed and On-going Works as on Date", value: "₹ 2,895.71 Crore" },
];

export function renderEsakshiSummaryCards() {
  return `
    <div class="stat-cards">
      ${ESAKSHI_SUMMARY_CARDS.map(
        (c) => `
        <div class="stat-card">
          <div class="stat-value">${c.value}</div>
          ${c.sub ? `<div class="stat-value stat-value-sub">${c.sub}</div>` : ""}
          <div class="stat-label">${c.label}</div>
        </div>`
      ).join("")}
    </div>`;
}

/**
 * Fetches a role's own project list (GET /projects/mine) and joins it,
 * client-side, with the two already-public feeds registry.js established
 * in Phase 5 (GET /flags/public, GET /flags/analysis/public) so each row
 * carries a real tier/driving-signal/escalation-stage/"analyzed" state.
 * This is presentation-layer joining over data the caller is ALREADY
 * allowed to see in full (their own /projects/mine rows) plus data that
 * is public to everyone regardless of role (/flags/public) -- it never
 * widens what the caller can see beyond their own jurisdiction-scoped
 * project list (Rules.md: jurisdiction filtering stays server-side; this
 * is only enriching rows the server already scoped for this caller).
 */
export async function fetchScopedRiskRows(projectsPath = "/projects/mine") {
  const [projects, flags, analysisResults] = await Promise.all([
    apiFetch(projectsPath),
    apiFetch("/flags/public"),
    apiFetch("/flags/analysis/public"),
  ]);

  const latestFlagByWork = {};
  for (const f of flags) {
    if (!(f.work_id in latestFlagByWork)) latestFlagByWork[f.work_id] = f;
  }
  const analyzedWorkIds = new Set(analysisResults.map((a) => a.work_id));

  return projects.map((p) => {
    const f = latestFlagByWork[p.work_id];
    return {
      ...p,
      tier: f?.tier ?? null,
      driving_signal: f?.driving_signal ?? null,
      flag_status: f?.status ?? null,
      analyzed: analyzedWorkIds.has(p.work_id),
    };
  });
}

const TIER_RANK = { Critical: 4, High: 3, Medium: 2, Low: 1, Normal: 0 };

/**
 * Renders a project table with an optional Completed/Ongoing status
 * filter and a "sort by risk" toggle (PRD.md S5.3's "risk-ordered project
 * section" / S5.4's "full project list"). Clicking a row calls
 * onRowClick(work_id).
 */
export function renderProjectListSection(container, rows, { onRowClick, statusTabs = true } = {}) {
  const statuses = statusTabs ? [...new Set(rows.map((r) => r.status).filter(Boolean))] : [];

  const html = `
    <div class="filter-bar">
      ${
        statuses.length
          ? `<select id="pl-status"><option value="">All statuses</option>${statuses
              .map((s) => `<option value="${s}">${s}</option>`)
              .join("")}</select>`
          : ""
      }
      <label><input type="checkbox" id="pl-risk-sort" /> Sort by risk (highest first)</label>
      <span id="pl-count" class="subtle"></span>
    </div>
    <div id="pl-results"></div>
  `;
  container.innerHTML = html;

  const statusSelect = container.querySelector("#pl-status");
  const riskSortBox = container.querySelector("#pl-risk-sort");
  const resultsEl = container.querySelector("#pl-results");
  const countEl = container.querySelector("#pl-count");

  function draw() {
    let visible = rows;
    if (statusSelect && statusSelect.value) visible = visible.filter((r) => r.status === statusSelect.value);
    if (riskSortBox.checked) {
      visible = [...visible].sort((a, b) => (TIER_RANK[b.tier] ?? -1) - (TIER_RANK[a.tier] ?? -1));
    }
    countEl.textContent = `${visible.length} of ${rows.length} works`;
    resultsEl.innerHTML =
      visible.length === 0
        ? "<p><em>No works match this filter.</em></p>"
        : `
      <table class="data-table">
        <thead><tr><th>Work ID</th><th>Description</th><th>Status</th><th>Progress</th><th>Risk / reason</th></tr></thead>
        <tbody>
          ${visible
            .map(
              (r) => `
            <tr data-work-id="${r.work_id}" class="clickable-row">
              <td>${r.work_id}</td>
              <td>${r.work_description ?? ""}</td>
              <td>${r.status ?? ""}</td>
              <td>${r.physical_progress_pct != null ? r.physical_progress_pct + "%" : ""}</td>
              <td>${tierBadge(r)}${r.driving_signal ? `<br><span class="subtle">${r.driving_signal}</span>` : ""}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;

    if (onRowClick) {
      resultsEl.querySelectorAll(".clickable-row").forEach((tr) => {
        tr.addEventListener("click", () => onRowClick(tr.dataset.workId));
      });
    }
  }

  statusSelect?.addEventListener("change", draw);
  riskSortBox.addEventListener("change", draw);
  draw();
}

/**
 * Tab shell: renders a row of buttons and swaps a single panel element's
 * content between each tab's render(panelEl) function. Every Phase 10
 * dashboard uses this for its top-level section switcher (Home / Area
 * Analysis / Projects / Review, etc. -- PRD.md S5.3-S5.6).
 */
export function renderDashboardTabs(container, tabs, initialKey) {
  container.innerHTML = `
    <div class="dashboard-tabs">
      ${tabs.map((t) => `<button class="dashboard-tab" data-key="${t.key}">${t.label}</button>`).join("")}
    </div>
    <div class="tab-panel" id="tab-panel"></div>
  `;
  const panel = container.querySelector("#tab-panel");
  const buttons = [...container.querySelectorAll(".dashboard-tab")];

  function activate(key) {
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.key === key));
    const tab = tabs.find((t) => t.key === key) ?? tabs[0];
    tab.render(panel);
  }

  buttons.forEach((b) => b.addEventListener("click", () => activate(b.dataset.key)));
  activate(initialKey ?? tabs[0].key);
}

/**
 * Opens one project's full detail as its own separate page, laid over
 * whatever dashboard/tab the caller was on (rather than appending the
 * detail inline at the bottom of the same project-list panel, which is
 * how this used to work). The dashboard underneath is left untouched --
 * "Back" just closes this page and, if the caller passed onBack, re-runs
 * it so anything the caller's list depends on (e.g. a flag's status
 * after an action taken here) is refreshed.
 *
 * panelEl is accepted for backwards compatibility with every call site
 * (agency/local/district/state/mp dashboards, review.js) but is no longer
 * where the detail renders into.
 */
export async function openWorkDetail(panelEl, workId, me, onBack) {
  let pageRoot = document.getElementById("project-page-root");
  if (!pageRoot) {
    pageRoot = document.createElement("div");
    pageRoot.id = "project-page-root";
    document.body.appendChild(pageRoot);
  }
  pageRoot.classList.add("project-page-overlay");
  pageRoot.innerHTML = `<div class="project-page-inner" id="project-page-inner"></div>`;
  document.body.style.overflow = "hidden";
  window.scrollTo({ top: 0, behavior: "instant" });

  const closeProjectPage = () => {
    pageRoot.classList.remove("project-page-overlay");
    pageRoot.innerHTML = "";
    document.body.style.overflow = "";
    onBack?.();
  };

  await renderWorkDetail(pageRoot.querySelector("#project-page-inner"), workId, me, {
    onBack: closeProjectPage,
  });
}
