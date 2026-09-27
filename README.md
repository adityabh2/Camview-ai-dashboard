# CAMVIEW COMMAND CENTER

**Alarm Intelligence • Live Operations • Investigation • Collaboration**

An enterprise alarm-operations platform built on Camview's `POST /alarms/listAlarms`
API. It turns raw alarms into a controlled, audited workflow:

```
ALERT ARRIVES → exam, client & location resolved automatically → human decides VALID / INVALID / EXCEPTION
      → VALID creates the ticket (once) → routed to the exam's client (automatic, or one-click Send)
```

**The system does the work; the person only decides — or nobody does.** With
`CAMVIEW_DELIVERY_TRIGGER=arrival` (Settings › Workflow › *On arrival*) every detection alert
is delivered to the exam's client the moment Camview sends it — a 12:00 alert reaches the
client at 12:00, not when someone marks it VALID hours later. Camera status events are never
delivered; INVALID / EXCEPTION (from Camview or an operator) withdraws an alert. With
`valid` the classic flow applies: delivery after Camview or an operator marks it VALID.
No forms, no mandatory notes,
no manual ticket creation, no choosing the client (unless an exam genuinely has several).
The full enterprise toolset (investigations, manual sharing workflow, analytics, rules…)
is still there under **Advanced**.

It is built for operators, supervisors, investigators, managers, administrators
and **client representatives**, with one hard rule running through everything:

> **Internal operational data is separate from client-visible information.
> A *valid* alarm is never automatically *shared*.**

---

## Contents

0. [The review flow — Dashboard, Alerts, Tickets, Exams](#0-the-review-flow--dashboard-alerts-tickets-exams)
1. [Quick start (demo, 2 minutes)](#1-quick-start-demo-2-minutes)
2. [Going live with real Camview data](#2-going-live-with-real-camview-data)
3. [Core concepts — four separate states](#3-core-concepts--four-separate-states)
4. [Roles, permissions and scope](#4-roles-permissions-and-scope)
5. [Tour of the product](#5-tour-of-the-product)
6. [Client sharing workflow](#6-client-sharing-workflow)
7. [Client portal](#7-client-portal)
8. [Intelligent alerts, rules, schedules, escalation](#8-intelligent-alerts-rules-schedules-escalation)
9. [Live data & freshness](#9-live-data--freshness)
10. [Analytics & reports](#10-analytics--reports)
11. [Nomenclature master data](#11-nomenclature-master-data)
12. [Audit trail](#12-audit-trail)
13. [Configuration reference](#13-configuration-reference)
14. [Architecture](#14-architecture)
15. [Security model](#15-security-model)
16. [API reference](#16-api-reference)
17. [Testing](#17-testing)
18. [Deployment](#18-deployment)
19. [What is real, what is V2 (no fake features)](#19-what-is-real-what-is-v2-no-fake-features)
20. [Troubleshooting](#20-troubleshooting)

---

## 0. The review flow — Dashboard, Alerts, Tickets, Exams

Main navigation: **Dashboard · Alerts · Tickets · Clients · Exams · Reports ·
Administration** (everything else is under **Advanced** / Administration).

| Screen | What it does |
|---|---|
| **Dashboard** (home) | ALERTS TODAY · PENDING · VALID · INVALID · EXCEPTION (every verdict, with today's share) · DELIVERED TO CLIENTS · **CAMERAS OFFLINE** · **CAMERA STATUS EVENTS**, **Alerts per hour** (stacked by verdict) and **Busiest alert types** charts, an **Alerts by type** table (how many of each type today, per verdict), a banner when tickets wait to be sent, the 12 highest-priority pending alerts as cards, and every exam with its pending count. Clients never see this page: their portal shows only the alerts delivered to them. |
| **Alerts** | **Today by default** — pick a date (or *All dates*) to see earlier days. One queue across **all exams, clients and projects** you may see; the project switcher in the top bar appears only for people with access to more than one project. Two kinds of record, one at a time: **Alerts** (detections — each row shows the **alert frame** Camview attached, with a play marker when a clip exists) and **Camera status events** (Camview's camera online / offline events, which carry no image or video and would otherwise bury the alerts). Tabs Pending / Valid / Invalid / Exception / All; filters client, exam, priority, centre, camera, date. **Sorted automatically**: priority → most recent → most repeated. Refreshes by itself; new alerts are marked NEW in place. |
| **Alert review** (`#/alerts/:id`) | **Evidence first**: video player (play / pause / seek / volume / speed / fullscreen) and the alert frame with **what the AI detected drawn on it** (labels, confidence and boxes from Camview's metadata file, e.g. *person ×3*). Images only: a stage with zoom + filmstrip and an optional *snapshot sequence* (clearly labelled — not a video). Missing or broken files show **VIDEO UNAVAILABLE / IMAGE UNAVAILABLE** without breaking the page. A **camera status event** says so (status and time as Camview reported them) and shows the **latest frame and clip Camview has from that camera**, clearly labelled as coming from another alert. Context resolved automatically: Client, Exam, Project, TEC, TC, Centre, Building, Floor, Room, Camera (Camview's own camera number, device id and sub-location). A fixed bottom bar with **VALID · INVALID · EXCEPTION** (keys V / I / E, N = next). |
| **Tickets** | One ticket per VALID alert (`TKT-000123`) — never duplicated, even on double-click, retry or reload. Tabs Ready to send / Choose client / Delivered / Withdrawn / Not deliverable; **Send** (one click, or *Send all ready*) and **Withdraw**. |
| **Exams** | Exam ↔ client ↔ project mapping (optional date window when several exams share a project). This is what makes routing automatic. |
| **Administration** | Setup (exams, clients, settings, users, roles, nomenclature, rules), monitoring (centre health board, data quality, audit) and the advanced tools. |

**Who sees what (production default, `clientsSeeOperatorValidOnly`)**

| Audience | Sees |
|---|---|
| Administrators and the operations team | Every alert and every verdict: pending, VALID, INVALID, EXCEPTION. *Alerts › Decided by* separates decisions made by the team from statuses that only come from Camview, and each decided row names the operator. |
| Clients | **Only alerts an operator of the backend team marked VALID.** Camview's own VALID status, delivery on arrival and automatic sharing never make an alert client-visible. This is enforced in the client firewall (portal, alert detail, evidence, analytics, reports), so nothing slips through even if a delivery record exists. |
| Camera online / offline events | Counted in the *Cameras offline* / *Camera status events* KPIs and camera health only. They cannot be decided and never become tickets. |

When the rule is on (the default), anything delivered earlier without an operator VALID is withdrawn and its automatic ticket cancelled, quietly and once (audit action `delivery.policy_enforced`). Settings › Workflow can switch the rule off; the earlier delivery modes below then apply again.

**What each decision does**

| Decision | Result |
|---|---|
| **VALID** | Ticket created (idempotent) → exam + client resolved → **automatic** mode: delivered to the client immediately; **controlled** mode (default): *Ready to send*, one click on **Send**. If the exam has several clients, the reviewer picks one — the only time a choice is asked. |
| **INVALID** | Never delivered. If it had been delivered, it is withdrawn and the ticket cancelled. |
| **EXCEPTION** | Not delivered; kept in the Exception tab / KPI for monitoring. |

Changing a decision is allowed (VALID → INVALID withdraws it from the client; back to
VALID re-opens the **same** ticket). After a decision the screen moves to the next
pending alert automatically (switchable). Live refresh never moves the alert you are
reviewing.

**Settings › Workflow › Client delivery**: delivery mode (automatic / controlled),
evidence sent to clients (all / first / none), *require a remark* (off by default —
decisions are one click), and *sender ≠ validator* for controlled mode.

**Client portal**: *My exams* tiles, alerts filterable by exam, and each alert shows the
exam name, ticket reference, images/video and client-safe context only — never internal
notes, operator names or internal states.

Demo data: 4 projects, 3 clients, 4 exams (*SRE 2026 — Prelims / Skill Test* → State
Recruitment Board, *UET 2026* → University Examinations Cell, *NNC 2026* → National
Nursing Council). Evidence frames are synthetic, watermarked **DEMO**.

---

### Camera & recording health (real source only)

An alarm titled "Camera Online" is an *event*, not proof of the camera's current state, so
health is a separate model (`backend/health.py`): `camera.state` online/offline/unknown,
`recording.state` recording/not_recording/unknown, `stream.state`, plus machine-readable
conditions (`CAMERA_OFFLINE`, `FRAME_SYNC_FAILED`, `CAMERA_ONLINE_NO_RECORDING`, `HEARTBEAT_STALE`, …).

**Source 1 — Camview (automatic in live mode).** Camview sends the camera's connection with
every alarm: `camera.frameSyncStatus` (`SYNCED` → online, `OFFLINE` → offline, `FAILED` →
unknown + FRAME SYNC FAILED) and `camera.lastFrameSync` (shown as *Last frame*). Every live
refresh writes the latest value per camera; the heartbeat is the time Camview reported it, so
the state becomes UNKNOWN / STATUS STALE if the feed stops. Camview does not report recording,
so recording stays UNKNOWN. When a camera goes from SYNCED to OFFLINE between two refreshes,
everyone who reviews alerts for that project gets an in-app notification **Camera offline:
<centre code - sub-location - camera number>** (once per camera per day), the Intelligent
Alert Center raises **N camera(s) offline** (severity high), and the Dashboard KPI
**Cameras offline** links to Monitoring › Camera & recording health, which lists offline
cameras first with Camview's naming.

**Camera status events.** Camview's own camera online / offline records (alarm type 10 in the
observed data: `alarmMetadata.status` + `reason`, no `alarmId`, no image or video) are kept
**out of the alert queue** and listed under **Alerts › Camera status events**, where they
can still be reviewed. They never count in the alert KPIs.

**Source 2 — a monitoring system (NVR / VMS / heartbeat service)**, authoritative when
configured (Camview's status is then not written): set
`CAMVIEW_HEALTH_INGEST_TOKEN` in the server `.env` and POST

```
POST /api/camera-health/ingest     Authorization: <token>
{"cameras":[{"projectId":34,"cameraId":9522,"cameraState":"online","recordingState":"not_recording",
             "streamState":"available","lastHeartbeatAt":"2026-09-26T16:51:44Z","lastRecordingAt":"2026-09-26T16:32:10Z"}]}
```

Heartbeats older than `CAMVIEW_HEALTH_STALE_SECONDS` (300) show as UNKNOWN / STATUS STALE.
Status: Settings › Camera health.

### Live data specifics

* Locations are built automatically from Camview camera data (`tcCode`, `centerCode`, `center`,
  `city`, `state`, `subLocation`, `cameraNumber`) — Project › [TC] › Centre › Sub-location › Camera,
  exactly the levels Camview sends: **no TEC, building or floor is ever created or shown for
  Camview-built context**, and completeness / data quality are measured against the levels a
  project really has (a Camview camera with project, centre, sub-location and camera is 100 %).
  The centre name carries the city and state Camview sends. Imported master data may add TEC,
  building and floor and always wins over the automatic tree. The project's **code is the exam's own code** when configured
  (`CAMVIEW_PROJECT_CODES=2773:MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL`, or Settings › Monitored
  projects › *Save code*; Camview's API sends only the number), otherwise Camview's project id
  (e.g. `2773`); the camera node's
  **code is Camview's own camera number** (e.g. `1001902_0`) and its name Camview's sub-location
  (e.g. `Camera1`); nothing is invented. Name projects in Nomenclature (select the project › *Add name*).
* **Which projects are monitored**: the configured `CAMVIEW_PROJECT_ID`, the ids added in
  Settings › Monitored projects, and projects from imported master data. A project whose tree was
  built automatically from Camview data does not keep itself monitored: change the configured
  project and the old one disappears from every screen; `CAMVIEW_EXCLUDED_PROJECTS=1,34` keeps
  listed ids out of monitoring, the extra list and the project scan for good; its auto-built tree is removed
  automatically at start-up and on the next refresh (audited; reviews, tickets and the audit
  trail stay). Settings › Monitored projects lists each
  monitored project with why it is monitored and its newest alert, and lists old auto-built
  projects with a **Delete its data** button (reviews, tickets and the audit trail are kept).
* **Evidence on every list.** Every record that lists an alert (Alerts queue, Dashboard cards,
  Live Operations table, My Work, camera and investigation pages, tickets, and the client
  portal's lists and cards) carries the alert frame with a play marker when a clip exists;
  the review screen and the client alert page play the clip. Frames need the `evidence.view`
  permission (clients: `client.evidence`, through the evidence proxy).
* **Project codes.** Camview's API sends only the project number, and most keys may not read
  Camview's project record (HTTP 403). Map ids to the exam's own codes in Settings › Monitored
  projects (per project, or paste a list `id, code[, name]`), or with `CAMVIEW_PROJECT_CODES`.
  When the key may read `/projects/getProject`, the code is taken from Camview automatically.
  **The client and the exam follow from the code** (policy `autoExams`, on by default):
  `MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL` → client **MPESB** (created if missing), exam
  **MPESB/G2SG4-CRT** (the year is dropped from the name), project 2773 attached, exam date
  22 Sep 2026. Another project with `MPESB/G2SG4-CRT-2026/230926/…` (another date or shift)
  joins the same exam; `MPESB/G2SG4-CRT-2025/…` is a different exam and keeps its year in the
  name, so exam names never collide. A project already mapped to an exam is never changed.
* **Times.** Camview timestamps are UTC (`…Z`); the UI shows them in the viewer's time zone.
* **Reports** are two choices: the exam, and who it is for — *the client* (only the alerts
  delivered to that client, shareable) or *our team* (every alert of the exam with every verdict).
* **Location naming follows the data**: with imported master data `PROJECT CODE - TC CODE - CAMERA
  CODE`; with Camview data only `CENTRE CODE - SUB-LOCATION - CAMERA NUMBER` (exactly the fields
  Camview sends, e.g. `43023501 - Camera1 - 1001902_0`, with the centre name on the next line);
  `PROJECT-<id> - TC not mapped - CAM-<id>` only when Camview sent no location at all.
* Every detection alert arrives with one `imageUrl` (the alert frame), one `videoUrl` (H.264
  MP4 clip) and a `metadataUrl` (AI labels, confidences and boxes). The review screen loads the
  metadata lazily when an alert is opened and draws the boxes on the frame. Alert type names
  are not sent by Camview. On the first live start the Dictionary is seeded with names read from
  the alerts themselves (AI labels and frames): 1 Person Movement · 2 Vehicle & Person Detected ·
  3 Trunk Open / Closed · 4 Trunk Changed · 5 Vehicle Movement · 6 Server Room Activity · 10 Camera
  Status · 11 Camera Tampering · 12 Lab Activity · 14 Mobile Phone Detected · 15 Candidate Standing.
  They are marked as inferred: confirm or rename them in Nomenclature › Dictionary
  (`CAMVIEW_ALARM_TYPE_NAMES` overrides them).
* Camview media links are signed for 7 days; the client evidence proxy always uses the fresh
  link from the live feed.
* Camview's pages are not sorted and shift while a busy project is read page by page, so one
  read can return a record twice and skip another. Duplicates are dropped; a record that was in
  the window and is skipped by a read is kept with its last data for 5 refreshes (Camview never
  deletes alarms), so an alert being reviewed never becomes "not available" between refreshes.
* Camview sends priority 1 on every record of a project. "Critical" therefore only means
  something once an administrator has confirmed the priority mapping (Nomenclature ›
  Dictionary): until then no new alert is announced as critical, no "critical pending" intelligent
  alert is raised, and camera status events are never announced as alarms. Alert type names are not sent either: type 11 records sometimes
  carry a metadata reason such as `no_flip`; that is shown as the alert's detail, never as the
  type's name.
* The top bar shows the worst feed across all projects: **● LIVE**, **⚠ DATA DELAYED**, or
  **✕ LIVE DATA DISCONNECTED**. LIVE means the connection works; whether Camview is still
  *producing* alerts is shown separately: after one day without a new alert the top bar adds
  **NO NEW ALERTS for N days** and the Dashboard shows a banner with the newest alert's date.
  Old alerts are never presented as new.
* **Finding the running project.** Camview has no "list projects" call and its pages are not
  time-ordered. Settings › Monitored projects › *Find the running project* scans a range of
  project ids with your key and lists every readable project with its alert count, camera count
  and newest alert (complete for projects up to 500 alerts, sampled for larger ones), newest first,
  with a **Monitor** button. Check the *Newest alert* column: a project whose newest alert is
  months old has ended, whatever the LIVE indicator says. Project ids grow over time (roughly
  33 per day in the observed data), so the exam running today has one of the highest ids:
  scan the highest range first (the form defaults to 1500–3500; raise it as time goes on).

---

## 1. Quick start (demo, 2 minutes)

Requires **Python 3.10+**.

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** and sign in as **`admin` / `admin12345`** (Super Admin —
set by `CAMVIEW_ADMIN_EMAIL` / `CAMVIEW_ADMIN_PASSWORD`; change the password in the
user menu). Create further users in **Users**.

Optional one-click demo accounts for every role (password `demo`) are created only when
`CAMVIEW_DEMO_USERS=1`:

| Account | Role | Sees |
|---|---|---|
| admin@demo.camview | Super Admin | everything, incl. roles & settings |
| it.admin@demo.camview | Administrator | users, clients, settings, master data |
| manager@demo.camview | Manager | executive overview, analytics, reports, approve & publish |
| supervisor@demo.camview | Supervisor | review queue, approvals, client sharing, shift |
| supervisor.p12@demo.camview | Supervisor | **Project 12 only** |
| operator@demo.camview | Operator | **Project 07 only** — monitor, investigate, validate |
| operator.tec04@demo.camview | Operator | **TEC-04 only** (narrow scope) |
| investigator@demo.camview | Investigator | investigations, evidence, analytics |
| client.admin@client-a.demo | Client Admin (State Recruitment Board) | client portal: shared alerts, evidence, reports, acknowledge |
| viewer@client-a.demo | Client Viewer (same client) | read-only client portal |
| user@client-b.demo | Client User (University Examinations Cell) | only Client B's alerts |

Demo mode uses its **own database** (`backend/camview-demo.db`) and is always
labelled **DEMO DATA**. It never mixes with live data. The seeded story matches the
product demo: *Project 07 — 40 alarms validated by operators → 15 approved → 8 shared
with Client A (2 acknowledged), 3 awaiting approval, 1 withdrawn.* The Client A portal
therefore shows exactly **8** alerts — nothing else leaks.

**The 1-minute demo (automated flow):** sign in as **admin** → **Dashboard** → click a
critical card → watch the evidence → press **V** → the ticket is created and shown as
*Ready to send to …* → **Send now** → sign in as **client.admin@client-a.demo** (needs
`CAMVIEW_DEMO_USERS=1`) → *My exams* → the alert with its exam and ticket reference.

**The 3-minute demo — manual workflow (Advanced)** (spec §129/§157):
1. Sign in as **operator** → Command Center → *Attention required* shows a critical
   pending alarm → **Investigate**.
2. Review context (Project / TEC / TC / Centre / Room / Camera), evidence (click to
   zoom), timeline, related activity → **Mark valid**. It is now *Ready for client* —
   still **Internal**.
3. **Request review** for the client.
4. Sign in as **supervisor** → My Work → *Approval requests* → **Approve**
   (four-eyes: the operator who validated cannot approve).
5. **Review & share** → the client-safe preview shows exactly what the client will
   see, what stays internal, which evidence is shared, the recipients → confirm.
6. Sign in as **client.admin@client-a.demo** → the alert appears, with only the
   approved context, evidence and summary → **Acknowledge**.
7. Back as supervisor: the investigation shows *Client acknowledged* and the full
   audit lifecycle. Open **Analytics** and **Presentation Mode**.

---

## 2. Going live with real Camview data

1. Sign in as a Super Admin → **Settings › Connection** (from the computer running the
   backend — the form is localhost-only to protect the key):
   * **API URL** — prod `https://default.prod.api.camviewai.com/alarms/listAlarms`
     or dev `https://default.dev.api.camviewai.com/alarms/listAlarms` (no `/api` prefix).
   * **API key** — the raw scoped key (no `Bearer`). Saved to `backend/.env`, never
     returned to the browser.
   * **Default project** — the numeric `projectId`.
   * **Test connection** — one real call; shows total alarms, latency, the alarm type
     IDs and camera fields Camview returned.
2. Switch **Mode → Live**. Live mode uses `backend/camview.db`. On the first live start
   a Super Admin is created (`CAMVIEW_ADMIN_EMAIL` / `CAMVIEW_ADMIN_PASSWORD`, or a
   random password printed once in the console) — sign in and change it.
3. **Nomenclature › Import** your master data (CSV/JSON, [§11](#11-nomenclature-master-data)).
   Without it, alarms still work but show `UNMAPPED` context — nothing is invented.
4. **Nomenclature › Dictionary** — name the numeric alarm types and **confirm the
   priority mapping** (Camview doesn't document what priority 1–4 mean; labels show
   "not confirmed" until you confirm them).
5. **Clients** — create clients and assign their projects. **Users** — create users,
   choose roles and scope (projects / TECs / TCs / centres / cameras).

> Status of the key provided so far: Camview returned `403 Unable to authenticate via
> the provided token` on both prod and dev, so this installation is set to
> `CAMVIEW_MODE=demo` in `backend/.env`. Ask the Camview team to activate the key,
> then switch to Live in Settings.

---

## 3. Core concepts — four separate states

The product never merges these into one "status":

| Concept | Source | Values | Who changes it |
|---|---|---|---|
| **Alarm state** | Camview (`lastActionType`, `alarmState`) | 0 Pending · 1 Valid · 2 Invalid · 3 Exception | Camview only — **read-only here** |
| **Ops validation** | Command Center | Unreviewed · Acknowledged · Valid · Invalid · Exception | Operators (`alarm.validate` / `invalidate` / `exception`) |
| **Workflow state** | Derived | New · Under review · Investigating · Ready for client · Awaiting approval · Approved · Shared · Client acknowledged · Withdrawn · Closed | Follows from the two rows above + sharing |
| **Client visibility** | Command Center, **per client** | Internal · Ready for review · Approved · Shared · Withdrawn · Archived | Approvers / publishers only |

Every screen shows them separately (the investigation header has four boxes). An
alarm can be **Valid** and **Internal** at the same time — that is the normal state
until someone explicitly publishes it.

**Provenance tags** appear next to data: `DIRECT` (from Camview), `DERIVED`
(calculated by Command Center — every derived item explains itself), `UNAVAILABLE`
(not in the data). There is no `AI` content — AI is a disabled V2 feature.

---

## 4. Roles, permissions and scope

Roles and their permissions live in the database and are edited in **Roles &
Permissions** (matrix view). Nothing is hard-coded in the UI; the server checks
every request.

Default roles: Super Admin, Administrator, Manager, Supervisor, Operator,
Investigator, Client Admin, Client User, Client Viewer.

Permissions (46): `dashboard.view, live.view, work.view, alarm.view,
alarm.investigate, alarm.validate, alarm.invalidate, alarm.exception, alarm.assign,
alarm.comment, alarm.export, alarm.approve, alarm.publish, alarm.withdraw,
evidence.view, evidence.download, camera.view, analytics.view, history.view,
report.view, report.generate, report.export, report.share, nomenclature.view,
nomenclature.manage, project.view, alert.view, alert.manage, shift.view,
shift.handover, presentation.view, notification.view, audit.view, user.view,
user.manage, role.view, role.manage, client.view, client.manage, settings.view,
settings.manage, client.portal, client.acknowledge, client.evidence,
client.report.view, client.analytics`.

**Audience wall.** Every role is `internal` or `client`. A client role can never hold
an internal permission (the server strips it even if someone adds it), and internal
endpoints answer client users with *404 Resource unavailable* regardless of
permissions.

**Scope.** Internal users can be limited to `global`, or any mix of `project`,
`tec`, `tc`, `centre`, `camera` (e.g. *User A: Project 1*, *User B: TEC-04*). Every
list, count, search result, alert, export and report is filtered to that scope.
Client users see only their client's assigned projects **and** only published alerts.

---

## 5. Tour of the product

| Page | Question it answers | Highlights |
|---|---|---|
| **Command Center** | What is happening? | Role-aware layout (operator / supervisor / manager), KPI strip (Total, Critical, Pending, Valid, Invalid, Exceptions, Suppressed, Ready for review, Ready for client, Approved, Shared), *Attention required*, review queue tabs, client sharing queue, activity chart, status/priority/shift distributions, most active cameras, recent alarms, operational insights, data freshness |
| **Live Operations** | What is happening right now? | Polling with LIVE/DELAYED/DISCONNECTED, pause (keeps data + "New activity available"), quick filters, advanced filters, search, sort, paging, table/card views, saved views, CSV export (permission + audited), bulk "review for client" |
| **My Work** | What needs *my* attention? | Pending review, my investigations, critical, approval requests, client sharing, evidence to review, escalations, rule-flagged items, reports |
| **Review Queue** | What is waiting in the workflow? | Pending · Under investigation · Ready for approval · Ready for client · Recently shared · Withdrawn |
| **Watchlist** | What am I keeping an eye on? | Watch alarms, cameras, projects, TECs, TCs, centres, rooms; live counts; a notification when a watched item gets a new alarm |
| **Daily Brief** | How did the day go? | Totals, critical, pending, validated, shared, peak period, highest activity, open investigations — derived, printable |
| **Activity Replay** | In what order did it happen? | Chronological replay of recorded alarms for a period — play / pause / next / previous |
| **Intelligent Alerts** | What stands out? | Operational / Investigation / Approval / Client sharing / System tabs, every alert with *Why am I seeing this?* |
| **Investigations** | What happened? | `/investigations/:id` workspace — four states, lifecycle, summary, nomenclature context, evidence focus mode, timeline, occurrences, related activity (each relation explained), internal notes, review, assignment, per-client sharing actions, client-safe summary, audit trail, investigation report; index with assigned / investigating / bookmarked; **Compare** two alarms |
| **Shift Control** | What is the state of this shift? | Current shift, pending / critical / investigations / approvals, handover with notes + snapshot, history |
| **Client Sharing** | What is approved to be shared? | Candidates, Ready for review, Approved, Shared, Withdrawn; filters; eligibility; preview; publish; withdraw; controlled bulk |
| **Clients** | Who can see what? | Client profile, projects, users, shared/acknowledged counts, visibility policy, notification preferences |
| **Cameras** | Which cameras are active? | Neutral rankings, camera detail (activity, history, evidence, investigations, context) |
| **Nomenclature** | Where exactly did it happen? | Context Explorer tree (Project → … → Camera → alarms), data quality, alarm type dictionary, priority configuration, import/export |
| **Evidence** | What evidence exists? | Evidence grid, focus mode (zoom, pan, keyboard, fullscreen, video), shared-evidence markers |
| **Analytics** | What patterns exist? | Range picker, volume, status, priority, type, shift, TEC/TC/centre, cameras, recurrence, suppression, client-shared activity, **heatmap** (day × hour) and **calendar** (click → history), Camview-vs-Ops verdicts |
| **Alarm History** | What happened before? | Camview's documented history query (`useHistory`, `startTime`, `endTime`, `lastKey`), URL-state filters |
| **Reports** | What should management / the client receive? | Report builder, preview, generate, export, share (client reports only) |
| **Alert Inbox** | What happened to my work? | All · Critical · Unread · Assigned · Pending · Approval · Client · System; mark read, archive, **snooze** 15/30/60 min (critical can't be snoozed) |
| **Data Quality** | What is wrong with the data? | Mapped vs seen cameras, unmapped cameras, missing project/TEC/TC/centre/room, unknown alarm types, duplicates, missing timestamps/evidence |
| **Preferences** | How do I like to work? | Theme, refresh interval, table density, display time zone, presentation cycle, notification preferences (on/off, critical only, sound — off by default, browser notifications) |
| **Presentation Mode** | The big-screen view | Internal or client version (client sees client data only), auto-refresh, fullscreen |
| **Alert Rules** | What should trigger attention? | Visual rule builder (event → conditions → scope → severity → recipients → channel → escalation → action), dry-run, schedules/event phases, templates |
| **Users / Roles / Audit / Settings** | Administration | Users with scopes, permission matrix, append-only audit, policy, thresholds, escalation, connection, mode, system status, feature flags |

**Noise reduction — activity groups.** Live Operations has a *Grouped* view: alarms on
the same camera (or room / centre) whose times are no more than N minutes apart form
one **activity group** (e.g. *CAM-102 · 12 reports · 10:00–10:08*). The header shows
*"365 raw alarms → 210 groups"*, the grouping rule is stated, every group expands to
**all** its raw alarms, and each group has an **impact view** (projects, TECs, TCs,
centres, rooms and cameras affected, count, time window). Nothing is deleted or hidden.

**One-click case view.** The investigation page is a single scrolling case with a
section navigator: *Alarm → Context (with a context-completeness bar — a data-quality
measure, not a confidence score) → Why alert (the intelligent alerts that reference
this alarm) → Evidence → Timeline → Related activity → Workflow → Client visibility*
(per-client status, share history, and the client conversation).

**Since last visit.** The Command Center compares with your previous sign-in: new
alarms, new critical, newly validated, new client shares, approval requests,
acknowledgements — and says so when your last visit is older than the monitored window.

Keyboard: **Ctrl K** search & commands, **/** focus the page search, **Enter** opens
a focused row, **Esc** closes dialogs, **← →** / **+ − 0** / **F** in the evidence
viewer, **?** shows shortcuts.

---

## 6. Client sharing workflow

```
Alarm → (operator) Mark valid → Ready for client  [still INTERNAL]
      → Request review            → READY FOR REVIEW
      → (supervisor) Approve      → APPROVED          [four-eyes: not the validator/requester]
      → Review & share            → client-safe preview → explicit confirmation → SHARED
      → (client) Viewed / Acknowledged (+ comment)
      → Withdraw (reason, confirmed) → client loses access immediately
```

**Eligibility engine** (`workflow.client_share_eligible`) — every rule is shown as a
✓/✕ check, and re-checked on the server:
permission · alarm counts as valid (policy: Camview, Ops or either; an Ops
*invalid/exception* always blocks) · client active · project assigned to the client ·
alarm in the user's scope · alarm type's client-share policy (Dictionary:
allowed/review/never) · optional: mapped context, evidence required · correct
current state · four-eyes.

**Client-safe preview** (the Sharing Review Screen) shows: alarm information, the
location levels the client may see (tick per level), **granular evidence** (tick per
image/video — unticked items stay INTERNAL and are never served), the editable
client-safe summary (template, not AI), the client and every recipient, a list of
**internal information NOT shared**, the publishing scope and the approval status.
Publishing then needs a second explicit confirmation.

**Bulk**: select alarms → the dialog shows *Selected / Valid / Not valid / Eligible /
Not eligible* with the reason for each → only eligible alarms are sent for approval.
Bulk *publishing* is deliberately impossible: each publish needs its own preview.

Policy (Settings › Workflow): two-step approval, four-eyes, valid source, evidence
required, context required, default client context levels, ticket visibility.

**Client conversation.** On a shared alert, client users (with `client.acknowledge`)
can *comment* or *request clarification*; users who can publish/approve reply with a
*response*. The thread is visible to both sides for that alert and client only, is
audited, notifies the other side, and never contains internal notes. Client status is
tracked separately as **Viewed · Acknowledged · Commented · Responded**.

---

## 7. Client portal

Client users land on `#/client`: shared alerts, critical shared, awaiting
acknowledgement, recent activity, evidence, reports, notifications, profile, a
client presentation mode and client analytics.

Everything comes from the **client dataset** produced by
`workflow.build_client_visible_alarm()`, which:
1. checks the user is a client user with `client.portal`;
2. checks the publication belongs to the user's client and to a project the client is
   **currently** assigned (removing a project revokes access immediately);
3. checks the publication is **shared** (not requested/approved/withdrawn);
4. copies only allow-listed fields (ID, type, priority, times, occurrences, shift);
5. includes only the approved context levels;
6. includes only evidence items marked shared — served through
   `/api/client/evidence/...` so the internal URL never reaches the client;
7. never includes internal notes, Ops decisions, workflow, assignment, Camview codes
   or audit data.

Client analytics, client reports and client presentation mode are computed only from
this dataset (if a client can see 8 of 100 alarms, its analytics are based on 8).
Hidden, withdrawn, other-client and non-existent alerts all return the same *Alert not
available*.

---

## 8. Intelligent alerts, rules, schedules, escalation

Built-in explainable alerts (`backend/intelligence.py`) — each has title, scope
(camera/room/centre/…/global), severity, timestamp, **reasons**, **inputs and
thresholds**, related alarms and an action:

| Alert | Logic (all thresholds configurable) |
|---|---|
| Critical pending | priority critical + Camview pending + no Ops decision |
| Repeated activity | `totalTimesReported ≥ 3` within 15 min (first → last instance) |
| High camera activity | ≥ 5 alarms on one camera in 60 min |
| Multiple related alarms | ≥ 3 alarms from ≥ 2 cameras in the same room/centre in 15 min (**only with nomenclature**) |
| Activity spike | last hour ≥ 2× the hourly baseline of the previous ≤ 24 h (needs ≥ 6 h history) |
| Activity surge | ≥ 20 alarms within 60 s |
| Suppression activity | ≥ 3 suppressed alarms in the last hour |
| Long pending | only when a pending limit is configured (no SLA is assumed) |
| Approval required / Client sharing required / Ready for client | workflow counts |
| Evidence available | pending critical/high alarms with evidence |
| Connection problem / Data quality | failed refresh / unmapped cameras |
| **Rules** | your own rules from the rule builder |

Checks that lack data are *skipped with a stated reason* (visible on the Alerts page),
never guessed. There are no risk scores.

**Alert rules**: conditions on priority, Camview state, alarm type, workflow, client
visibility, occurrences, age, evidence, suppressed, shift, TEC/TC/centre/camera;
scope; severity; recipient roles; channel (**in-app only** — email/SMS/push are shown
as not integrated); optional escalation; dry-run shows current matches.

**Rule studio**: every save creates a new **version** (v1 Active → v2 Draft → v3
Disabled …) with who, when and which fields changed; drafts and disabled rules never
fire. The **rule tester** runs a rule over the last 1 h … 14 days and lists the matching
alarms without changing anything. **Recipient groups** (e.g. *Control Room*,
*Supervisor Team*) combine internal roles and users and can be used as rule/schedule
recipients.

**Schedules / event phases**: PRE_EVENT, EVENT_START, DURING_EVENT,
BEFORE_EVENT_END, EVENT_END, POST_EVENT, SHIFT_HANDOVER with templates and recipient
roles; each fires once as an in-app notification.

**Escalation policy**: steps like *after 15 min → Supervisor, after 45 min → Manager*
for critical pending alarms; none are assumed in live mode.

Notifications are only created by real events: approval requested, approved, shared,
withdrawn, client acknowledged, assignment, new critical alarm received, refresh
failure, schedule, escalation, handover, report shared.

---

## 9. Live data & freshness

* **Screens update the moment data changes — push, with polling as the fallback.**
  The server keeps one **data version** (`backend/changes.py`): it moves whenever a
  Camview refresh brought *different* alarm data (new alert, status change, repeat
  count, camera connection — re-signed media links alone do not count) and whenever
  Command Center data is written (a decision, a ticket sent or withdrawn, a
  notification, master data). The browser holds one `/api/events` stream
  (Server-Sent Events) that carries only this number; when it changes, every open
  screen re-reads what it shows through the normal API — scope and permissions stay
  where they are enforced, and a burst of changes (auto-share of 100 tickets) becomes
  one re-read. The stream carries no data itself. When it cannot be opened (a proxy
  that buffers, an old browser) nothing breaks: the browser keeps polling
  `/api/status` at the chosen interval (default 15 s, profile menu) and compares the
  version in the answer. The freshness pill in the top bar shows a ⚡ when push
  updates are connected; hovering it tells when the screen last re-read its data.
* **Nothing is re-rendered for nothing.** A screen re-reads only when the version
  moved (or once a minute, so relative times stay right). The Dashboard and the Alerts
  queue are patched in place: numbers that changed flash briefly, charts glide to their
  new values, rows that stayed keep their thumbnails, scroll position, focus and open
  menus; new alerts slide in marked NEW.
* **One enrichment per change, shared by everyone.** Joining reviews, tickets,
  publications, nomenclature, exams and camera health onto every alarm of the window
  is done once per (Camview refresh, data version) and served to every request and
  every user until something changes (`datasource.enriched`). Schedules, escalations
  and rules are evaluated at most every 10 s, whoever polls, not once per browser tab.
  API answers above 1.4 KB are gzip-compressed.
* **Camview is read over pooled keep-alive connections** (pages of a project in
  parallel on the same TLS session); a timeout, connection reset or 5xx is retried
  twice with a short back-off before a refresh counts as failed.
* The server keeps a per-project **working window** — the newest
  `CAMVIEW_WINDOW_PAGES × 100` alarms — refreshed every `CAMVIEW_CACHE_SECONDS` in the
  background with one in-flight request per project.
* **LIVE / DELAYED / DISCONNECTED** is based on the last *successful* Camview refresh.
  If a refresh fails the previous data stays on screen with "Refresh failed · last
  successful update HH:MM:SS"; admins get a system notification. A browser that cannot
  reach the backend backs off (up to 60 s between attempts) and retries at once when
  the network is back or the tab becomes visible.
* **New alarm detection** is real: the first load is the baseline; any alarm ID seen
  afterwards is *NEW* (highlighted, counted, critical ones notified).
* Camview has no get-by-ID endpoint, so an investigation for an alarm that has left the
  window is shown from the **snapshot** saved when it was reviewed / assigned /
  shared, clearly labelled.
* Every open browser tab holds one server thread for its push stream, so the container
  runs gunicorn with `CAMVIEW_THREADS` threads (default 48: tabs + requests).

---

## 10. Analytics & reports

**Analytics** (Today / 24 h / 7 d / 30 d / custom) reads up to
`CAMVIEW_KPI_MAX_PAGES × 100` alarms (or uses Camview's history query when
`CAMVIEW_KPI_USE_HISTORY=1`) and says when it is based on the newest N only.

**Reports**: Alarm Summary, Critical, Project, TEC, TC, Centre, Shift, Camera
Activity, Investigation, Client-shared (internal view), Management Summary — and client
reports (Shared Alert Report, Client Summary).

```
Internal dataset → permission & scope filter → (client firewall) → report
```

Every report carries **audience, scope, data range, generated by, generated at,
visibility** (INTERNAL vs CLIENT). Only client reports — built from the client dataset
— can be shared with a client. Generation, export and sharing are audited.

---

## 11. Nomenclature master data

Import in **Nomenclature › Import** (replaces the tree; audited). CSV columns:

```
project_id,project_code,project_name,tec_code,tec_name,tc_code,tc_name,centre_code,centre_name,building,floor,room,camera_id,camera_code,camera_name
7,PROJECT-07,State Recruitment Exam,TEC-04,Kanpur Region,TC-0711,Test Centre 711,CTR-0711,Govt. School,A,2,204,109,CAM-109,Room 204 Front
```

`project_id` and `camera_id` are the numeric IDs Camview uses. Any level may be left
blank. JSON (nested `projects → tecs → tcs → centres → buildings → floors → rooms →
cameras`) is also accepted; **Export** gives you the current tree as CSV.

**Data quality** reports mapped vs seen cameras, unmapped cameras, unknown projects,
missing TEC/TC/centre/room, duplicate codes, unknown alarm types and alarms missing
timestamps/camera/evidence — computed only from master data and alarms actually seen.

---

## 12. Audit trail

`audit_events` is **append-only** (SQLite triggers reject UPDATE and DELETE). Recorded:
login / logout / failed login, alarm viewed (≤ 1 per user per hour), review actions,
notes, assignment, evidence viewed / downloaded, export, share request / approve /
publish (with evidence counts and context levels) / withdraw (with reason), client
viewed / acknowledged, report generate / export / share, user / role / client /
assignment / settings / connection / mode changes, nomenclature imports, rules,
schedules, handovers. Each row: who, what, when, resource, old value, new value,
project, client, note. Demo seed rows are tagged `DEMO SEED`.

---

## 13. Configuration reference

`backend/.env` (see `backend/.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `CAMVIEW_MODE` (alias `DATA_MODE=mock\|live`) | live if key set, else demo | `demo` or `live` |
| `CAMVIEW_DEMO_USERS` | 0 | 1 = create the demo login accounts (demo mode only) |
| `CAMVIEW_API_URL` | prod URL | must be https on `*.camviewai.com`, ending `/alarms/listAlarms` |
| `CAMVIEW_API_KEY` | — | raw scoped key, server-side only |
| `CAMVIEW_PROJECT_ID` | — | default project in live mode |
| `CAMVIEW_ADMIN_EMAIL` / `CAMVIEW_ADMIN_PASSWORD` | admin@camview.local / random | first live Super Admin |
| `CAMVIEW_SECRET_KEY` | generated | session signing key |
| `CAMVIEW_WINDOW_PAGES` | 3 | live working window (× 100 alarms) |
| `CAMVIEW_CACHE_SECONDS` | 30 | live data (alerts + fresh photo/video links) is fetched from Camview at most every 30 s per project; the UI refreshes from this cache |
| `CAMVIEW_KPI_MAX_PAGES` / `CAMVIEW_KPI_USE_HISTORY` | 10 / 0 | analytics data volume / history query |
| `CAMVIEW_TIMEOUT` | 15 | Camview timeout (s) |
| `CAMVIEW_ALARM_TYPE_NAMES` / `CAMVIEW_PRIORITY_LABELS` | — / 1:critical… | seed values for the dictionary (edit in the UI afterwards) |
| `CAMVIEW_DB_PATH` / `CAMVIEW_DEMO_DB_PATH` | backend/camview.db / camview-demo.db | databases |
| `CAMVIEW_ALLOW_REMOTE_SETUP` | 0 | allow the connection form from other machines |
| `CAMVIEW_SECURE_COOKIES` | 0 | set 1 behind HTTPS |
| `ENABLE_*` | see file | feature flags (V2 off) |
| `PORT` / `HOST` | 5000 / 127.0.0.1 | dev server |

Workflow policy, intelligence thresholds, escalation, monitored projects, alarm types
and priorities are edited in the UI (Settings / Nomenclature) and stored in the DB.

---

## 14. Architecture

```
Camview listAlarms ──(server-side key)──► camview_client.py   documented fields only, error mapping
                                              │
                                              ▼
datasource.py  working window, cache, dedupe, new-alarm detection, freshness
   └─ normalize (alarms.py) → context (nomenclature.py) → review/workflow/visibility (workflow.py) → flags
                                              │
rbac.py  authentication, permissions, audience wall, scope        ◄── every route
                                              │
intelligence.py  explainable alerts · analytics.py · kpis.py · reports.py · notify.py
                                              │
workflow.py  CLIENT DATA FIREWALL: build_client_visible_alarm() → client dataset
                                              │
routes_ops / routes_sharing / routes_admin / routes_client / routes_common  (JSON API)
                                              │
frontend/  no-build ES-module SPA: core (api, state, router, layout, live, palette, ui, charts),
           components (alarms, evidence, sharing), pages (internal + client/)
```

```
backend/
  app.py            Flask app, security headers, error handling
  bootstrap.py      DB init per mode, demo seed, first live admin
  config.py         .env / environment, feature flags, mode
  camview_client.py the only Camview caller
  datasource.py     working set + enrichment pipeline
  alarms.py         raw → flat normalization
  nomenclature.py   context engine, import/export, quality
  workflow.py       validation/workflow/visibility, eligibility, firewall, notes, assignment
  intelligence.py   explainable alerts + rule engine
  notify.py         notifications, schedules, escalation
  analytics.py kpis.py reports.py
  rbac.py           permissions, roles, scope, decorators
  db.py             schema, audit, persistence
  demo_seed.py mock_data.py   demo mode only
  routes_*.py       HTTP API
  tests/            pytest suite
frontend/
  index.html css/app.css js/main.js js/core/* js/components/* js/pages/* js/pages/client/*
```

---

## 15. Security model

* The Camview key lives only in `backend/.env` / the server environment. It is never
  sent to the browser, logged, or returned by any endpoint; the connection form is
  localhost-only and only accepts `https://*.camviewai.com/.../alarms/listAlarms`.
* Authentication: server sessions (HttpOnly, SameSite=Lax cookies), hashed passwords,
  sign-in rate limiting, audited logins.
* Authorization is enforced **on the server** for every route: permission + audience +
  scope. Hidden buttons are only a convenience.
* CSRF: all writes must be JSON (cross-site forms can't send JSON; no CORS is
  enabled).
* URL manipulation: out-of-scope, other-client, unpublished and non-existent records
  all return the same *not available* answer.
* Content-Security-Policy (no inline scripts, no eval), X-Frame-Options DENY, nosniff,
  `no-store` on API responses. All data rendered with HTML escaping.
* Evidence for clients goes through an authorization-checked proxy; internal evidence
  views and downloads are audited; downloads need `evidence.download`.
* Append-only audit trail.

Before exposing beyond a trusted network: run behind HTTPS
(`CAMVIEW_SECURE_COOKIES=1`), set a strong `CAMVIEW_SECRET_KEY`, change all passwords.

---

## 16. API reference

All JSON. Errors: `{"error": code, "message": text}` — never stack traces.

**Auth / common**: `GET /api/auth/session`, `POST /api/auth/login`, `POST /api/auth/logout`,
`POST /api/auth/password`, `GET /api/notifications`, `POST /api/notifications/mark`,
`GET|POST /api/views`, `DELETE /api/views/:id`.

**Operations (internal)**: `GET /api/status`, `/api/overview`, `/api/alarms` (filters:
`quick, search, tec, tc, centre, camera, alarmType, priority, lastActionType,
workflowState, visibility, shiftLabel, from, to, sort, dir, page, size`),
`/api/alarms/:id`, `POST /api/alarms/:id/review|notes|assign|bookmark`,
`GET /api/bookmarks`, `/api/users/assignable`, `/api/history`, `/api/alerts`,
`/api/work`, `/api/cameras`, `/api/cameras/:id`, `/api/context/tree`,
`/api/context/node/:id`, `/api/context/quality`, `/api/search`, `/api/analytics`,
`/api/evidence`, `POST /api/evidence/log`, `/api/shift`, `POST /api/handovers`,
`/api/presentation`, `/api/export/alarms.csv`, `/api/groups` (activity groups: `by=camera|room|centre|tc|tec`,
`gap` minutes), `/api/since-last-visit`, `/api/brief?date=`, `/api/compare?level=project|tec|tc|centre`,
`GET|POST /api/watchlist`, `POST /api/watchlist/remove`, `GET|PUT /api/me/preferences`.

**Review flow**: `GET /api/queue` (filters `status=pending|valid|invalid|exception|all,
client, exam, priority, centre, camera, search, from, to, page, size`; returns counts,
facets, freshness), `GET /api/queue/summary?tzOffset=`, `GET /api/queue/:id`,
`POST /api/queue/:id/decide` `{result, note?, clientId?}`, `GET /api/tickets`
(`delivery, status, client, exam, search`), `POST /api/tickets/:id/send|withdraw`,
`GET|POST /api/exams`, `PUT /api/exams/:id`. Client: `GET /api/client/alerts?exam=`.

**Sharing**: `GET /api/sharing?tab=`, `POST /api/sharing/eligibility`,
`GET /api/sharing/preview`, `POST /api/sharing/request|approve|publish|withdraw|bulk-request|respond`,
`GET /api/sharing/history`.

**Admin**: `/api/users`, `/api/roles`, `/api/clients`, `/api/settings`,
`PUT /api/settings/policy`, `PUT /api/settings/projects`, `POST /api/config/setup|test|raw|mode`,
`POST /api/nomenclature/import`, `GET /api/nomenclature/export.csv`, `/api/dictionary`,
`PUT /api/dictionary/alarm-types|priorities`, `/api/alert-rules`, `POST /api/alert-rules/test`,
`GET /api/alert-rules/:id/versions`, `/api/recipient-groups`, `/api/schedules`, `/api/audit`, `/api/reports`, `POST /api/reports/preview`,
`GET /api/reports/:id[/csv]`, `POST /api/reports/:id/share`.

**Client portal**: `GET /api/client/overview`, `/api/client/alerts`,
`/api/client/alerts/:id`, `POST /api/client/alerts/:id/acknowledge`, `POST /api/client/alerts/:id/messages`,
`GET /api/client/evidence/:alarmId/:kind/:index`, `/api/client/reports[/:id[/csv]]`,
`/api/client/analytics`, `/api/client/presentation`, `/api/client/profile`.

**Live updates**: `GET /api/events` (Server-Sent Events: `version` events only), `dataVersion` in
`GET /api/status` and `GET /api/notifications`.

**Project codes**: `PUT /api/nomenclature/projects/:projectId` `{code, name?}`,
`PUT /api/nomenclature/project-codes` `{text: "2773, MPESB/…
…"}` (settings.manage or nomenclature.manage).

**Incidents (V2)**: `GET /api/incidents` (`status, owner, search, alarm, page, size`),
`GET /api/incidents/suggestions` (`gapMinutes, minAlerts, limit, projectId, id`), `POST /api/incidents`
`{suggestionId | alarms | alarmIds, title?, severity?, ownerId?}`, `GET|PUT /api/incidents/:id`,
`POST /api/incidents/:id/comments` `{body}`, `POST /api/incidents/:id/alarms` `{add, remove}`.

**Operations map (V2)**: `GET /api/map?range=today|24h|window`, `GET|PUT /api/map/places`
`{key, lat, lng, label?}` or `{key, clear: true}` (settings.manage).

---

## 17. Testing

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

The suite (133 tests) covers activity grouping (nothing hidden), context completeness,
since-last-visit, the daily brief, scoped comparison, watchlist, snooze and preferences,
the client conversation, rule versioning / recipient groups / tester, room scope, the
spec's error texts, the `DATA_MODE` alias, datasets of 0 / 1 / 20 / 1 250 alarms across
pages, and: RBAC and permissions per action, project/TEC scope,
client scope, the audience wall, publication rules, *valid ≠ client-visible*, the
client firewall (no internal fields, no unshared evidence, no internal notes), evidence
visibility, revocation when a project is unassigned, inactive clients, four-eyes,
two-step approval, alarm-type share policy, bulk eligibility, notifications, schedules,
the append-only audit, report datasets (client reports from the client dataset only),
normalization, nomenclature import/resolution/quality, explainable intelligence and
skipped checks, analytics, pagination, filtering, live mode against a fake Camview
(documented request, raw key, 0-based paging, failure keeps last data) and the security
scenarios of spec §127. Tests use throwaway databases and never call Camview.

---

## 18. Deployment

```bash
export CAMVIEW_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
export CAMVIEW_MODE=live CAMVIEW_API_KEY=... CAMVIEW_PROJECT_ID=... CAMVIEW_ADMIN_PASSWORD=...
docker compose up --build
```

Runs gunicorn (1 worker × 8 threads — the working-set cache and new-alarm detection
are per process) with both databases in a Docker volume. Put it behind an HTTPS
reverse proxy and set `CAMVIEW_SECURE_COOKIES=1`.

---

### CI / CD (GitHub Actions)

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | What it does |
|---|---|
| Backend tests | Python 3.12 (as in the image): byte-compile, then the full pytest suite in demo mode with temporary databases and a mocked Camview — no secrets needed. The JUnit report is attached to the run. |
| Frontend syntax check | `node --check` on every ES module under `frontend/js`. |
| Docker image | Builds the image and smoke-tests it: starts in demo mode, signs in, reads `/api/queue/summary` and `/api/status`. |
| Publish (main / `v*` tags only) | Pushes the image to `ghcr.io/<owner>/<repo>` as `latest`, `sha-…` and the version of a `v1.2.3` tag, using the built-in `GITHUB_TOKEN`. |

The image never contains `.env` or a database (`.dockerignore`); production settings are given when the container starts. Database files and backups are git-ignored everywhere.

---

## 19. What is real, what is V2 (no fake features)

**Real in V1** (stored in Command Center's own database, audited): users, roles,
scopes, clients, nomenclature, alarm types, priorities, operator validation, notes,
assignment, bookmarks, the full sharing lifecycle including client view and
acknowledgement, in-app notifications, rules, schedules, escalation, handovers, saved
views, reports, audit. *These do not write back to Camview* — Camview's documented API
is read-only, so Camview's own alarm state is never changed.

**V2 — built and switched on** (each has a feature flag in `backend/.env`; *Administration › V2 features* shows the state):

| Feature | Where | What is real about it |
|---|---|---|
| **Incidents** (`ENABLE_INCIDENTS`, `ENABLE_ADVANCED_CORRELATION`) | Sidebar › Incidents | Related alerts (same centre within a time window, or the same camera + type repeating) are *suggested* with the reason in words; an operator creates the incident. Owner, status (open → investigating → resolved → closed), comments, timeline, audit. One alarm is in at most one open incident. |
| **Operations Map** (`ENABLE_MAP`) | Sidebar › Map, Monitoring › Map | Centres coloured by alert status and sized by activity. Camview sends no coordinates, so each **city** is looked up once on OpenStreetMap (only city + state are sent; `CAMVIEW_GEOCODE=0` turns this off) and centres are placed around it, drawn dashed as *approximate*. An administrator can place a centre exactly; that wins. Centres without a city are listed under *Not on the map*. |
| **AI Assistant** (`ENABLE_AI`) | Sidebar › AI Assistant | Claude answers questions through tools that call the same permission-checked functions as the screens, so it only ever sees what the asking user may see. Answers separate facts (with alert ids) from summary. Needs `CAMVIEW_AI_API_KEY` (and optional `CAMVIEW_AI_MODEL`) in the `.env`; without it the page says so and nothing is sent anywhere. |
| **Smart Search** (`ENABLE_SMART_SEARCH`) | Sidebar › Smart Search, Ctrl K | Plain words ("critical pending mobile phone at 9111 today") become the Alerts filters, shown as chips before searching. Works without any AI key. |
| **Real-time updates** (`ENABLE_REALTIME`) | Everywhere | Push from Command Center to the browser (section 9). Camview itself has no push, so the server still reads it every `CAMVIEW_CACHE_SECONDS`. |

**Still not built**: email/SMS/push channels, evidence annotation/versioning, dashboard customization, anomaly detection beyond the explained baseline spike.

**Assumptions to confirm with Camview**: the meaning of `priority` 1–4 (defaults to
critical/high/medium/low, flagged "not confirmed"), alarm type names, and the camera
object's fields (hence the nomenclature import).

---

## 20. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Login shows DEMO DATA | `CAMVIEW_MODE=demo`, or no key. Switch in Settings › Connection. |
| "Camview rejected the API key (HTTP 403)" | Key not active / wrong environment. Settings › Connection › Test. |
| Everything is `UNMAPPED` | Import nomenclature master data. |
| Types show "Alert type 5" | Name them in Nomenclature › Dictionary (Camview sends only the number). |
| Project shows as a number (2773) instead of its code | Camview's `listAlarms` carries only the numeric `projectId`, and `POST /projects/getProject` answers *"API key is not permitted to access this endpoint"* (HTTP 403) for the current key — so the code cannot be read from Camview. Set it once: `CAMVIEW_PROJECT_CODES=2773:MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL` in the `.env` next to docker-compose.yml (several: comma-separated), or type it in the yellow **project code** bar that administrators see at the top of every screen while a monitored project has no code. The code is then shown everywhere. If Camview later allows the key to read project records, codes are picked up automatically (checked hourly). |
| Alerts open with VIDEO UNAVAILABLE / IMAGE UNAVAILABLE | Camera status events (camera online / offline) never carry media — they are listed under Alerts › Camera status events, and the review screen shows the latest frame Camview has from that camera. A detection alert with a broken image means its signed link expired (links are refreshed with every live refresh). |
| Cameras offline KPI shows — | No camera status received yet: it appears after the first live refresh (Camview `frameSyncStatus`) or when a monitoring source pushes status. |
| Priority labels "not confirmed" | Confirm the mapping in Nomenclature › Dictionary. |
| Connection form disabled | Open the UI on the server itself (localhost) or set `CAMVIEW_ALLOW_REMOTE_SETUP=1`. |
| First live login | Password is `CAMVIEW_ADMIN_PASSWORD` or printed once in the console. |
| DELAYED / DISCONNECTED | Last successful refresh is old / failing; data shown is the last good data. |
| LIVE, but every alert is months old | Camview is not producing alerts for the monitored project id (the exam has ended). Settings › Monitored projects › *Find the running project* shows which project ids your key can read and when each last raised an alert. If none is recent, the running exam is on a project this key cannot read — ask Camview for the key/project of the running exam. |
| Access denied / not available | Your role or scope doesn't include it — ask an administrator. |
| Charts missing | The chart library CDN is blocked; all numbers remain in tables. |
#   C a m v i e w - a i - d a s h b o a r d 
 
 #   C a m v i e w - a i - d a s h b o a r d 
 
 
## Deploy to a server

Every push to `main` that passes the tests is published to GHCR and deployed by the `deploy` job over SSH.

**GitHub secrets** (Settings › Secrets and variables › Actions):

| Secret | Value |
|---|---|
| `SERVER_IP` | the server's public IP |
| `SERVER_SSH_KEY` | the full private key (`-----BEGIN … KEY-----` to `-----END … KEY-----`) |
| `SERVER_USER` | optional, default `ubuntu` (Amazon Linux: `ec2-user`) |
| `SERVER_PORT` | optional, default `22` |

**AWS security group:** the SSH rule must allow GitHub Actions to connect. GitHub's runners use changing IPs, so set the source of port 22 to `0.0.0.0/0` (login is by key only). A rule limited to your own IP gives `dial tcp …:22: i/o timeout`. Port 5000 (or 80/443 behind a proxy) must be open for users.

**Once on the server:** install Docker, then put the settings in `/opt/camview/.env` (same content as the local `.env`, never committed):

```sh
sudo mkdir -p /opt/camview && sudo nano /opt/camview/.env
sudo usermod -aG docker ubuntu     # lets the deploy user run docker without sudo
```

Data (users, clients, tickets, audit trail) is kept in the Docker volume `camview_data` across deployments.
