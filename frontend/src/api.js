// Phase 5 -- shared API helper. One place that knows the backend base URL
// so every view module doesn't reimplement it.
//
// Phase 8 bug fix (Implementation-Guide.md Phase 8 item 6 / Build-Log.md
// #19 finding 2): this used to let the frontend pick ANY seeded
// X-User-Id with no credential check, which is exactly how dashboards
// ended up reachable with no real login. Identity is now a real session
// token, returned only by a successful POST /auth/login (routers/auth.py)
// and sent back as `Authorization: Bearer <token>`. This file still
// enforces nothing by itself (Rules.md/PRD.md S4.11) -- the backend
// (auth/identity.py) is the only real gate; this is just the client-side
// bookkeeping for the token plus a clean 401 -> "log in again" path.

// Bug fix: this used to be hardcoded to http://localhost:8000, which means
// after a Netlify build the deployed site would try to call "localhost"
// inside the VISITOR's own browser -- i.e. every request would fail with
// nothing but "Backend not reachable". Vite exposes any VITE_-prefixed env
// var at build time via import.meta.env; set VITE_API_BASE in Netlify's
// site settings to your Render backend URL (e.g. https://your-api.onrender.com)
// and it gets baked in at build time. Falls back to localhost so `npm run
// dev` still works unconfigured.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Security fix (stored XSS): every view builds HTML with template strings +
// innerHTML and NONE of them escape, while several fields are free text typed
// by other users (stage labels, review comments, project descriptions).
// Defense-in-depth interim fix: neutralise `<`, `>` and `"` in every string
// of every API response before any view sees it, which blocks tag injection
// in both text and double-quoted-attribute contexts. (`&` and `'` are left
// alone so names like "Jammu & Kashmir" still round-trip to the API.) The
// proper long-term fix is escaping at each render site.
function sanitizeDeep(v) {
  if (typeof v === "string") {
    return v.replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  if (Array.isArray(v)) return v.map(sanitizeDeep);
  if (v && typeof v === "object") {
    const out = {};
    for (const k of Object.keys(v)) out[k] = sanitizeDeep(v[k]);
    return out;
  }
  return v;
}

const TOKEN_KEY = "sentinel_session_token";
const USER_KEY = "sentinel_session_user"; // { x_user_id, name, role }

export function getCurrentUser() {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// Kept for the view modules that only ever needed "is someone logged in,
// and as whom" -- same external shape as before, backed by the session
// user now instead of a bare client-chosen id.
export function getCurrentUserId() {
  return getCurrentUser()?.x_user_id ?? null;
}

function getToken() {
  return localStorage.getItem(TOKEN_KEY) || null;
}

function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export async function login(xUserId, password) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x_user_id: xUserId, password }),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = sanitizeDeep((body && typeof body.detail === "string" && body.detail) || "Login failed.");
    throw new Error(detail);
  }
  const safeUser = sanitizeDeep(body.user);
  setSession(body.token, safeUser);
  return safeUser;
}

export async function logout() {
  const token = getToken();
  clearSession();
  if (!token) return;
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    // Session is already cleared client-side either way -- a failed
    // logout call server-side just means the token expires on its own.
  }
}

// Stage 2c -- public area-lookup flow (routers/public.py), no OTP/session
// involved at all: state -> district -> that district's project list.
// Deliberately NOT routed through apiFetch()'s Authorization-header/401-
// session-clearing logic above -- this is the no-login public flow, there
// is no session to clear either way.
async function _publicFetch(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  let body = null;
  try {
    body = await res.json();
  } catch {
    // no/invalid JSON body
  }
  if (!res.ok) {
    const detail = sanitizeDeep((body && typeof body.detail === "string" && body.detail) || res.statusText || "Request failed");
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return sanitizeDeep(body);
}

// { state: [district, ...], ... }, straight from the works data -- used to
// populate the state dropdown, then the matching district dropdown.
export async function fetchPublicDistricts() {
  return _publicFetch("/public/districts");
}

export async function fetchPublicAreaProjects(state, district) {
  const qs = new URLSearchParams({ state, district });
  return _publicFetch(`/public/area-projects?${qs.toString()}`);
}

export async function apiFetch(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  let body = null;
  try {
    body = await res.json();
  } catch {
    // no/invalid JSON body -- fine for some 2xx responses
  }
  if (res.status === 401) {
    // Session missing/invalid/expired server-side -- don't keep sending a
    // dead token on every subsequent call; drop it so the UI falls back
    // to the logged-out state and prompts a fresh login.
    clearSession();
  }
  if (!res.ok) {
    const detail = sanitizeDeep((body && typeof body.detail === "string" && body.detail) || res.statusText || "Request failed");
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return sanitizeDeep(body);
}