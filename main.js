// Phase 5 -- view switching, originally a flat nav + role dropdown.
// Phase 8 bug fix (Implementation-Guide.md Phase 8 items 1, 4, 5, 6;
// Rules.md) replaced the dropdown with a real login form + a persistent
// switcher, still routing into a shared flat set of views
// (dashboard/registry/network/review/collusion).
//
// Phase 10 (Implementation-Guide.md Phase 10, PRD.md S5) replaces THAT
// flat view set with one full, self-contained dashboard per role --
// PRD.md's dashboard spec is "one dashboard per role", not "one shared set
// of views a role picks between", so there is no longer a top nav to
// choose among Registry/Network/Review/Collusion: each role's dashboard
// folds in exactly the sections PRD.md S5.2-S5.6 describe for it (see
// views/agency_dashboard.js, local_dashboard.js, district_dashboard.js,
// state_dashboard.js, mp_dashboard.js), each with its own internal tabs.
// registry.js/network.js/review.js/collusion.js are NOT deleted --
// network.js and collusion.js are reused wholesale as embedded sections
// (see those dashboard files' imports); review.js's work-detail renderer
// is reused via views/shared/dashboard_kit.js. registry.js's flat
// all-India table and review.js's flat renderReviewView() are superseded
// by each role's own scoped project list + review section and are no
// longer routed to, but stay in the tree in case the team wants to bring
// either back as a reference view later.
//
// Stage 2b -- the corner switcher is now a collapsed "Menu" trigger that
// opens a dropdown of the 3 entry points (Login as Authority / Login as
// Implementing Agency / Check Your Area Projects). Picking any of them
// opens a centered overlay (see #modal-root, renderModal() below) with
// the home dashboard still visible, dimmed, behind it -- the corner box
// itself no longer holds the login form or the area-lookup flow inline.

import { API_BASE, apiFetch, getCurrentUser, login, logout } from "./api.js";
import { renderDashboardView, renderVerifySection, wireVerifySection } from "./views/dashboard.js";
import { renderAgencyDashboard } from "./views/agency_dashboard.js";
import { renderLocalDashboard } from "./views/local_dashboard.js";
import { renderDistrictDashboard } from "./views/district_dashboard.js";
import { renderStateDashboard } from "./views/state_dashboard.js";
import { renderMpDashboard } from "./views/mp_dashboard.js";
import { openWorkDetail } from "./views/shared/dashboard_kit.js";

const statusEl = document.getElementById("status");
const navEl = document.getElementById("view-nav");
const rootEl = document.getElementById("view-root");
const switcherEl = document.getElementById("dashboard-switcher");
const modalRootEl = document.getElementById("modal-root");
const alertsBannerEl = document.getElementById("alerts-banner");

// Phase 10 -- one full dashboard per role (PRD.md S5.2-S5.6). null = no
// login (public dashboard, S5.1, unchanged from Phase 8).
const DASHBOARD_FOR_ROLE = {
  implementing_agency: renderAgencyDashboard,
  local_authority: renderLocalDashboard,
  district_authority: renderDistrictDashboard,
  nodal_state_authority: renderStateDashboard,
  mp: renderMpDashboard,
};

// Phase 12 (user-flagged practicality issue): the login form used to fetch
// GET /auth/demo-accounts and show every valid x_user_id in a <select> --
// convenient for a demo, but exactly the kind of thing that "would cause a
// lot of problems" in practice (a real login form must never let a visitor
// browse which account ids exist). Replaced with a plain two-category
// chooser ("Login as Authority" / "Login as Implementing Agency" -- purely
// a label/placeholder choice, both post to the same POST /auth/login) plus
// bare ID + password text fields. No account list is fetched or shown
// anywhere; an unrecognized id and a wrong password get the exact same
// generic "Incorrect ID or password" (routers/auth.py already returns one
// generic error for both cases -- this was already true server-side, the
// dropdown was the only thing undermining it).
let loginCategory = null; // null -> "authority" | "agency"
let loginError = "";

// Stage 2b state -- is the corner dropdown open, and which of the 3
// options (if any) currently owns the centered overlay.
let menuOpen = false;
let modalOption = null; // null -> "authority" | "agency" | "verify"

navEl.style.display = "none"; // Phase 10 -- no flat nav; see header comment.

// Alerts banner -- shown above every dashboard for a logged-in user, not
// buried inside a single project's page. Two sources, both jurisdiction-
// scoped server-side: GET /flags/mine (unresolved flags this role must act
// on) and GET /notices/pending (formal notices the agency hasn't
// acknowledged yet). Clicking an entry opens that work's own detail page
// straight to its Alerts tab.
let alertsExpanded = false;

async function refreshAlertsBanner() {
  const user = getCurrentUser();
  if (!user) {
    alertsBannerEl.innerHTML = "";
    return;
  }
  let flags = [];
  let notices = [];
  try {
    [flags, notices] = await Promise.all([
      apiFetch("/flags/mine"),
      apiFetch("/notices/pending"),
    ]);
  } catch {
    alertsBannerEl.innerHTML = "";
    return; // not fatal -- dashboard content still loads normally
  }

  const total = flags.length + notices.length;
  if (total === 0) {
    alertsBannerEl.innerHTML = "";
    return;
  }

  const items = [
    ...flags.map((f) => ({
      workId: f.work_id,
      tier: f.tier,
      text: `${f.work_id} -- ${f.tier ?? "flagged"}${f.driving_signal ? `: ${f.driving_signal}` : ""}`,
    })),
    ...notices.map((n) => ({
      workId: n.work_id,
      tier: null,
      text: `${n.work_id} -- notice from ${n.sender_name ?? n.sender_role} awaiting your reply`,
    })),
  ];

  alertsBannerEl.innerHTML = `
    <div class="alerts-banner ${alertsExpanded ? "expanded" : ""}">
      <button class="alerts-banner-toggle" id="alerts-toggle">
        <span class="alerts-banner-count">${total}</span> alert${total === 1 ? "" : "s"} need${total === 1 ? "s" : ""} your attention
        <span class="alerts-banner-caret">${alertsExpanded ? "&#9650;" : "&#9660;"}</span>
      </button>
      ${
        alertsExpanded
          ? `<ul class="alerts-banner-list">
              ${items
                .map(
                  (it) => `<li class="alerts-banner-item" data-work-id="${it.workId}">
                    ${it.tier ? `<span class="tier-pill tier-${it.tier.toLowerCase()}">${it.tier}</span>` : `<span class="tier-pill tier-pending">Notice</span>`}
                    ${it.text}
                  </li>`
                )
                .join("")}
            </ul>`
          : ""
      }
    </div>
  `;

  alertsBannerEl.querySelector("#alerts-toggle").addEventListener("click", () => {
    alertsExpanded = !alertsExpanded;
    refreshAlertsBanner();
  });
  alertsBannerEl.querySelectorAll(".alerts-banner-item").forEach((li) => {
    li.addEventListener("click", async () => {
      const me = await apiFetch("/auth/me");
      await openWorkDetail(null, li.dataset.workId, me, () => refreshAlertsBanner());
    });
  });
}

async function showDashboard() {
  const user = getCurrentUser();
  if (!user) {
    alertsBannerEl.innerHTML = "";
    await renderDashboardView(rootEl);
    return;
  }
  refreshAlertsBanner();
  const renderFn = DASHBOARD_FOR_ROLE[user.role];
  if (!renderFn) {
    // Unknown role -- fall back to the plain public-style summary rather
    // than a blank screen.
    await renderDashboardView(rootEl);
    return;
  }
  rootEl.innerHTML = `<p><em>Loading...</em></p>`;
  let me;
  try {
    me = await apiFetch("/auth/me");
  } catch (err) {
    rootEl.innerHTML = `<p class="error">Could not load your session: ${err.message}</p>`;
    return;
  }
  await renderFn(rootEl, me);
}

function renderSwitcher() {
  const current = getCurrentUser();

  if (!current) {
    // Stage 2b -- collapsed "Menu" trigger; the 3 options only appear once
    // it's clicked, and picking one opens the centered overlay (renderModal)
    // rather than expanding the corner box itself.
    switcherEl.innerHTML = `
      <button id="menu-trigger" class="menu-trigger" aria-label="Menu" aria-expanded="${menuOpen}">
        <span class="hamburger-icon">&#9776;</span>
      </button>
      ${
        menuOpen
          ? `<div class="menu-dropdown">
              <button id="menu-login-authority">Login as Authority</button>
              <button id="menu-login-agency">Login as Implementing Agency</button>
              <button id="menu-check-area">Check Your Area Projects</button>
            </div>`
          : ""
      }
    `;
    switcherEl.querySelector("#menu-trigger").addEventListener("click", () => {
      menuOpen = !menuOpen;
      renderSwitcher();
    });
    if (menuOpen) {
      switcherEl.querySelector("#menu-login-authority").addEventListener("click", () => {
        loginCategory = "authority";
        modalOption = "authority";
        loginError = "";
        menuOpen = false;
        renderSwitcher();
        renderModal();
      });
      switcherEl.querySelector("#menu-login-agency").addEventListener("click", () => {
        loginCategory = "agency";
        modalOption = "agency";
        loginError = "";
        menuOpen = false;
        renderSwitcher();
        renderModal();
      });
      switcherEl.querySelector("#menu-check-area").addEventListener("click", () => {
        modalOption = "verify";
        menuOpen = false;
        renderSwitcher();
        renderModal();
      });
    }
  } else {
    switcherEl.innerHTML = `
      <button id="menu-trigger" class="menu-trigger" aria-label="Menu" aria-expanded="${menuOpen}">
        <span class="hamburger-icon">&#9776;</span>
      </button>
      ${
        menuOpen
          ? `<div class="menu-dropdown">
              <div class="switcher-title">Signed in</div>
              <div class="switcher-account">${current.name}<br><span class="subtle">${current.role}</span></div>
              <button id="logout-btn">Log out</button>
            </div>`
          : ""
      }
    `;
    switcherEl.querySelector("#menu-trigger").addEventListener("click", () => {
      menuOpen = !menuOpen;
      renderSwitcher();
    });
    if (menuOpen) {
      switcherEl.querySelector("#logout-btn").addEventListener("click", async () => {
        loginCategory = null;
        modalOption = null;
        menuOpen = false;
        await logout();
        renderSwitcher();
        renderModal();
        await showDashboard();
      });
    }
  }
}

// Stage 2b -- the centered overlay itself. Renders whichever of the 3
// menu options is active into #modal-root; the home dashboard in
// #view-root stays exactly as it was underneath, just dimmed by the
// overlay's background.
function renderModal() {
  if (!modalOption) {
    modalRootEl.innerHTML = "";
    document.body.style.overflow = "";
    return;
  }
  document.body.style.overflow = "hidden";

  let bodyHtml;
  if (modalOption === "verify") {
    bodyHtml = renderVerifySection();
  } else {
    // Implementation-Guide.md Phase 8 item 6 -- a real login form. Typing
    // an id no longer logs anyone in by itself; the password is checked
    // server-side by POST /auth/login before any session token (and
    // therefore any dashboard access) is issued.
    const idLabel = modalOption === "agency" ? "Work ID" : "Authority ID";
    bodyHtml = `
      <h3>Log in as ${modalOption === "agency" ? "Implementing Agency" : "Authority"}</h3>
      <label class="switcher-label" for="login-account">${idLabel}</label>
      <input id="login-account" type="text" placeholder="${idLabel}" autocomplete="username" />
      <label class="switcher-label" for="login-password">Password</label>
      <div class="password-field">
        <input id="login-password" type="password" placeholder="Password" autocomplete="current-password" />
        <button type="button" id="password-toggle" class="password-toggle" aria-label="Show password">Show</button>
      </div>
      <button id="login-submit" class="primary-button">Log in</button>
      ${loginError ? `<p class="error">${loginError}</p>` : ""}
    `;
  }

  modalRootEl.innerHTML = `
    <div class="modal-overlay" id="modal-overlay">
      <div class="modal-box">
        <button class="modal-close" id="modal-close" aria-label="Close">&times;</button>
        ${bodyHtml}
      </div>
    </div>
  `;

  const overlay = document.getElementById("modal-overlay");
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeModal();
  });
  document.getElementById("modal-close").addEventListener("click", closeModal);

  if (modalOption === "verify") {
    wireVerifySection(modalRootEl, renderModal);
  } else {
    document.getElementById("login-submit").addEventListener("click", onLoginSubmit);
    const pwInput = document.getElementById("login-password");
    const pwToggle = document.getElementById("password-toggle");
    pwToggle.addEventListener("click", () => {
      const show = pwInput.type === "password";
      pwInput.type = show ? "text" : "password";
      pwToggle.textContent = show ? "Hide" : "Show";
      pwToggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
      pwInput.focus();
    });
    document.getElementById("login-password").addEventListener("keydown", (e) => {
      if (e.key === "Enter") onLoginSubmit();
    });
  }
}

function closeModal() {
  modalOption = null;
  loginCategory = null;
  loginError = "";
  renderModal();
  renderSwitcher();
}

async function onLoginSubmit() {
  const account = document.getElementById("login-account").value.trim();
  const password = document.getElementById("login-password").value;
  if (!account || !password) {
    loginError = "Enter your ID and password.";
    renderModal();
    return;
  }
  try {
    await login(account, password);
    loginError = "";
    loginCategory = null;
    modalOption = null;
    renderModal();
    renderSwitcher();
    await showDashboard();
  } catch (err) {
    // routers/auth.py already returns one generic message for both
    // "no such account" and "wrong password" -- shown as-is, never
    // distinguished client-side either.
    loginError = err.message || "Incorrect ID or password.";
    renderModal();
  }
}

async function checkBackend() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    await res.json();
    // Stage 2b -- the connectivity check still runs (so a genuinely
    // unreachable backend is still surfaced below), but on success this
    // slot now shows the product mark instead of a raw "Backend
    // connected" string.
    // The project name is now the masthead's top line (index.html), so the
    // small status chip is simply hidden once the backend is reachable.
    statusEl.textContent = "";
    statusEl.style.display = "none";
    renderSwitcher();
    await showDashboard();
  } catch (err) {
    statusEl.classList.remove("product-mark");
    statusEl.style.display = "";
    statusEl.textContent = "Backend not reachable";
    statusEl.style.color = "red";
  }
}

checkBackend();