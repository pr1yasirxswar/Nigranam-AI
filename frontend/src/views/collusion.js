// Phase 9 -- Collusion Alerts panel (PRD.md S4.9), backed by
// GET /collusion/alerts. Nodal State Authority only.
//
// Implementation-Guide.md Phase 9 item 5 names this panel's home as
// "state_dashboard.js" -- that file doesn't exist yet, since Phase 10 (the
// full one-view-per-role dashboard rebuild) hasn't been built. Rather than
// jumping ahead into Phase 10's rebuild here, this stays a standalone view
// module, same pattern network.js/registry.js already use pre-Phase-10 --
// wired into main.js's VIEWS_FOR_ROLE for nodal_state_authority only, and
// intended to be lifted into the real State dashboard's "Collusion Alerts"
// panel wholesale once Phase 10 builds that file (its render function
// already takes just a container element, so the lift is a copy-paste of
// the panel markup, not a rewrite).

import { apiFetch, getCurrentUser } from "../api.js";

export async function renderCollusionView(container) {
  const user = getCurrentUser();

  if (!user || user.role !== "nodal_state_authority") {
    container.innerHTML = `
      <h2>Collusion Alerts</h2>
      <p class="subtle">This view is available to the Nodal State Authority only.</p>`;
    return;
  }

  container.innerHTML = `<p><em>Loading collusion alerts...</em></p>`;

  let alerts;
  try {
    alerts = await apiFetch("/collusion/alerts");
  } catch (err) {
    container.innerHTML = `<p class="error">Could not load collusion alerts: ${err.message}</p>`;
    return;
  }

  if (alerts.length === 0) {
    container.innerHTML = `
      <h2>Collusion Alerts</h2>
      <p class="subtle">A repeat-offender pattern here means one specific authority account has cleared the
      same recurring anomaly on the same agency category several times in a row without it resolving -- not
      a claim that any two named companies are colluding.</p>
      <p><em>No collusion alerts in your state yet.</em></p>`;
    return;
  }

  container.innerHTML = `
    <h2>Collusion Alerts -- ${user.name}</h2>
    <p class="subtle">A repeat-offender pattern here means one specific authority account has cleared the
    same recurring anomaly on the same agency category several times in a row without it resolving -- not
    a claim that any two named companies are colluding.</p>

    <div class="network-nodes">
      ${alerts
        .map(
          (a) => `
        <div class="network-card">
          <div class="network-card-title">${a.agency_id}</div>
          <div class="subtle">Reviewed by: ${a.authority_name} (${a.authority_role.replace("_", " ")})</div>
          <p class="network-reason">${a.reason}</p>
          <div class="subtle" style="margin-top:0.5rem;">
            ${a.override_count} consecutive overrides &middot; work IDs: ${a.related_work_ids.join(", ")}
          </div>
        </div>`
        )
        .join("")}
    </div>
  `;
}
