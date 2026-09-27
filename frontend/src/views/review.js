// Phase 5 -- Role-based review panel: the jurisdiction-scoped dashboard
// per role, with the escalation FSM and visibility cascade visible in the
// UI (Implementation-Guide.md Phase 5, item 4). Server-side jurisdiction
// filtering does all the real work (GET /projects/mine, GET /flags/mine) --
// this view only renders what the backend already scoped (Rules.md: never
// filter jurisdiction in the frontend).
//
// Phase 10 (Implementation-Guide.md Phase 10) -- every per-role dashboard
// (agency/local/district/state/mp) needs the SAME "click a work -> see
// detail, escalation track, review thread, action buttons" panel as its
// own "review page" section, rather than five copies of this logic. So
// CHAIN_LABELS/CHAIN_ORDER and the work-detail renderer are now exported
// for reuse; renderReviewView() below is UNCHANGED and kept as the
// original flat, non-role-specific panel (still reachable only if a view
// wires it in -- Phase 10 dashboards no longer do, in favor of their own
// sectioned layout that embeds renderWorkDetail directly).

import { apiFetch, getCurrentUserId } from "../api.js";

export const CHAIN_LABELS = {
  local_pending: "Local Authority",
  escalated_district: "District Authority",
  escalated_state: "Nodal State Authority",
  escalated_mp: "Member of Parliament",
  resolved: "Resolved",
};
export const CHAIN_ORDER = ["local_pending", "escalated_district", "escalated_state", "escalated_mp"];

function _escalationTrack(currentStatus) {
  if (currentStatus === "resolved") {
    return `<div class="escalation-track"><span class="escalation-step resolved">Resolved ✓</span></div>`;
  }
  const idx = CHAIN_ORDER.indexOf(currentStatus);
  return `<div class="escalation-track">${CHAIN_ORDER.map((stage, i) => {
    const cls = i < idx ? "past" : i === idx ? "current" : "future";
    return `<span class="escalation-step ${cls}">${CHAIN_LABELS[stage]}</span>`;
  }).join('<span class="escalation-arrow">&rarr;</span>')}</div>`;
}

/**
 * Renders one work's full detail: project header, tier/escalation track,
 * review thread, and (if the caller's role is currently accountable for
 * the flag's stage) action buttons. Shared by every Phase 10 per-role
 * dashboard's "review" section, plus the legacy flat renderReviewView()
 * below.
 *
 * @param {HTMLElement} container element this renders into
 * @param {string} workId
 * @param {object} me -- GET /auth/me's response (name/role/jurisdiction)
 * @param {{onBack?: () => void}} [options] -- onBack, if given, renders a
 *   "back" link above the detail that calls it instead of the default
 *   (return to the flat review panel) -- lets a Phase 10 dashboard's own
 *   project-list section be what the back button returns to.
 */
// Phase 14 (Evidence Layer) -- money/delay/duplicate/photo-forensics
// evidence cards, rendered from GET /projects/{work_id}/evidence's
// `evidence` payload (scoring/pipeline.py's score_work() -> per-module
// evidence_x() dicts, persisted on AnalysisResult -- see
// Implementation-Guide-Phase13.md's Phase 14). Rendered for ANY module
// that scored above 0 -- not just the driving_signal -- so a "clear"
// project can still show its own numbers backing up its status
// (STAGE-ANALYSIS-v5.md's core point), not only flagged ones.
function _fmtDate(d) {
  return d ? new Date(d).toLocaleDateString() : "—";
}
function _fmtPct(fraction) {
  return fraction === null || fraction === undefined ? "—" : `${(fraction * 100).toFixed(1)}%`;
}
function _fmtNum(n, digits = 0) {
  return n === null || n === undefined ? "—" : Number(n).toFixed(digits);
}

function _moneyCard(ev) {
  if (!ev) return "";
  return `
    <div class="evidence-card">
      <h5>Money</h5>
      <p class="subtle">Category: ${ev.work_category ?? "—"}</p>
      <p>Sanctioned amount: ₹${_fmtNum(ev.sanctioned_amount)} &middot;
         peer (same-category) Q3: ₹${_fmtNum(ev.peer_q3_sanctioned_amount)}
         ${ev.peer_outlier_score > 0 ? ` <span class="tier-pill tier-high">outlier</span>` : ""}</p>
      ${ev.expected_cost_from_boq ? `
      <p>BOQ-expected cost: ₹${_fmtNum(ev.expected_cost_from_boq)} &middot;
         sanctioned/BOQ ratio: ${_fmtNum(ev.sanctioned_vs_boq_ratio, 2)}x
         ${ev.dsr_score > 0 ? ` <span class="tier-pill tier-high">above standard rate</span>` : ""}</p>
      ` : ""}
    </div>`;
}

function _delayCard(ev) {
  if (!ev) return "";
  if (ev.status === "Completed") {
    const late = ev.days_late;
    return `
      <div class="evidence-card">
        <h5>Delay</h5>
        <p>Expected completion: ${_fmtDate(ev.expected_completion_date)} &middot;
           actual: ${_fmtDate(ev.actual_completion_date)}</p>
        <p>${late === null ? "—" : late > 0 ? `<strong>${late} days late</strong>` : `${Math.abs(late)} days early/on time`}</p>
      </div>`;
  }
  if (ev.status === "In Progress") {
    return `
      <div class="evidence-card">
        <h5>Delay</h5>
        <p>Expected completion: ${_fmtDate(ev.expected_completion_date)} &middot;
           ${ev.days_past_expected_deadline > 0 ? `<strong>${ev.days_past_expected_deadline} days past deadline</strong>` : "not yet past deadline"}</p>
        <p>Physical progress: ${ev.physical_progress_pct ?? "—"}% &middot;
           spend: ${_fmtPct(ev.spend_fraction)} of sanctioned</p>
      </div>`;
  }
  return "";
}

function _duplicateCard(ev) {
  if (!ev || !ev.matched_work_id) return "";
  return `
    <div class="evidence-card">
      <h5>Possible duplicate</h5>
      <p>Matches <a href="#" data-goto-work="${ev.matched_work_id}">${ev.matched_work_id}</a>
         &middot; text similarity: ${_fmtNum(ev.text_similarity * 100, 0)}%
         ${ev.distance_km !== null ? ` &middot; ${_fmtNum(ev.distance_km, 1)} km apart` : ""}</p>
    </div>`;
}

function _photoCard(ev) {
  if (!ev || !ev.photo_available) return "";
  const badges = [];
  if (ev.gps_distance_km !== null && ev.gps_distance_km > 5) {
    badges.push(`<span class="tier-pill tier-critical">GPS ${_fmtNum(ev.gps_distance_km, 1)} km from site</span>`);
  }
  if (ev.backdated_score > 0) {
    badges.push(`<span class="tier-pill tier-critical">captured before sanction date</span>`);
  }
  if (ev.missing_exif) {
    badges.push(`<span class="tier-pill tier-medium">metadata missing</span>`);
  }
  if (ev.reused_photo_with_work_id) {
    badges.push(`<span class="tier-pill tier-critical">same photo as <a href="#" data-goto-work="${ev.reused_photo_with_work_id}">${ev.reused_photo_with_work_id}</a></span>`);
  }
  return `
    <div class="evidence-card">
      <h5>Photo / location</h5>
      <p>Captured: ${_fmtDate(ev.photo_captured_at)} &middot; sanctioned: ${_fmtDate(ev.sanction_date)}</p>
      ${badges.length ? `<p>${badges.join(" ")}</p>` : `<p class="subtle">No forensic issue detected.</p>`}
    </div>`;
}

function _stagesSection(stages) {
  if (!stages || !stages.length) return "";
  return `
    <h4>Stage submissions</h4>
    <div class="stage-list">
      ${stages.map((s) => `
        <div class="stage-entry">
          <div><strong>${s.stage_label}</strong> &middot; <span class="subtle">${new Date(s.submitted_at).toLocaleDateString()}</span>
            ${s.ai_tier ? ` &middot; <span class="tier-pill tier-${s.ai_tier.toLowerCase()}">${s.ai_tier}</span>` : ""}</div>
          <div class="subtle">Progress: ${s.physical_progress_pct}% &middot; Expenditure: ₹${s.expenditure_this_stage}
            &middot; ${s.photo_count} photo(s), ${s.document_count} document(s)</div>
        </div>`).join("")}
    </div>`;
}

function _renderEvidence(evidence, stages) {
  const cards = [
    _moneyCard(evidence?.money),
    _delayCard(evidence?.delay),
    _duplicateCard(evidence?.duplicate),
    _photoCard(evidence?.photo_forensics),
  ].filter(Boolean);

  return `
    <h4>Evidence <span class="subtle">(the actual numbers/dates behind the tier above)</span></h4>
    ${cards.length ? `<div class="evidence-grid">${cards.join("")}</div>` : "<p><em>No supporting evidence recorded for this work yet.</em></p>"}
    ${_stagesSection(stages)}
  `;
}

export async function renderWorkDetail(container, workId, me, options = {}) {
  container.innerHTML = `<p><em>Loading work ${workId}...</em></p>`;

  let flag, reviews, project, evidenceData;
  try {
    [flag, project, evidenceData] = await Promise.all([
      apiFetch(`/flags/${workId}`),
      apiFetch(`/projects/${workId}`),
      apiFetch(`/projects/${workId}/evidence`),
    ]);
    reviews = flag.status === "no_flag" ? [] : await apiFetch(`/projects/${workId}/reviews`);
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load work ${workId}: ${err.message}</p>`;
    return;
  }

  const isActionable = flag.status && flag.status !== "no_flag" && flag.status !== "resolved";
  const accountableStage = CHAIN_ORDER.find((s) => s === flag.status);
  const roleForStage = {
    local_pending: "local_authority",
    escalated_district: "district_authority",
    escalated_state: "nodal_state_authority",
    escalated_mp: "mp",
  };
  const canActOnStage = isActionable && roleForStage[accountableStage] === me.role;

  container.innerHTML = `
    ${options.onBack ? `<button id="back-to-list" class="link-button">&larr; Back to list</button>` : ""}
    <h3>${workId} -- ${project.work_description ?? ""}</h3>
    <p class="subtle">${project.state}, ${project.implementing_district} &middot; Executing agency: ${project.executing_agency}
    &middot; Status: ${project.status} &middot; Progress: ${project.physical_progress_pct ?? "?"}%</p>

    ${
      flag.status === "no_flag"
        ? `<p><em>No flag currently on this work.</em></p>
           <button id="raise-flag-btn" class="primary-button">Run AI risk check / raise flag</button>`
        : `
          <p><strong>Tier:</strong> <span class="tier-pill tier-${(flag.tier || "").toLowerCase()}">${flag.tier}</span>
             ${flag.driving_signal ? ` &middot; driving signal: ${flag.driving_signal}` : ""}</p>
          ${_escalationTrack(flag.status)}
          <p class="subtle">Stage deadline: ${flag.stage_deadline ? new Date(flag.stage_deadline).toLocaleString() : "—"}</p>
        `
    }

    ${_renderEvidence(evidenceData.evidence, evidenceData.stages)}

    <h4>Review thread <span class="subtle">(visibility cascade -- every tier whose jurisdiction covers this work sees the same thread)</span></h4>
    <div class="review-thread">
      ${
        reviews.length
          ? reviews
              .map(
                (r) => `
        <div class="review-entry">
          <div><strong>${r.actor_role}</strong> &middot; <span class="subtle">${new Date(r.created_at).toLocaleString()}</span></div>
          <div>${r.action}${r.from_status ? ` (${r.from_status} &rarr; ${r.to_status})` : ""}</div>
          ${r.reason ? `<div class="review-reason">"${r.reason}"</div>` : ""}
        </div>`
              )
              .join("")
          : "<p><em>No review activity yet.</em></p>"
      }
    </div>

    ${
      isActionable
        ? `
      <h4>Take action</h4>
      ${
        canActOnStage
          ? `
        <p class="subtle">Your role (${me.role}) is currently accountable for this flag's stage.</p>
        <textarea id="action-reason" placeholder="Justification (required for clear/escalate, optional for comment)"></textarea>
        <div class="action-buttons">
          <button data-action="comment" class="action-btn">Comment</button>
          <button data-action="clear" class="action-btn clear">Clear</button>
          <button data-action="escalate" class="action-btn escalate">Escalate</button>
        </div>`
          : `
        <textarea id="action-reason" placeholder="Comment (any jurisdiction holder can comment)"></textarea>
        <div class="action-buttons">
          <button data-action="comment" class="action-btn">Comment</button>
        </div>
        <p class="subtle">Only ${roleForStage[accountableStage] ?? "the accountable role"} can clear or escalate this flag at its current stage.</p>`
      }
      <div id="action-result"></div>
    `
        : ""
    }
  `;

  container.querySelector("#back-to-list")?.addEventListener("click", options.onBack);

  // Phase 14 -- evidence cards' "matched/reused work_id" links jump straight
  // to that other work's own detail view (same container, same options).
  container.querySelectorAll("[data-goto-work]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      renderWorkDetail(container, link.dataset.gotoWork, me, options);
    });
  });

  container.querySelector("#raise-flag-btn")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      await apiFetch(`/flags/${workId}/raise`, { method: "POST" });
      await renderWorkDetail(container, workId, me, options);
    } catch (err) {
      container.querySelector("#raise-flag-btn").insertAdjacentHTML("afterend", `<p class="error">${err.message}</p>`);
    }
  });

  container.querySelectorAll(".action-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      const reason = container.querySelector("#action-reason").value.trim();
      const resultEl = container.querySelector("#action-result");
      try {
        await apiFetch(`/projects/${workId}/reviews`, {
          method: "POST",
          body: JSON.stringify({ action, reason: reason || null }),
        });
        await renderWorkDetail(container, workId, me, options);
      } catch (err) {
        resultEl.innerHTML = `<p class="error">${err.message}</p>`;
      }
    });
  });
}

export async function renderReviewView(container, skipReload) {
  if (!getCurrentUserId()) {
    container.innerHTML = `
      <h2>Review Panel</h2>
      <p class="subtle">Pick a role in the switcher above to see that role's jurisdiction-scoped dashboard.</p>`;
    return;
  }

  container.innerHTML = `<p><em>Loading your dashboard...</em></p>`;

  let me, myProjects, myFlags;
  try {
    me = await apiFetch("/auth/me");
    [myProjects, myFlags] = await Promise.all([apiFetch("/projects/mine"), apiFetch("/flags/mine")]);
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load dashboard: ${err.message}</p>`;
    return;
  }

  const flagByWork = Object.fromEntries(myFlags.map((f) => [f.work_id, f]));
  const jurisdiction = Object.entries(me.jurisdiction)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}: ${v}`)
    .join(" &middot; ");

  container.innerHTML = `
    <h2>Review Panel -- ${me.name} (${me.role})</h2>
    <p class="subtle">Jurisdiction: ${jurisdiction || "none"}. Showing ${myProjects.length} works in scope,
    ${myFlags.length} awaiting your tier's action.</p>

    <h3>Flags awaiting your action</h3>
    ${
      myFlags.length
        ? `<ul class="work-list">${myFlags
            .map((f) => `<li data-work-id="${f.work_id}" class="work-item clickable">
              <span class="tier-pill tier-${(f.tier || "").toLowerCase()}">${f.tier}</span>
              ${f.work_id} &middot; stage: ${CHAIN_LABELS[f.status] ?? f.status}
            </li>`)
            .join("")}</ul>`
        : "<p><em>Nothing waiting on you right now.</em></p>"
    }

    <h3>All works in your jurisdiction</h3>
    <ul class="work-list">
      ${myProjects
        .map((p) => {
          const f = flagByWork[p.work_id];
          return `<li data-work-id="${p.work_id}" class="work-item clickable">
            ${f ? `<span class="tier-pill tier-${(f.tier || "").toLowerCase()}">${f.tier}</span>` : ""}
            ${p.work_id} -- ${p.work_description ?? ""} <span class="subtle">(${p.status})</span>
          </li>`;
        })
        .join("")}
    </ul>

    <div id="work-detail"></div>
  `;

  container.querySelectorAll(".work-item").forEach((li) => {
    li.addEventListener("click", () => {
      const detailEl = container.querySelector("#work-detail");
      detailEl.scrollIntoView?.({ behavior: "smooth" });
      renderWorkDetail(detailEl, li.dataset.workId, me, {
        onBack: () => renderReviewView(container.closest("#view-root") || container.parentElement, true),
      });
    });
  });
}
