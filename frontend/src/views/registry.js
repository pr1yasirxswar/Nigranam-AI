// Phase 5 -- Registry: all-India list of works with reasons (shared,
// unfiltered public view -- Implementation-Guide.md Phase 5, item 2).
// Backed by GET /projects (public, unfiltered) joined client-side with
// GET /flags/public for the "reason" column. This join is presentation
// only -- both endpoints are already public/unfiltered, so there is no
// jurisdiction filtering happening here (Rules.md's ban on client-side
// filtering is about hiding rows a role isn't authorized to see, not
// about a search box over already-public data).
//
// Phase 8 bug fix (PRD.md S7 item 2, Architecture.md S4: "any 'no flag
// yet' UI state must be distinguishable from 'confirmed clean'") -- also
// joins GET /flags/analysis/public, so a badge now reflects one of three
// REAL states instead of collapsing "never scored" into "not flagged":
//   - an open Flag exists            -> its tier badge (as before)
//   - no Flag, but an AnalysisResult -> "Clear" (scored, genuinely below
//     the flagging bar)
//   - neither                        -> "Pending analysis" (never scored)

import { apiFetch } from "../api.js";

let _allRows = [];

function _tierBadge(row) {
  if (row.tier) return `<span class="tier-pill tier-${row.tier.toLowerCase()}">${row.tier}</span>`;
  if (row.analyzed) return `<span class="tier-pill tier-clear">Clear</span>`;
  return `<span class="tier-pill tier-pending">Pending analysis</span>`;
}

function _renderTable(rows) {
  if (rows.length === 0) return "<p><em>No works match this filter.</em></p>";
  return `
    <table class="data-table">
      <thead>
        <tr>
          <th>Work ID</th><th>State</th><th>Constituency</th><th>District</th>
          <th>Status</th><th>Progress</th><th>Risk / reason</th><th>Escalation stage</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (r) => `
          <tr>
            <td>${r.work_id}</td>
            <td>${r.state ?? ""}</td>
            <td>${r.constituency ?? ""}</td>
            <td>${r.implementing_district ?? ""}</td>
            <td>${r.status ?? ""}</td>
            <td>${r.physical_progress_pct != null ? r.physical_progress_pct + "%" : ""}</td>
            <td>${_tierBadge(r)}${r.driving_signal ? `<br><span class="subtle">${r.driving_signal}</span>` : ""}</td>
            <td>${r.flag_status ?? "—"}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function _applyFilters(container, state, status, flaggedOnly) {
  let rows = _allRows;
  if (state) rows = rows.filter((r) => (r.state || "").toLowerCase().includes(state.toLowerCase()));
  if (status) rows = rows.filter((r) => r.status === status);
  if (flaggedOnly) rows = rows.filter((r) => r.tier);
  container.querySelector("#registry-results").innerHTML = _renderTable(rows);
  container.querySelector("#registry-count").textContent = `${rows.length} of ${_allRows.length} works`;
}

export async function renderRegistryView(container) {
  container.innerHTML = `<p><em>Loading registry...</em></p>`;

  let projects, flags, analysisResults;
  try {
    [projects, flags, analysisResults] = await Promise.all([
      apiFetch("/projects"),
      apiFetch("/flags/public"),
      apiFetch("/flags/analysis/public"),
    ]);
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load registry: ${err.message}</p>`;
    return;
  }

  const latestFlagByWork = {};
  for (const f of flags) {
    // /flags/public is already ordered newest-first, so the first hit per
    // work_id is its latest flag.
    if (!(f.work_id in latestFlagByWork)) latestFlagByWork[f.work_id] = f;
  }
  const analyzedWorkIds = new Set(analysisResults.map((a) => a.work_id));

  _allRows = projects.map((p) => {
    const f = latestFlagByWork[p.work_id];
    return {
      ...p,
      tier: f?.tier ?? null,
      driving_signal: f?.driving_signal ?? null,
      flag_status: f?.status ?? null,
      analyzed: analyzedWorkIds.has(p.work_id),
    };
  });

  const statuses = [...new Set(_allRows.map((r) => r.status).filter(Boolean))];

  container.innerHTML = `
    <h2>Project Registry</h2>
    <p class="subtle">All-India list of MPLAD works, with the AI's flagged reason where one exists. Public, unfiltered
    -- every role (and the public) see the same rows here; jurisdiction scoping only applies to the Review Panel.</p>

    <div class="filter-bar">
      <input type="text" id="filter-state" placeholder="Filter by state..." />
      <select id="filter-status">
        <option value="">All statuses</option>
        ${statuses.map((s) => `<option value="${s}">${s}</option>`).join("")}
      </select>
      <label><input type="checkbox" id="filter-flagged" /> Flagged only</label>
      <span id="registry-count" class="subtle"></span>
    </div>

    <div id="registry-results"></div>
  `;

  const stateInput = container.querySelector("#filter-state");
  const statusSelect = container.querySelector("#filter-status");
  const flaggedCheckbox = container.querySelector("#filter-flagged");

  const refresh = () => _applyFilters(container, stateInput.value, statusSelect.value, flaggedCheckbox.checked);
  stateInput.addEventListener("input", refresh);
  statusSelect.addEventListener("change", refresh);
  flaggedCheckbox.addEventListener("change", refresh);

  refresh();
}
