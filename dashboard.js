// Phase 8 bug fix (PRD.md S5.1, S7 item 1; Implementation-Guide.md Phase 8
// item 1) -- this is now STRICTLY the public/no-login dashboard's content:
// PRD.md S5.1's three summary figures (sanctioned amount, completed count,
// ongoing count) + the S4.10 area-lookup section. Registry/Network/Review
// data must never be wired in here, even for a logged-in role -- PRD.md
// S5.3-S5.6 each describe an authority's own "Home" as this SAME
// public-style summary (jurisdiction-scoped, once Phase 10 builds that),
// not a richer view, so this file is deliberately shared by every role,
// not just the no-login visitor.
//
// The richer flag/tier/reviewer aggregate stats this file used to render
// (GET /stats/public) are NOT deleted -- that endpoint still exists,
// unused by any view right now, until Phase 10 gives it a proper
// authority-only home. Do not wire it back into this file; that would
// reopen the exact scope-leak bug this fixes.

import { getCurrentUserId, fetchPublicDistricts, fetchPublicAreaProjects } from "../api.js";

// Stage 3 -- the 6 summary cards match the official eSAKSHI portal's
// public dashboard exactly (label + figure), so every role's Home shows
// the same headline numbers a visitor sees on the real MPLADS site,
// instead of the 4 cards computed from our own synthetic dataset.
const ESAKSHI_SUMMARY_CARDS = [
  { label: "Allocated Limit for Hon'ble MPs", value: "₹ 8,349.39 Crore" },
  { label: "Amount consented for Calamity", value: "₹ 4.06 Crore" },
  { label: "Works Recommended", value: "No. 110562", sub: "₹ 5,927.53 Crore" },
  { label: "Works Sanctioned", value: "No. 82603", sub: "₹ 4,361.48 Crore" },
  { label: "Works Completed", value: "No. 36131", sub: "₹ 1,776.58 Crore" },
  { label: "Expenditure on Completed and On-going Works as on Date", value: "₹ 2,895.71 Crore" },
];

// Stage 2c -- OTP verification removed entirely: a visitor now picks their
// state, then their district, from real dropdown lists (state -> district
// chain, populated straight from the works data via GET /public/districts)
// and sees that district's projects directly -- no phone/OTP/name/address
// steps. Kept as plain module-level state + a re-render function, same
// pattern main.js's login form already uses -- no component framework
// (Rules.md).
const _areaState = {
  districtsByState: null, // null until GET /public/districts resolves
  loadingDistricts: false,
  state: "",
  district: "",
  results: null,
  error: "",
  loading: false,
};

function _renderAreaSection() {
  const s = _areaState;
  const errorHtml = s.error ? `<p class="error">${s.error}</p>` : "";

  if (s.districtsByState == null) {
    return `
      <h3>Check your area's projects</h3>
      <p class="subtle">Pick your state and district to see the projects recommended for that area.</p>
      <div class="verify-box">
        <p class="subtle">${s.error ? "Could not load the state/district list." : "Loading states/districts..."}</p>
        ${errorHtml}
      </div>
    `;
  }

  const states = Object.keys(s.districtsByState);
  const districts = s.state ? s.districtsByState[s.state] || [] : [];

  return `
    <h3>Check your area's projects</h3>
    <p class="subtle">Pick your state and district to see the projects recommended for that area
    (status, implementing authority, % complete).</p>
    <div class="verify-box">
      <select id="area-state">
        <option value="">Select State</option>
        ${states.map((st) => `<option value="${st}" ${st === s.state ? "selected" : ""}>${st}</option>`).join("")}
      </select>
      <select id="area-district" ${s.state ? "" : "disabled"}>
        <option value="">${s.state ? "Select District" : "Select state first"}</option>
        ${districts.map((d) => `<option value="${d}" ${d === s.district ? "selected" : ""}>${d}</option>`).join("")}
      </select>
      <button id="area-lookup" class="primary-button" ${s.loading || !s.state || !s.district ? "disabled" : ""}>
        ${s.loading ? "Looking up..." : "View Projects"}
      </button>
      ${s.results !== null ? `<button id="area-reset" class="link-button">Start over</button>` : ""}
      ${errorHtml}
    </div>
    ${s.results !== null ? _renderAreaResults(s.results) : ""}
  `;
}

function _renderAreaResults(results) {
  if (results == null) return "";
  if (results.length === 0) {
    return `<p class="subtle">No projects found for that district.</p>`;
  }
  return `
    <ul class="work-list">
      ${results
        .map(
          (r) => `<li class="work-item">
            ${r.work_id} -- ${r.work_description ?? r.work_category ?? ""}
            <span class="subtle">(${r.status}, ${r.physical_progress_pct ?? "?"}% complete, ${r.executing_agency ?? "—"})</span>
          </li>`
        )
        .join("")}
    </ul>
  `;
}

function _wireAreaSection(container, rerender) {
  const box = container.querySelector(".verify-box");
  if (!box) return;

  if (_areaState.districtsByState == null) return; // nothing interactive to wire yet

  const stateSelect = box.querySelector("#area-state");
  const districtSelect = box.querySelector("#area-district");
  const lookupBtn = box.querySelector("#area-lookup");
  const resetBtn = box.querySelector("#area-reset");

  stateSelect.addEventListener("change", (e) => {
    _areaState.state = e.target.value;
    _areaState.district = "";
    _areaState.results = null;
    _areaState.error = "";
    rerender();
  });

  districtSelect.addEventListener("change", (e) => {
    _areaState.district = e.target.value;
    rerender();
  });

  lookupBtn.addEventListener("click", async () => {
    _areaState.loading = true;
    _areaState.error = "";
    rerender();
    try {
      _areaState.results = await fetchPublicAreaProjects(_areaState.state, _areaState.district);
    } catch (err) {
      _areaState.error = err.message || "Could not look up that district.";
    } finally {
      _areaState.loading = false;
      rerender();
    }
  });

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      Object.assign(_areaState, { state: "", district: "", results: null, error: "" });
      rerender();
    });
  }
}

async function _ensureDistrictsLoaded(rerender) {
  if (_areaState.districtsByState != null || _areaState.loadingDistricts) return;
  _areaState.loadingDistricts = true;
  try {
    _areaState.districtsByState = await fetchPublicDistricts();
  } catch (err) {
    _areaState.error = err.message || "Could not load the state/district list.";
  } finally {
    _areaState.loadingDistricts = false;
    rerender();
  }
}

// Stage 2 -- big "About the Scheme" section for the public/no-login
// homepage, using the exact text supplied for the scheme description.
// Shown once, above the summary stat cards.
function _renderAboutSection() {
  return `
    <section class="about-scheme">
      <h2>About the Scheme</h2>
      <p>The Members of Parliament Local Area Development Scheme (MPLADS) is a Central Sector Scheme fully funded by the Government of India, launched on 23 December 1993. The Scheme enables Members of Parliament (MPs) to recommend developmental works based on the locally felt needs of their constituencies, with a focus on creating durable community assets and improving essential public services such as health, sanitation, education, and drinking water infrastructure.</p>
      <p>The Scheme is administered by the Ministry of Statistics and Programme Implementation (MoSPI), which is responsible for formulating guidelines and overseeing the implementation and monitoring of the Scheme.</p>
      <p>At the time of its launch in 1993-94, each Member of Parliament was allocated ₹5 lakh per annum under MPLADS. The annual allocation was subsequently enhanced to ₹1 crore in 1994-95, ₹2 crore in 1998-99, and ₹5 crore from the financial year 2011-12 onwards. In view of the COVID-19 pandemic, the Scheme was suspended from 6 April 2020 to 9 November 2021, and no funds were allocated during FY 2020-21. For the remaining period of FY 2021-22 (10 November 2021 to 31 March 2022), an allocation of ₹2 crore was provided to each Member of Parliament.</p>
      <p>The first MPLADS Guidelines were issued in February 1994 and have since been revised periodically in December 1994, February 1997, September 1999, April 2002, November 2005, August 2012, May 2014, June 2016, and most recently in April 2023. The current Guidelines of 2023 reflect nearly three decades of implementation experience and incorporate suggestions from Members of Parliament of both Lok Sabha and Rajya Sabha, feedback from Central Ministries, State/UT Governments, other stakeholders, and findings from independent third-party evaluations commissioned by MoSPI.</p>
      <p>In April 2023, MPLADS transitioned from a paper-based system to a fully digital end-to-end platform, eSAKSHI, comprising a web portal and mobile application. The platform provides dedicated login access to all stakeholders and facilitates transparent, efficient, and seamless implementation, monitoring, and management of the Scheme. Since April 2025, MPLAD Scheme implemented the TSA/Hybrid fund flow procedure achieving the goal of 'just-in-time' fund release to the vendors through an integrated network of PFMS, RBI and SBI (Scheduled Commercial Bank).</p>
      <p>MPLAD Scheme is strategically aligned with the vision of Viksit Bharat @ 2047, which seeks to transform India into a developed, inclusive, and self-reliant nation by the centenary of its independence. While continuing to address immediate local needs, the Scheme encourages MPs to prioritize the creation of sustainable and future-ready assets. By promoting green practices, digital inclusion, skill development, and social equity, MPLADS contributes to grassroots development while supporting the nation's long-term growth and transformation.</p>
    </section>
  `;
}

export async function renderDashboardView(container) {
  const isLoggedIn = !!getCurrentUserId();

  function render() {
    container.innerHTML = `
      <p class="subtle">${
        isLoggedIn
          ? "Home summary."
          : "Public transparency summary -- no login required."
      }</p>

      <div class="stat-cards">
        ${ESAKSHI_SUMMARY_CARDS.map(
          (c) => `
          <div class="stat-card">
            <div class="stat-value">${c.value}</div>
            ${c.sub ? `<div class="stat-value stat-value-sub">${c.sub}</div>` : ""}
            <div class="stat-label">${c.label}</div>
          </div>`
        ).join("")}
      </div>

      ${isLoggedIn ? "" : _renderAboutSection()}
    `;
  }

  render();
}

// Exported so main.js's corner menu / centered overlay ("Check Your Area
// Projects") can reuse this exact state -> district -> results flow,
// instead of it being embedded inline on the homepage.
export function renderVerifySection() {
  return _renderAreaSection();
}
export function wireVerifySection(container, rerender) {
  _ensureDistrictsLoaded(rerender);
  return _wireAreaSection(container, rerender);
}