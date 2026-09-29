// Project details page -- the core screen of Nigranam-AI. One work, seven
// clearly separated sections:
//   Summary        - what this work is + the AI's one-line verdict
//   Alerts         - active flag, who must act by when, unacknowledged notices
//   Agency Data    - everything the implementing agency uploaded (stages,
//                    photos, documents) + the upload form for the agency
//   Chats          - agency <-> authority conversation thread
//   AI Detections  - what the AI flagged, on which data point, with numbers
//   Notices        - formal notices an authority sends the agency
//   Review         - escalation track, review thread, clear/escalate actions
//
// All data comes from existing jurisdiction-gated endpoints plus
// /projects/{id}/messages (routers/messages.py). Everything the server sends
// is already sanitized by api.js, so it is safe to place in innerHTML.

import { apiFetch, apiFetchBlob, apiUpload } from "../api.js";
import { CHAIN_LABELS, CHAIN_ORDER } from "./review.js";

const ROLE_LABELS = {
  implementing_agency: "Implementing Agency",
  local_authority: "Local Authority",
  district_authority: "District Authority",
  nodal_state_authority: "Nodal State Authority",
  mp: "Member of Parliament",
  system: "System",
};
const AUTHORITY_ROLES = ["local_authority", "district_authority", "nodal_state_authority", "mp"];
const ROLE_FOR_STAGE = {
  local_pending: "local_authority",
  escalated_district: "district_authority",
  escalated_state: "nodal_state_authority",
  escalated_mp: "mp",
};

const roleLabel = (r) => ROLE_LABELS[r] ?? r ?? "";
const fmtDT = (d) => (d ? new Date(d).toLocaleString() : "—");
const fmtD = (d) => (d ? new Date(d).toLocaleDateString() : "—");
const rupees = (n) => (n == null ? "—" : `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`);
const crore = (n) => (n == null ? "—" : `₹${(n / 1e7).toFixed(2)} Cr`);
const num = (n, d = 0) => (n == null ? "—" : Number(n).toFixed(d));
const isLive = (workId) => /\/USR\//.test(workId);

function tierPill(tier) {
  if (!tier) return `<span class="tier-pill tier-clear">Clear</span>`;
  return `<span class="tier-pill tier-${tier.toLowerCase()}">${tier}</span>`;
}
function sevPill(sev) {
  const label = { critical: "Critical", high: "High", medium: "Medium", low: "Low" }[sev] ?? sev;
  return `<span class="tier-pill tier-${sev}">${label}</span>`;
}

function escalationTrack(status) {
  if (status === "resolved") return `<div class="escalation-track"><span class="escalation-step resolved">Resolved ✓</span></div>`;
  const idx = CHAIN_ORDER.indexOf(status);
  return `<div class="escalation-track">${CHAIN_ORDER.map((stage, i) => {
    const cls = i < idx ? "past" : i === idx ? "current" : "future";
    return `<span class="escalation-step ${cls}">${CHAIN_LABELS[stage]}</span>`;
  }).join('<span class="escalation-arrow">&rarr;</span>')}</div>`;
}

// ---------------------------------------------------------------------------
// AI detections -- turns the pipeline's evidence into "what was flagged, and
// on which data point". Every module is listed, flagged or clear.
// ---------------------------------------------------------------------------
function buildDetections(ev, stages) {
  const found = [];
  const checked = []; // { name, state: "clear" | "pending", text }

  const m = ev?.money;
  if (!m || Object.keys(m).length === 0) {
    checked.push({ name: "Cost check", state: "pending", text: "Not analysed yet" });
  } else {
    let hit = false;
    if (m.peer_outlier_score > 0) {
      hit = true;
      found.push({
        module: "Cost check", sev: "high",
        title: "Cost is unusually high for this type of work",
        what: `Sanctioned ${rupees(m.sanctioned_amount)} against a typical upper range of ${rupees(m.peer_q3_sanctioned_amount)} for ${m.work_category ?? "similar works"}.`,
        where: "Sanctioned amount",
      });
    }
    if (m.expected_cost_from_boq && m.dsr_score > 0) {
      hit = true;
      found.push({
        module: "Cost check", sev: "high",
        title: "Sanctioned above the standard-rate estimate",
        what: `Bill-of-quantities estimate is ${rupees(m.expected_cost_from_boq)}; the sanction is ${num(m.sanctioned_vs_boq_ratio, 2)}x of it.`,
        where: "Bill of quantities vs sanction",
      });
    }
    if (!hit) checked.push({ name: "Cost check", state: "clear", text: "Cost is within the normal range" });
  }

  const d = ev?.delay;
  if (!d || !d.status) {
    checked.push({ name: "Timeline check", state: "pending", text: "Not applicable yet" });
  } else {
    let late = 0;
    let what = "";
    if (d.status === "Completed" && d.days_late > 0) {
      late = d.days_late;
      what = `Expected ${fmtD(d.expected_completion_date)}, completed ${fmtD(d.actual_completion_date)} (${late} days late).`;
    } else if (d.status === "In Progress" && d.days_past_expected_deadline > 0) {
      late = d.days_past_expected_deadline;
      what = `Expected ${fmtD(d.expected_completion_date)}; ${late} days past deadline at ${num(d.physical_progress_pct)}% progress with ${num((d.spend_fraction ?? 0) * 100, 1)}% of sanction spent.`;
    }
    if (late > 0) {
      found.push({
        module: "Timeline check", sev: late > 90 ? "high" : "medium",
        title: "Work is behind schedule", what, where: "Expected completion date",
      });
    } else {
      checked.push({ name: "Timeline check", state: "clear", text: "On schedule" });
    }
  }

  const dup = ev?.duplicate;
  if (dup && dup.matched_work_id) {
    found.push({
      module: "Duplicate check", sev: "high",
      title: "Looks like another work",
      what: `Matches ${dup.matched_work_id} (${num(dup.text_similarity * 100, 0)}% text similarity${dup.distance_km != null ? `, ${num(dup.distance_km, 1)} km apart` : ""}).`,
      where: "Work description and location",
      link: dup.matched_work_id,
    });
  } else {
    checked.push({ name: "Duplicate check", state: dup ? "clear" : "pending", text: dup ? "No similar work found" : "Not analysed yet" });
  }

  const ph = ev?.photo_forensics;
  if (!ph || !ph.photo_available) {
    checked.push({ name: "Photo check", state: "pending", text: "No geotagged photo on record yet" });
  } else {
    let hit = false;
    const add = (sev, title, what) => { hit = true; found.push({ module: "Photo check", sev, title, what, where: `Latest uploaded photo (captured ${fmtD(ph.photo_captured_at)})` }); };
    if (ph.gps_distance_km != null && ph.gps_distance_km > 5) add("critical", "Photo was taken away from the site", `GPS in the photo is ${num(ph.gps_distance_km, 1)} km from the project location.`);
    if (ph.backdated_score > 0) add("critical", "Photo predates the sanction", `Captured ${fmtD(ph.photo_captured_at)}, but the work was sanctioned ${fmtD(ph.sanction_date)}.`);
    if (ph.missing_exif) add("medium", "Photo metadata is missing", "Camera, time and GPS data could not be read from the file.");
    if (ph.reused_photo_with_work_id) {
      hit = true;
      found.push({
        module: "Photo check", sev: "critical", title: "Same photo used on another work",
        what: `Image matches a photo already used for ${ph.reused_photo_with_work_id}.`,
        where: "Latest uploaded photo", link: ph.reused_photo_with_work_id,
      });
    }
    if (!hit) checked.push({ name: "Photo check", state: "clear", text: "No forensic issue in the uploaded photo" });
  }

  for (const s of stages ?? []) {
    const t = (s.ai_tier ?? "").toLowerCase();
    if (["critical", "high", "medium"].includes(t)) {
      found.push({
        module: "Stage submission", sev: t,
        title: `Stage "${s.stage_label}" was flagged`,
        what: `Scored ${s.ai_tier}${s.ai_driving_signal ? `; main trigger: ${s.ai_driving_signal}` : ""}. Reported ${num(s.physical_progress_pct)}% progress, ${rupees(s.expenditure_this_stage)} spent, ${s.photo_count} photo(s), ${s.document_count} document(s).`,
        where: `Stage submitted ${fmtD(s.submitted_at)}`,
      });
    }
  }

  const rank = { critical: 3, high: 2, medium: 1, low: 0 };
  found.sort((a, b) => (rank[b.sev] ?? 0) - (rank[a.sev] ?? 0));
  return { found, checked };
}

// ---------------------------------------------------------------------------
// Tab renderers. Each returns HTML; wiring happens in wire*() below.
// ---------------------------------------------------------------------------
function summaryTab(ctx) {
  const { project, flag, detections, stages, messages, workId } = ctx;
  const notices = messages.filter((x) => x.kind === "notice");
  const pending = notices.filter((x) => !x.acknowledged_at).length;
  const hasFlag = flag.status && flag.status !== "no_flag";
  const n = detections.found.length;
  const verdict = hasFlag
    ? `AI flagged this work as <strong>${flag.tier}</strong>${flag.driving_signal ? ` — main trigger: ${flag.driving_signal}` : ""}. ${n} issue${n === 1 ? "" : "s"} found. ${flag.status === "resolved" ? "The flag is resolved." : `It is currently with the ${CHAIN_LABELS[flag.status] ?? flag.status}.`}`
    : n > 0
      ? `No flag raised, but ${n} point${n === 1 ? "" : "s"} need${n === 1 ? "s" : ""} attention. See AI Detections.`
      : "No risk detected by the AI on the data available so far.";
  const prog = Math.max(0, Math.min(100, Number(project.physical_progress_pct) || 0));
  const spentPct = project.sanctioned_amount ? Math.min(100, ((project.expenditure ?? 0) / project.sanctioned_amount) * 100) : null;

  return `
    <div class="pd-verdict ${hasFlag ? `pd-verdict-${(flag.tier || "").toLowerCase()}` : ""}">${verdict}</div>
    <div class="pd-facts">
      <div><span>Status</span><strong>${project.status ?? "—"}</strong></div>
      <div><span>Category</span><strong>${project.work_category ?? "—"}</strong></div>
      <div><span>Location</span><strong>${[project.village_or_locality, project.implementing_district, project.state].filter(Boolean).join(", ") || "—"}</strong></div>
      <div><span>Constituency / MP</span><strong>${[project.constituency, project.mp_name].filter(Boolean).join(" · ") || "—"}</strong></div>
      <div><span>Executing agency</span><strong>${project.executing_agency ?? "Not assigned"}</strong></div>
      <div><span>Sanctioned</span><strong>${crore(project.sanctioned_amount)}</strong></div>
      <div><span>Spent so far</span><strong>${crore(project.expenditure)}${spentPct != null ? ` (${spentPct.toFixed(0)}%)` : ""}</strong></div>
    </div>
    <div class="pd-progress"><div class="pd-progress-label">Physical progress: ${prog}%</div>
      <div class="pd-bar"><div class="pd-bar-fill" style="width:${prog}%"></div></div></div>
    <div class="pd-counts">
      <div><strong>${detections.found.length}</strong><span>AI detections</span></div>
      <div><strong>${stages.length}</strong><span>Stage uploads</span></div>
      <div><strong>${messages.filter((x) => x.kind === "chat").length}</strong><span>Chat messages</span></div>
      <div><strong>${pending}</strong><span>Notices awaiting reply</span></div>
    </div>
    <p class="subtle">Work ID: ${workId}</p>`;
}

function alertsTab(ctx) {
  const { flag, messages, me } = ctx;
  const hasFlag = flag.status && flag.status !== "no_flag";
  const pendingNotices = messages.filter((x) => x.kind === "notice" && !x.acknowledged_at);
  const accountable = ROLE_FOR_STAGE[flag.status];
  let html = "";
  if (hasFlag && flag.status !== "resolved") {
    html += `
      <div class="pd-alert pd-alert-${(flag.tier || "").toLowerCase()}">
        <div>${tierPill(flag.tier)} <strong>Active AI flag</strong></div>
        ${flag.driving_signal ? `<div>Trigger: ${flag.driving_signal}</div>` : ""}
        <div>Action needed from: <strong>${roleLabel(accountable)}</strong>${accountable === me.role ? " (you)" : ""}</div>
        <div class="subtle">Auto-escalates if untouched by ${fmtDT(flag.stage_deadline)}</div>
        ${escalationTrack(flag.status)}
      </div>`;
  } else if (hasFlag) {
    html += `<div class="pd-alert pd-alert-clear"><strong>Flag resolved.</strong>${escalationTrack("resolved")}</div>`;
  } else {
    html += `<div class="pd-alert pd-alert-clear"><strong>No active flag on this work.</strong></div>`;
  }
  if (pendingNotices.length) {
    html += `<h4>Notices awaiting the agency's reply (${pendingNotices.length})</h4>` +
      pendingNotices.map((x) => `<div class="pd-alert pd-alert-notice"><div class="subtle">${roleLabel(x.sender_role)} · ${fmtDT(x.created_at)}</div><div>${x.body}</div></div>`).join("");
  }
  return html;
}

function photoTile(workId, p) {
  return `<figure class="pd-photo" data-photo-id="${p.id}"><div class="pd-photo-img">Loading…</div><figcaption>${p.filename ?? "photo"}</figcaption></figure>`;
}

function agencyDataTab(ctx) {
  const { stages, workId, me, project } = ctx;
  const live = isLive(workId);
  const canUpload = me.role === "implementing_agency" && live && ["Sanctioned", "In Progress"].includes(project.status);

  const stageHtml = stages.length
    ? stages.map((s) => `
      <div class="pd-stage">
        <div class="pd-stage-head"><strong>${s.stage_label}</strong>
          <span class="subtle">${fmtD(s.submitted_at)} · deadline ${fmtD(s.task_deadline)}</span>
          ${s.ai_tier ? tierPill(s.ai_tier) : ""}
          <span class="pd-chip">${s.accepted === true ? "Accepted" : s.accepted === false ? "Sent back" : "Awaiting review"}</span></div>
        <div class="subtle">Progress ${num(s.physical_progress_pct)}% · spent ${rupees(s.expenditure_this_stage)}</div>
        ${s.photos.length ? `<div class="pd-photo-grid">${s.photos.map((p) => photoTile(workId, p)).join("")}</div>` : ""}
        ${s.documents.length ? `<ul class="pd-docs">${s.documents.map((d) => `<li><button class="link-button" data-doc-id="${d.id}">📄 ${d.filename ?? "document"}</button></li>`).join("")}</ul>` : ""}
      </div>`).join("")
    : `<p class="subtle">${live ? "Nothing uploaded yet." : "No agency uploads are recorded for this work."}</p>`;

  const form = canUpload ? `
    <h4>Upload a stage report</h4>
    <div class="upload-box">
      <div class="pd-form-grid">
        <label>Stage name<input id="up-label" type="text" placeholder="e.g. Foundation complete" /></label>
        <label>Stage deadline<input id="up-deadline" type="date" /></label>
        <label>Physical progress (%)<input id="up-progress" type="number" min="0" max="100" step="0.1" /></label>
        <label>Spent in this stage (₹)<input id="up-spend" type="number" min="0" step="1" /></label>
      </div>
      <label>Photos (geotagged)<input id="up-photos" type="file" accept="image/*" multiple /></label>
      <label>Documents<input id="up-docs" type="file" multiple /></label>
      <button id="up-submit" class="primary-button">Submit stage report</button>
      <div id="up-result"></div>
    </div>` : "";

  return `<h4>Stage submissions, photos and documents</h4>${stageHtml}${form}`;
}

function chatsTab(ctx) {
  const chats = ctx.messages.filter((x) => x.kind === "chat");
  return `
    <div class="pd-thread" id="chat-thread">
      ${chats.length ? chats.map((c) => `
        <div class="pd-msg ${c.sender_role === ctx.me.role ? "pd-msg-me" : ""}">
          <div class="pd-msg-meta">${c.sender_name ?? ""} · ${roleLabel(c.sender_role)} · ${fmtDT(c.created_at)}</div>
          <div>${c.body}</div>
        </div>`).join("") : `<p class="subtle">No messages yet. Start the conversation below.</p>`}
    </div>
    <textarea id="chat-input" placeholder="Write a message about this work"></textarea>
    <button id="chat-send" class="primary-button">Send</button>
    <div id="chat-result"></div>`;
}

function detectionsTab(ctx) {
  const { found, checked } = ctx.detections;
  const flagged = found.length
    ? found.map((f) => `
      <div class="pd-detect pd-detect-${f.sev}">
        <div class="pd-detect-head">${sevPill(f.sev)} <strong>${f.title}</strong></div>
        <div>${f.what}</div>
        <div class="subtle">Where: ${f.where} · Check: ${f.module}${f.link ? ` · <a href="#" data-goto-work="${f.link}">Open ${f.link}</a>` : ""}</div>
      </div>`).join("")
    : `<p class="subtle">The AI found nothing to flag on the data available.</p>`;
  const ok = checked.map((c) => `<li><span class="pd-check-${c.state}">${c.state === "clear" ? "✓" : "–"}</span> <strong>${c.name}</strong> — ${c.text}</li>`).join("");
  return `
    <h4>Flagged by AI (${found.length})</h4>${flagged}
    <h4>Other checks</h4><ul class="pd-checks">${ok}</ul>`;
}

function noticesTab(ctx) {
  const { me } = ctx;
  const notices = ctx.messages.filter((x) => x.kind === "notice").reverse();
  const isAuthority = AUTHORITY_ROLES.includes(me.role);
  const compose = isAuthority ? `
    <div class="upload-box">
      <strong>Send a notice to the implementing agency</strong>
      <textarea id="notice-input" placeholder="State clearly what the agency must explain, correct or submit"></textarea>
      <button id="notice-send" class="primary-button">Send notice</button>
      <div id="notice-result"></div>
    </div>` : "";
  const list = notices.length ? notices.map((x) => `
    <div class="pd-notice ${x.acknowledged_at ? "" : "pd-notice-pending"}">
      <div class="pd-msg-meta">${x.sender_name ?? ""} · ${roleLabel(x.sender_role)} · ${fmtDT(x.created_at)}</div>
      <div>${x.body}</div>
      <div class="pd-notice-foot">
        ${x.acknowledged_at ? `<span class="pd-chip">Acknowledged ${fmtDT(x.acknowledged_at)}</span>`
          : me.role === "implementing_agency" ? `<button class="action-btn" data-ack="${x.id}">Acknowledge</button>`
          : `<span class="pd-chip pd-chip-warn">Awaiting agency</span>`}
      </div>
    </div>`).join("") : `<p class="subtle">No notices have been sent for this work.</p>`;
  return `${compose}<h4>Notices</h4>${list}<div id="ack-result"></div>`;
}

function reviewTab(ctx) {
  const { flag, reviews, me } = ctx;
  const hasFlag = flag.status && flag.status !== "no_flag";
  const isActionable = hasFlag && flag.status !== "resolved";
  const canAct = isActionable && ROLE_FOR_STAGE[flag.status] === me.role;
  const canRaise = !hasFlag && me.role !== "implementing_agency";

  const top = hasFlag
    ? `<p>${tierPill(flag.tier)}${flag.driving_signal ? ` · trigger: ${flag.driving_signal}` : ""}</p>${escalationTrack(flag.status)}`
    : `<p class="subtle">No flag on this work.</p>${canRaise ? `<button id="raise-flag-btn" class="primary-button">Run AI risk check</button>` : ""}`;

  const thread = reviews.length
    ? reviews.map((r) => `
      <div class="review-entry">
        <div><strong>${roleLabel(r.actor_role)}</strong> · <span class="subtle">${fmtDT(r.created_at)}</span></div>
        <div>${r.action}${r.from_status ? ` (${CHAIN_LABELS[r.from_status] ?? r.from_status} &rarr; ${CHAIN_LABELS[r.to_status] ?? r.to_status})` : ""}</div>
        ${r.reason ? `<div class="review-reason">"${r.reason}"</div>` : ""}
      </div>`).join("")
    : `<p class="subtle">No review activity yet.</p>`;

  const actions = isActionable ? `
    <h4>Take action</h4>
    <textarea id="action-reason" placeholder="${canAct ? "Justification (required to clear or escalate)" : "Comment"}"></textarea>
    <div class="action-buttons">
      <button data-action="comment" class="action-btn">Comment</button>
      ${canAct ? `<button data-action="clear" class="action-btn clear">Clear</button><button data-action="escalate" class="action-btn escalate">Escalate</button>` : ""}
    </div>
    ${canAct ? "" : `<p class="subtle">Only the ${roleLabel(ROLE_FOR_STAGE[flag.status])} can clear or escalate at this stage.</p>`}
    <div id="action-result"></div>` : "";

  return `${top}<h4>Review thread</h4><div class="review-thread">${thread}</div>${actions}`;
}

// ---------------------------------------------------------------------------
// Main entry
// ---------------------------------------------------------------------------
export async function renderProjectDetail(container, workId, me, options = {}) {
  container.innerHTML = `<p><em>Loading ${workId}…</em></p>`;

  let flag, project, evidenceData, reviews, messages;
  try {
    [flag, project, evidenceData] = await Promise.all([
      apiFetch(`/flags/${workId}`),
      apiFetch(`/projects/${workId}`),
      apiFetch(`/projects/${workId}/evidence`),
    ]);
    reviews = flag.status === "no_flag" ? [] : await apiFetch(`/projects/${workId}/reviews`);
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load ${workId}: ${err.message}</p>`;
    return;
  }
  try {
    messages = await apiFetch(`/projects/${workId}/messages`);
  } catch {
    messages = [];
  }

  const stages = evidenceData.stages ?? [];
  const detections = buildDetections(evidenceData.evidence, stages);
  const ctx = { workId, me, flag, project, reviews, messages, stages, detections };

  const hasActiveFlag = flag.status && !["no_flag", "resolved"].includes(flag.status);
  const pendingNotices = messages.filter((x) => x.kind === "notice" && !x.acknowledged_at).length;
  const alertCount = (hasActiveFlag ? 1 : 0) + pendingNotices;

  const tabs = [
    { key: "summary", label: "Summary", render: summaryTab },
    { key: "alerts", label: "Alerts", badge: alertCount, render: alertsTab },
    { key: "agency", label: "Agency Data", badge: stages.length, render: agencyDataTab },
    { key: "chats", label: "Chats", badge: messages.filter((x) => x.kind === "chat").length, render: chatsTab },
    { key: "detections", label: "AI Detections", badge: detections.found.length, render: detectionsTab },
    { key: "notices", label: "Notices", badge: pendingNotices, render: noticesTab },
    { key: "review", label: "Review", render: reviewTab },
  ];

  let active = tabs.some((t) => t.key === options.initialTab) ? options.initialTab : hasActiveFlag || pendingNotices ? "alerts" : "summary";

  const reload = (tab) => renderProjectDetail(container, workId, me, { ...options, initialTab: tab });

  function draw() {
    container.innerHTML = `
      ${options.onBack ? `<button id="back-to-list" class="link-button">&larr; Back</button>` : ""}
      <div class="pd-header">
        <h3>${project.work_description ?? workId}</h3>
        <div class="subtle">${workId} · ${project.state ?? ""}${project.implementing_district ? `, ${project.implementing_district}` : ""} · ${project.executing_agency ?? "Agency not assigned"}</div>
        <div>${tierPill(hasActiveFlag || flag.status === "resolved" ? flag.tier : null)} <span class="pd-chip">${project.status ?? ""}</span></div>
      </div>
      <div class="pd-tabs" role="tablist">
        ${tabs.map((t) => `<button class="pd-tab ${t.key === active ? "active" : ""}" data-tab="${t.key}">${t.label}${t.badge ? ` <span class="pd-badge">${t.badge}</span>` : ""}</button>`).join("")}
      </div>
      <div class="pd-panel" id="pd-panel">${tabs.find((t) => t.key === active).render(ctx)}</div>`;

    container.querySelector("#back-to-list")?.addEventListener("click", options.onBack);
    container.querySelectorAll(".pd-tab").forEach((b) => b.addEventListener("click", () => { active = b.dataset.tab; draw(); }));
    wire(container.querySelector("#pd-panel"));
  }

  function showError(el, msg) { if (el) el.innerHTML = `<p class="error">${msg}</p>`; }

  function wire(panel) {
    panel.querySelectorAll("[data-goto-work]").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      renderProjectDetail(container, a.dataset.gotoWork, me, options);
    }));

    // Photo thumbnails + document links: protected files, fetched with the token.
    panel.querySelectorAll(".pd-photo").forEach(async (fig) => {
      const box = fig.querySelector(".pd-photo-img");
      try {
        const url = await apiFetchBlob(`/projects/${workId}/files/photos/${fig.dataset.photoId}`);
        box.innerHTML = `<img src="${url}" alt="Uploaded photo" />`;
        box.querySelector("img").addEventListener("click", () => window.open(url, "_blank"));
      } catch {
        box.textContent = "Preview unavailable";
      }
    });
    panel.querySelectorAll("[data-doc-id]").forEach((b) => b.addEventListener("click", async () => {
      try { window.open(await apiFetchBlob(`/projects/${workId}/files/documents/${b.dataset.docId}`), "_blank"); }
      catch { b.textContent = "File unavailable"; }
    }));

    // Agency upload form
    panel.querySelector("#up-submit")?.addEventListener("click", async (e) => {
      const result = panel.querySelector("#up-result");
      const label = panel.querySelector("#up-label").value.trim();
      const deadline = panel.querySelector("#up-deadline").value;
      const progress = panel.querySelector("#up-progress").value;
      const spend = panel.querySelector("#up-spend").value;
      if (!label || !deadline || progress === "" || spend === "") return showError(result, "Fill in the stage name, deadline, progress and spend.");
      const fd = new FormData();
      fd.append("stage_label", label);
      fd.append("task_deadline", deadline);
      fd.append("physical_progress_pct", progress);
      fd.append("expenditure_this_stage", spend);
      for (const f of panel.querySelector("#up-photos").files) fd.append("photos", f);
      for (const f of panel.querySelector("#up-docs").files) fd.append("documents", f);
      e.target.disabled = true;
      result.innerHTML = `<p class="subtle">Uploading and running AI checks…</p>`;
      try {
        await apiUpload(`/projects/${workId}/stages`, fd);
        await reload("agency");
      } catch (err) {
        e.target.disabled = false;
        showError(result, err.message);
      }
    });

    // Chat
    panel.querySelector("#chat-send")?.addEventListener("click", async (e) => {
      const input = panel.querySelector("#chat-input");
      if (!input.value.trim()) return;
      e.target.disabled = true;
      try {
        await apiFetch(`/projects/${workId}/messages`, { method: "POST", body: JSON.stringify({ kind: "chat", body: input.value.trim() }) });
        await reload("chats");
      } catch (err) { e.target.disabled = false; showError(panel.querySelector("#chat-result"), err.message); }
    });
    const thread = panel.querySelector("#chat-thread");
    if (thread) thread.scrollTop = thread.scrollHeight;

    // Notices
    panel.querySelector("#notice-send")?.addEventListener("click", async (e) => {
      const input = panel.querySelector("#notice-input");
      if (!input.value.trim()) return showError(panel.querySelector("#notice-result"), "Write the notice first.");
      e.target.disabled = true;
      try {
        await apiFetch(`/projects/${workId}/messages`, { method: "POST", body: JSON.stringify({ kind: "notice", body: input.value.trim() }) });
        await reload("notices");
      } catch (err) { e.target.disabled = false; showError(panel.querySelector("#notice-result"), err.message); }
    });
    panel.querySelectorAll("[data-ack]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await apiFetch(`/projects/${workId}/messages/${b.dataset.ack}/ack`, { method: "POST" });
        await reload("notices");
      } catch (err) { b.disabled = false; showError(panel.querySelector("#ack-result"), err.message); }
    }));

    // Review
    panel.querySelector("#raise-flag-btn")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        await apiFetch(`/flags/${workId}/raise`, { method: "POST" });
        await reload("review");
      } catch (err) { e.target.disabled = false; e.target.insertAdjacentHTML("afterend", `<p class="error">${err.message}</p>`); }
    });
    panel.querySelectorAll(".action-btn[data-action]").forEach((btn) => btn.addEventListener("click", async () => {
      const reason = panel.querySelector("#action-reason").value.trim();
      try {
        await apiFetch(`/projects/${workId}/reviews`, { method: "POST", body: JSON.stringify({ action: btn.dataset.action, reason: reason || null }) });
        await reload("review");
      } catch (err) { showError(panel.querySelector("#action-result"), err.message); }
    }));
  }

  draw();
}
