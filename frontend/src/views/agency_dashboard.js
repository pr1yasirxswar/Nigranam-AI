// Phase 10 (Implementation-Guide.md Phase 10 item 1, PRD.md S5.2) --
// Implementing Agency dashboard: Home (own-works summary), and a project
// list grouped by assigning authority with a per-project upload
// workspace.
//
// Login itself is unchanged -- Implementing Agency accounts already use
// the same generic POST /auth/login (auth/identity.py, auth/seed_users.py
// already seeds a "demo-agency" account with role=implementing_agency).
// Implementation-Guide.md Phase 10 item 1 mentions a separate
// "auth/agency_auth.py, issued by District Authority at assignment" --
// that's explicitly Phase 11 scope (the ISSUANCE flow letting a District
// Authority generate a new Work-ID/password pair); it is not a
// prerequisite for this dashboard to work with the accounts that already
// exist, so it is not built here (Rules.md: don't build ahead of the
// guide's own phase split).
//
// "Assigning authority" is rendered as the agency's own `district` field
// (auth/permissions.py's jurisdiction key for this role is
// (executing_agency, implementing_district)) -- there is no separate
// "assigned-by" identity column in the dataset/User model to group by
// instead, and inventing one would be exactly what Rules.md warns against.
//
// The "per-project upload workspace" is shown as a real but explicitly
// disabled UI shell, same honest-placeholder pattern dashboard.js already
// uses for the Phase 11 OTP verify flow -- PRD.md does not specify what
// backend an evidence-upload endpoint would look like (storage, file
// types, size limits), so this flags it rather than inventing one.

import { apiFetch } from "../api.js";
import {
  formatCrore,
  renderStatCards,
  renderDashboardTabs,
  renderRiskAnalysisSection,
  fetchScopedRiskRows,
  openWorkDetail,
  tierBadge,
} from "./shared/dashboard_kit.js";

async function renderHome(panel, me, rows) {
  const sanctioned = rows.reduce((s, r) => s + (Number(r.sanctioned_amount) || 0), 0);
  const flagged = rows.filter((r) => r.tier).length;

  panel.innerHTML = `
    <p class="subtle">Executing agency: ${me.jurisdiction.executing_agency} &middot; District: ${me.jurisdiction.district}</p>
    ${renderStatCards([
      { value: rows.length, label: "Assigned works" },
      { value: formatCrore(sanctioned), label: "Sanctioned amount" },
      { value: flagged, label: "Flagged by AI" },
    ])}
  `;
}

function _uploadWorkspace(workId) {
  return `
    <div class="upload-box">
      <p class="subtle"><strong>Upload workspace</strong> -- not yet wired to a backend (Phase 11+ scope, not
      specified in PRD.md: storage, file types, and size limits still need a team decision). Shown here as a
      real but disabled UI shell rather than faked.</p>
      <input type="file" disabled multiple />
      <button class="primary-button" disabled>Upload evidence for ${workId}</button>
    </div>
  `;
}

async function renderMyProjects(panel, rows, me) {
  const byDistrict = {};
  for (const r of rows) {
    const d = r.implementing_district ?? "Unknown";
    (byDistrict[d] ??= []).push(r);
  }

  panel.innerHTML = `
    <p class="subtle">Grouped by the District Authority that assigned each work.</p>
    ${Object.entries(byDistrict)
      .map(
        ([district, works]) => `
      <h3>District Authority -- ${district}</h3>
      <ul class="work-list">
        ${works
          .map(
            (w) => `<li data-work-id="${w.work_id}" class="work-item clickable">
              ${tierBadge(w)} ${w.work_id} -- ${w.work_description ?? ""} <span class="subtle">(${w.status})</span>
            </li>`
          )
          .join("")}
      </ul>`
      )
      .join("")}
    <div id="agency-work-detail"></div>
  `;

  panel.querySelectorAll(".work-item").forEach((li) => {
    li.addEventListener("click", async () => {
      const detailEl = panel.querySelector("#agency-work-detail");
      await openWorkDetail(detailEl, li.dataset.workId, me, () => renderMyProjects(panel, rows, me));
      detailEl.insertAdjacentHTML("beforeend", _uploadWorkspace(li.dataset.workId));
    });
  });
}

export async function renderAgencyDashboard(container, me) {
  container.innerHTML = `<p><em>Loading your dashboard...</em></p>`;
  let rows;
  try {
    rows = await fetchScopedRiskRows("/projects/mine");
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load dashboard: ${err.message}</p>`;
    return;
  }

  container.innerHTML = `<h2>Implementing Agency Dashboard -- ${me.name}</h2>`;
  const tabRoot = document.createElement("div");
  container.appendChild(tabRoot);

  renderDashboardTabs(tabRoot, [
    { key: "home", label: "Home", render: (p) => renderHome(p, me, rows) },
    { key: "analysis", label: "Analysis", render: (p) => renderRiskAnalysisSection(p, rows) },
    { key: "projects", label: "My Projects", render: (p) => renderMyProjects(p, rows, me) },
  ]);
}
