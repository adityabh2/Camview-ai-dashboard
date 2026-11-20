# CAMVIEW Command Center — API documentation

This document covers:

1. The two **Camview APIs** the dashboard reads (List Alarms, Project Lookup)
2. How the dashboard turns their data into alerts (alert codes, live vs earlier, delivery)
3. The dashboard's own **HTTP API** (for integrations and troubleshooting)

---

## 1. Camview APIs

Both live on the same host:

| Environment | Base URL |
|---|---|
| Production | `https://default.prod.api.camviewai.com` |
| Development | `https://default.dev.api.camviewai.com` |

Both take `POST` with a JSON body and `Content-Type: application/json`.

### 1.1 Authentication

The credential goes in the `Authorization` header:

| Credential | Header | Notes |
|---|---|---|
| Scoped API key (infra) | `Authorization: <key>` | No prefix. Long-lived. The alarm key is limited to `/alarms/listAlarms`. |
| User login token | `Authorization: Bearer <token>` | From `POST /api/auth/login`. Expires after about an hour. |

The dashboard decides the prefix itself:

* a value that starts with `Bearer ` is sent as it is;
* a bare JWT (three dot-separated parts starting `eyJ`) gets `Bearer ` added;
* anything else is a raw key and is sent without a prefix.

Keys are kept only on the server (`backend/.env` or Settings › Connection) and are never sent to a browser.

### 1.2 List Alarms — `POST /alarms/listAlarms`

Returns the alarms of one Camview project. **No `/api` prefix** (with it the server answers 500 "no handler").

**Request body** (only `projectId` is required):

| Field | Type | Description |
|---|---|---|
| `projectId` | number | **Required.** Camview project id. |
| `page` | number | Page, 0-based (default 0). |
| `size` | number | Page size (default 20, max 100). The dashboard reads pages of 100. |
| `limit` | number | Max results (default 50). |
| `lastKey` | string | Cursor from the previous response (history queries). |
| `alarmType` | number[] | Filter by alert codes (see §2.1). |
| `alarmState` | number | Filter by alarm state. |
| `lastActionType` | number | 0 pending · 1 valid · 2 invalid · 3 exception. |
| `useHistory` | boolean | Read the history table instead of live alarms. |
| `startTime` / `endTime` | number (epoch ms) | Time range; reads history. |
| `shiftLabel` | string | History rows of one shift. |

**Example**

```bash
curl -X POST "https://default.prod.api.camviewai.com/alarms/listAlarms" \
  -H "Authorization: <API_KEY>" -H "Content-Type: application/json" \
  -d '{ "projectId": 3598, "page": 0, "size": 100 }'
```

**Response** (shortened):

```json
{
  "cameraAlarmsDetails": [
    {
      "camera": { "cameraId": 6052, "centerCode": "C018", "subLocation": "ENTRY EXIT", "frameSyncStatus": "SYNCED" },
      "alarm": {
        "alarmId": "C-392903-18-1795162763342-5693",
        "cameraId": 6052, "projectId": 3598, "alarmType": 18,
        "lastActionType": 0, "priority": 1,
        "firstInstance": "2026-11-20T08:18:19Z", "lastInstance": "2026-11-20T08:19:23Z",
        "totalTimesReported": 3, "ticketId": 456,
        "imageUrl": "https://…", "videoUrl": "https://…", "shiftLabel": "Morning"
      },
      "imageUrls": ["https://…"], "serialNumber": 1
    }
  ],
  "lastKey": "…", "page": 0, "size": 100, "totalElements": 149, "totalPages": 2, "hasNext": true
}
```

**Status codes**

| Code | Meaning | What the dashboard does |
|---|---|---|
| 200 | Success | Data is used. |
| 401 / 403 | Key or token rejected (wrong key, expired Bearer token, key used on another path) | Shows "access denied", keeps the last data. |
| 429 | Rate limited | Waits (honours `Retry-After`), keeps the last data. |
| 5xx | Camview error | Retries twice; after 5 failures in a row pauses calls (15 s, doubling to 5 min). |

**How the dashboard reads it**

* Every monitored project is read in the background every `CAMVIEW_CACHE_SECONDS` (default 30 s), in pages
  of 100, up to `CAMVIEW_WINDOW_PAGES` pages (default 100 = 10,000 alarms). Camview's list is not sorted
  newest-first, so all pages are read.
* At most `CAMVIEW_MAX_CONCURRENCY` (default 6) calls run at the same time.
* A record that cannot be used is skipped and written to **Data quality** (it never disappears silently).

### 1.3 Project Lookup — `POST /api/project/listProjectsByClientProjectId`

Turns an exam's **project code** (Camview's `clientProjectId`) into every Camview project that carries it.

**Request**

```json
{ "clientProjectId": "RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL" }
```

The code goes in the JSON body, so `/`, `&` and spaces need no escaping. Must not be empty.

```bash
curl -X POST "https://default.prod.api.camviewai.com/api/project/listProjectsByClientProjectId" \
  -H "Authorization: <API_KEY>" -H "Content-Type: application/json" \
  -d '{ "clientProjectId": "RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL" }'
```

**Response**

```json
{
  "clientProjectId": "RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL",
  "count": 1,
  "projects": [
    { "projectId": 3598, "clientProjectId": "RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL",
      "name": "JKSSB-AAE-FOC/151126", "status": "ACTIVE",
      "description": "Jammu and Kashmir Services Selection Board (JKSSB) Accounts Assistant Exam – 2026",
      "startDate": "2026-11-15", "endDate": "2026-11-15" }
  ]
}
```

No match gives `200` with `"count": 0, "projects": []`.

| Code | `description` | Cause |
|---|---|---|
| 400 | `clientProjectId is required` | Empty or blank code. |
| 401 | `Unable to determine client ID from token` | A user token that cannot be resolved (sign in again). |
| 403 | `API key is not permitted to access this endpoint` | The key is not authorised for project lookup. Set a separate *Project lookup key* in Settings › Connection. |
| 500 | `Internal Server Error` | Camview error. |

**How the dashboard uses it**

* **Add project code** (Dashboard or Settings) calls it once, monitors every returned project and labels it
  with the code.
* Every registered code is looked up again every 15 minutes, so a project Camview adds later under the same
  code is picked up by itself.
* `startDate` / `endDate` are kept; the **final report** goes to clients the day after the end date.
* Key: `CAMVIEW_PROJECT_API_KEY` if set, otherwise the alarm key (`CAMVIEW_API_KEY`).

---

## 2. From Camview data to alerts

### 2.1 Alert codes (`alarmType`)

Camview sends only a number. The dashboard names every alert from the official CamviewAI feature list
(`features.xlsx`). The short code is the alert's **classification** everywhere in the dashboard, reports and
client portal. There is no critical / high / medium ranking.

| Code | Short | Alert type | Constant |
|---|---|---|---|
| 1 | CD | Crowd Detection | `FEATURE_ID_CROWD_DETECTION` |
| 2 | ZI | Zone Intrusion | `FEATURE_ID_ZONE_INTRUSION` |
| 3 | TP | Trunk Placed | `FEATURE_ID_TRUNK_PLACED` |
| 4 | TO | Trunk Opened | `FEATURE_ID_TRUNK_OPENED` |
| 5 | VD | Vehicle Detection | `FEATURE_ID_VEHICLE_DETECTION` |
| 6 | NP | No Person Detection | `FEATURE_ID_NO_PERSON` |
| 8 | FD | Fire Detection | `FEATURE_ID_FIRE_DETECTION` |
| 10 | HMS | Health Monitoring | `FEATURE_ID_HMS` |
| 11 | CT | Camera Tampering | `FEATURE_ID_CAMERA_TAMPERING` |
| 12 | INM | Invigilator Not Moving | `FEATURE_ID_INVIGILATOR_STILLNESS` |
| 14 | MD | Mobile Detection | `FEATURE_ID_MOBILE_DETECTION` |
| 15 | MPS | Multiple People Standing | `FEATURE_ID_PEOPLE_STANDING` |
| 16 | FM | Furniture Movement | `FEATURE_ID_FURNITURE_MOVEMENT` |
| 17 | CHT | Cheating Detection | `FEATURE_ID_CHEATING` |
| 18 | TT | Trunk Tampering | `FEATURE_ID_TRUNK_TAMPERING` |
| 20 | SSH | Sack Stack Height | `FEATURE_ID_SACK_STACK_HEIGHT` |
| 21 | SOR | Sack Outside ROI | `FEATURE_ID_SACK_OUTSIDE_ROI` |
| 22 | CE | Crowd Estimate | `FEATURE_ID_CROWD_ESTIMATE` |
| 23 | SA | Suspicious Activity | `FEATURE_ID_SUSPICIOUS_ACTIVITY` |

* A code **not** in this list is shown as **"Unknown alert code N"** and recorded in Data quality
  (`unknown_alert_code`). It is never shown under a guessed name.
* An administrator can rename a type in Nomenclature › Dictionary; that name then wins.
* Code 10 also carries camera online / offline events. Those are camera status events, never sent to clients.

### 2.2 Live or earlier

An alert is **Live** while its own latest occurrence (`lastInstance`) is at most `CAMVIEW_LIVE_MINUTES`
(default 30) minutes old. It is judged by the alert's time, **never by when it reached the dashboard**: an old
alert read now (a newly added project code, a late page) is an **earlier** alert.

* Lists show a green **Live** badge only on live alerts; others show their date and time.
* Lists are sorted: live first, then most recent, then most repeated.
* Only live alerts are marked **NEW** and announced to clients as new. Earlier alerts are still delivered,
  quietly.

### 2.3 Delivery to clients

`CAMVIEW_CLIENT_DELIVERY=all` (default):

* Every detection alert goes to the client(s) of its project the moment it is read. Nobody has to mark it.
* An administrator marks **Invalid** (or **Exception**) to take it back. It disappears from the client's view
  and stays in the dashboard with its decision, ticket and audit trail. It is not sent again unless marked
  **Valid** later. **Revoke from client** takes it back without a verdict.
* When a client **opens** an alert in the portal, it is acknowledged automatically.

`CAMVIEW_CLIENT_DELIVERY=valid`: clients see only alerts an operator marked Valid.

### 2.4 Automatic reports

* **Daily** at `CAMVIEW_DAILY_REPORT_HOUR` (default 20:00, time zone `CAMVIEW_REPORT_TZ_OFFSET` minutes, default
  330 = India): each client with alerts that day receives a report of the day.
* **Final**: the day after a project's end date, each of its clients receives one report of the whole project.
* Each is sent once, shared in the portal and announced in-app.

---

## 3. Dashboard HTTP API

All paths are relative to the dashboard (local: `http://localhost:5000`, portal: `:5001`). JSON in, JSON out.
Errors: `{"error": "<code>", "message": "<text>", "requestId": "…"}`.

### 3.1 Session

| Method & path | Purpose |
|---|---|
| `GET /api/auth/session` | Who is signed in, mode, `noLogin`, `clientPortal`, projects. |
| `POST /api/auth/login` `{email, password}` | Sign in. On the client door only client accounts are accepted. |
| `POST /api/auth/logout` | Sign out. |

### 3.2 Alerts

| Method & path | Purpose |
|---|---|
| `GET /api/queue/summary?code=<code>` | Dashboard numbers, by type, by hour, projects. `code` limits to one project code. |
| `GET /api/queue?status=pending&code=<code>&page=1&size=50` | The alert list. Filters: `status`, `code`, `projectId`, `client`, `exam`, `type`, `centre`, `camera`, `from`, `to`, `search`, `view`. |
| `POST /api/queue/<alarmId>/decide` `{result: valid\|invalid\|exception, note}` | Decide an alert. Invalid / exception take it back from clients. |
| `POST /api/queue/<alarmId>/acknowledge` | Mark as picked up by the team. |
| `POST /api/tickets/<id>/withdraw` | Revoke from the client (no verdict). |
| `POST /api/tickets/<id>/send` | Send to the client again. |

Each alert carries `alarmType`, `alarmTypeName`, `alarmTypeShort`, `alarmTypeKnown`, `live`, `lastAgeMinutes`,
`firstInstance`, `lastInstance`, `decision`, `evidence`, `exam`, `client`, `ticket`.

### 3.3 Project codes

| Method & path | Purpose |
|---|---|
| `GET /api/project-codes` | Registered codes with their projects. |
| `POST /api/project-codes` `{code}` | Look a code up in Camview and register it (404 when no project has it). |
| `POST /api/project-codes/remove` `{code}` | Unregister; its projects stop being monitored. |
| `POST /api/project-codes/refresh` | Look every code up again now. |
| `POST /api/settings/projects/lookup` `{code}` | Look up without registering. |
| `POST /api/config/test-lookup` `{code}` | Test the Project Lookup key. |

### 3.4 Clients, reports, settings

| Method & path | Purpose |
|---|---|
| `GET /api/clients`, `POST /api/clients` `{name, projectCodes}`, `PUT /api/clients/<id>` | Clients are linked to project codes. |
| `POST /api/users` `{name, email, roleId, clientId, password}` | Create a client login. |
| `GET /api/reports`, `POST /api/reports/preview`, `POST /api/reports`, `POST /api/reports/<id>/share` | Reports. |
| `GET /api/settings`, `POST /api/config/setup` `{apiUrl, apiKey, projectApiKey}` | Connection settings (keys are never returned). |

### 3.5 Client portal (sign-in on port 5001)

| Method & path | Purpose |
|---|---|
| `GET /api/client/overview` | Portal home numbers, exams, latest alerts, by type. |
| `GET /api/client/alerts` | The client's alerts (filters `exam`, `ack`, `search`). |
| `GET /api/client/alerts/<alarmId>` | One alert with evidence. **Opening it acknowledges it.** |
| `GET /api/client/reports`, `GET /api/client/reports/<id>` | Reports shared with the client. |
