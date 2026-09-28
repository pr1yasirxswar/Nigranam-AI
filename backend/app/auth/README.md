# NIGRANAM-AI — MPLADS Scheme Monitoring Platform

**End-to-End Workflow & AI Verification System**

NIGRANAM-AI is an AI-powered monitoring and transparency platform built for the **Members of Parliament Local Area Development Scheme (MPLADS)**. It digitizes the entire lifecycle of an MPLADS project — from proposal to completion — and layers an automated AI verification pipeline on top of every stage, so that fraud, delays, and anomalies are caught early and routed to the right authority for action.

**Demo logins:** see [DEMO-CREDENTIALS.md](DEMO-CREDENTIALS.md)

---

## 1. Overview

MPLADS lets a Member of Parliament recommend development works (roads, schools, water supply, sanitation, etc.) in their constituency, funded by the Central Government. Historically this process has been paper-based and difficult to audit in real time.

NIGRANAM-AI replaces that with a single digital platform where:

- MPs propose projects and track them end-to-end.
- Nodal/District Authorities run tendering and sanctioning.
- Contractors execute work and upload stage-wise evidence (photos, bills, progress reports).
- An AI verification pipeline automatically checks that evidence for tampering, duplication, and financial anomalies.
- Human officials review only what the AI actually flags — not every single submission.
- Unresolved or high-risk cases automatically escalate up a jurisdictional chain (Local → District → State → MP) until someone with the authority to act does.

The result is a system where **routine, clean submissions move fast**, and **suspicious ones get real scrutiny**, instead of every case getting the same shallow manual check.

---

## 2. Actors in the System

| Actor | Role in the workflow |
|---|---|
| **Member of Parliament (MP)** | Proposes new projects; final/apex reviewer for escalated flags in their own constituency; can compare risk across constituencies. |
| **Nodal / District Authority** | Reviews proposal eligibility, runs the tender, selects the contractor, and formally sanctions the project. |
| **Contractor / Implementing Agency** | Executes the work; logs into the platform to submit stage-wise progress, photos, bills, and completion reports. |
| **Local Officials** | First line of human review for AI-flagged issues; conduct site visits and verify claims on the ground. |
| **District Authority (Escalation Hierarchy)** | Reviews cases that repeatedly fail or hit the maximum allowed extensions; investigates anomalies and decides on penalties, extensions, or contract action. |

Every actor gets their own **role-scoped dashboard** — they only ever see projects and flags within their own jurisdiction.

---

## 3. End-to-End Workflow

### Stage 1 — Proposal (Member of Parliament)
1. MP logs into the platform.
2. MP proposes a new project — submitting details, cost estimates, and location.
3. The platform captures the proposal and generates a **unique project ID**, which is used to track the project through every later stage.

### Stage 2 — Sanctioning (Nodal / District Authority)
1. The Nodal Authority receives the MP's notification and reviews the proposal for eligibility and guideline compliance.
   - **If rejected** → it's sent back for proposal revision (loop back to the MP).
   - **If approved** → it proceeds to tendering.
2. Bidding and tender is initiated directly on the platform.
3. Bids are evaluated and a contractor is selected.
4. The project is formally **sanctioned and awarded**, and becomes visible on the MP's and Nodal Authority's dashboards.

### Stage 3 — Execution (Contractor)
1. The contractor logs into the platform.
2. The contractor initiates a project stage (e.g., Mobilization, Foundation work).
3. For each stage, the contractor uploads:
   - **Geotagged photos** (start, progress, and end of the stage)
   - **Itemized bills** (invoice PDFs and cost data)
   - A **progress report** marking the stage as submitted for completion

### Stage 4 — AI Verification Pipeline (FastAPI Backend)
Every stage submission is automatically run through four independent checks:

| Module | What it checks |
|---|---|
| **Photo/Geotag Analysis** | Confirms the photo's location and timestamp actually match the project's registered site and schedule. |
| **Perceptual Image Hashing** | Detects duplicate or reused photos, and signs of tampering, across this and other projects. |
| **Financial Anomaly Detection** | Compares billed rates and amounts against historical and peer-project data to flag suspicious pricing or cost inflation. |
| **Weather API Integration** | Cross-checks any weather-related delay claims against actual historical weather data for that location and date. |

These four modules combine into a single **AI Verification Report with a confidence score**, deciding whether the stage is clean or should be flagged.

- **Clean data (no issues found)** → the stage report goes straight to Local Officials for a routine review.
- **AI-flagged issues (high-confidence)** → the case is routed for deeper scrutiny (see Stage 6).

### Stage 5 — Human Review by Local Officials
1. The Local Official/Engineer reviews the stage — combining a site visit with what's already verified on the platform.
2. They **Approve** or **Reject** the stage:
   - **Approve** → the payment installment for that stage is released, and the system checks whether all project stages are complete.
     - If **complete** → **Project Completion & Handover**.
     - If **not complete** → the contractor initiates the next project stage, and the cycle repeats.
   - **Reject** → the contractor is asked to correct defects and resubmit, re-entering the AI verification pipeline.

### Stage 6 — Escalation Hierarchy (District Authorities)
When AI flags a **high-confidence issue**, or a project keeps hitting its **maximum allowed extensions**, it's escalated beyond Local Officials:

1. The system checks: **has the maximum number of extensions been reached?**
   - If not yet — the counter continues and the project keeps going through normal cycles.
   - If reached — the case is escalated.
2. The **District Authority/Collector** reviews the escalated case.
3. They investigate the anomaly or the unverified delay claims directly.
4. A final decision is made: **penalties, an extension grant, or contract action** (e.g., termination, blacklisting) — closing that case out.

---

## 4. Multi-Tier Dashboards

Every authority level — **MP, Nodal, District, Local** — has its own dashboard showing the same underlying data, scoped to what they're responsible for:

- Summary stat cards (works recommended / sanctioned / completed, expenditure to date)
- A jurisdiction-scoped project list with risk tier badges
- A risk/status breakdown (charts)
- A review queue of flags currently awaiting that role's action

This means no authority has to dig through unrelated regions' data, and everyone is looking at a live, shared source of truth rather than separate paper trails.

---

## 5. Why This Design

- **AI does the first pass, humans make the decisions.** The system never auto-rejects or auto-penalizes — it only surfaces evidence and confidence scores; every consequential action (approve, reject, penalize) is taken by an accountable human official.
- **Escalation is automatic, not discretionary.** A case that isn't resolved at one tier doesn't just sit there — it moves up the chain on its own once a clear trigger (max extensions, high-confidence flag) is hit.
- **Jurisdiction boundaries are enforced everywhere.** No authority can see or act on projects outside their own scope — this is validated at the data layer, not just hidden in the UI.
- **Every action is logged.** The review thread on every project preserves who took what action, when, and why, which is what actually enables downstream audits.

---

## 6. Tech Stack

- **Backend:** Python, FastAPI
- **Frontend:** Vite + vanilla JavaScript
- **AI/Scoring:** Isolation Forest–based anomaly scoring, duplicate-detection embeddings, rule-based evidence modules (money, delay, duplicate, photo forensics)
- **Data:** CSV/synthetic seed dataset for demo purposes, with a migration script for a real database

---

## 7. Status

This platform is under active development. The workflow above represents the intended end-to-end design; current implementation progress may cover a subset of these stages — check the codebase's `Implementation-Guide.md` and `PRD.md` (where present) for the up-to-date phase status.
