// Phase 5 -- Network view: agency-district risk-concentration graph,
// Local Authority/District/State/MP only, not Implementing Agency
// (Implementation-Guide.md Phase 5, item 3). Requires the role switcher
// in the header to have a role selected -- backed by GET /agencies/network.
//
// Phase 8 bug fix (PRD.md S7 item 3, S4.2: "must now surface a reason
// string per flagged agency/unit") -- every unit returned by the backend
// now carries a plain-language `reason` (agency_network.py's
// get_reasoned_units()) plus the works_count/high_plus_count evidence
// behind it. Units with no genuine reason are no longer returned by the
// backend at all (routers/agencies.py), so there is nothing here to
// render with a bare centrality number.

import { apiFetch, getCurrentUserId } from "../api.js";

export async function renderNetworkView(container) {
  if (!getCurrentUserId()) {
    container.innerHTML = `
      <h2>Network View</h2>
      <p class="subtle">Pick a role in the switcher above to view agency risk clustering. Implementing Agency
      accounts don't have access to this view.</p>`;
    return;
  }

  container.innerHTML = `<p><em>Loading network...</em></p>`;

  let data;
  try {
    data = await apiFetch("/agencies/network");
  } catch (err) {
    if (err.status === 403) {
      container.innerHTML = `<p class="error">${err.message}</p>`;
    } else {
      container.innerHTML = `<p class="error">Could not load network view: ${err.message}</p>`;
    }
    return;
  }

  if (!data.state) {
    container.innerHTML = `<p><em>No jurisdiction data found for this account.</em></p>`;
    return;
  }
  if (data.nodes.length === 0) {
    container.innerHTML = `
      <h2>Network View -- ${data.state}</h2>
      <p><em>No agency-district units with a genuine, evidenced reason to flag in this state yet.</em></p>`;
    return;
  }

  const key = (n) => `${n.agency}|${n.district}`;
  const connections = {};
  for (const e of data.edges) {
    const a = key(e.source);
    const b = key(e.target);
    (connections[a] ??= new Set()).add(b);
    (connections[b] ??= new Set()).add(a);
  }
  const labelByKey = {};
  data.nodes.forEach((n) => (labelByKey[key(n)] = `${n.agency} (${n.district})`));

  const maxCentrality = Math.max(0.01, ...data.nodes.map((n) => n.centrality));

  container.innerHTML = `
    <h2>Network View -- ${data.state}</h2>
    <p class="subtle">Agency-district units genuinely tied to a repeated fraud/anomaly pattern, each shown with the
    plain-language reason behind its flag. This surfaces <strong>regional risk
    concentration</strong>, not a collusion claim -- the dataset only has generic agency-type categories, not
    distinct company identities.</p>

    <div class="network-nodes">
      ${data.nodes
        .map((n) => {
          const k = key(n);
          const peers = [...(connections[k] || [])].map((pk) => labelByKey[pk]).filter(Boolean);
          return `
          <div class="network-card">
            <div class="network-card-title">${n.agency}</div>
            <div class="subtle">${n.district}</div>
            <p class="network-reason">${n.reason}</p>
            <div class="bar-track" style="margin:0.5rem 0;">
              <div class="bar-fill" style="width:${(n.centrality / maxCentrality) * 100}%"></div>
            </div>
            <div class="subtle">${n.high_plus_count} of ${n.works_count} works High+ &middot; centrality ${n.centrality}</div>
            ${
              peers.length
                ? `<div class="subtle" style="margin-top:0.5rem;">Connected to: ${peers.join(", ")}</div>`
                : `<div class="subtle" style="margin-top:0.5rem;">No same-state elevated peers</div>`
            }
          </div>`;
        })
        .join("")}
    </div>
  `;
}
