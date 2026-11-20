# CAMVIEW Command Center — User manual

For the operations team (administrators, operators) and for client users of the portal.

---

## 1. Starting the dashboard

1. Start **Docker Desktop**.
2. In the `camview` folder, double-click **`start-local.bat`**. The browser opens on the dashboard.
3. To stop it: **`stop-local.bat`**. Your data is kept.

| Address | For | Sign-in |
|---|---|---|
| `http://localhost:5000` | Operations team, on this computer | None |
| `http://<this computer's IP>:5001` | Clients, from the network | Required (client accounts only) |

The Camview keys and settings are in the `.env` file next to `start-local.bat`, or in
**Settings › Connection**.

---

## 2. Project codes: what you see is organised by exam

Each exam has a **project code**, for example `RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL`. You never
type Camview's project numbers.

**Add a project code:** Dashboard › **+ Add project code** → type the code exactly as set in Camview →
**Add**. Camview finds every project with that code; they are monitored from now on. New projects Camview adds
later under the same code join by themselves.

**Switch between exams:** the tabs under the dashboard heading (one per code). **All projects** shows
everything. The Alerts page follows the same choice.

**Remove a code:** Dashboard › **Manage codes** › **Remove**.

---

## 3. The dashboard

* **Heading:** the selected project code, the exam name and Camview's connection status.
* **Coloured bar:** the share of Pending, Valid, Invalid and Exception alerts. Click a name to open those alerts.
* **Cards:** alerts today, pending, not acknowledged, valid, invalid, exceptions, delivered to clients,
  camera status.
* **Charts:** alerts per hour (by verdict) and the busiest alert types.
* A yellow note appears when Camview has sent no new alert for a day or more.

---

## 4. Alerts

### 4.1 Reading an alert

| You see | Meaning |
|---|---|
| **CHT**, **MD**, **TT** … | The alert's type code from the CamviewAI feature list (hover for the full name). See §9. |
| **Live** (green) | It happened in the last 30 minutes, by the alert's own time. |
| Date and time | An earlier alert. An old alert that arrives now is shown as earlier, never as live or NEW. |
| **NEW** | A live alert that arrived since you opened the page. |
| **Unknown alert code N** (amber) | Camview sent a code not in the feature list. It is also listed in Data quality. |

Lists show live alerts first, then the most recent, then the most repeated.

### 4.2 Every alert goes to the client automatically

You do **not** have to mark alerts valid. As soon as an alert arrives it is visible to the client of its
exam.

**To take an alert back from the client:** open it and press **Invalid** (or **Exception**). It disappears
from the client's portal and stays in the dashboard with your decision, its ticket and the audit trail. It is
not sent again unless someone marks it **Valid** later.

**Revoke from client** (on the alert, or **Revoke** on the Tickets page) takes it back without a verdict.
**Send to client again** restores it.

### 4.3 Filters

Status tabs (Pending, Valid, Invalid, Exception), quick views (Needs review, Not acknowledged, Camera issues …),
search, project, client, exam, alert type, centre, camera, and dates (**Today** / **All dates** / from–to).

---

## 5. Clients

**Clients › New client:** a name, a contact, and the project codes the client may see (tick a registered
code or type a new one). The client then receives every alert of those codes.

**Logins for a client:** on the client card press **Add login**, choose a sign-in name, and type or
generate a password. Give the client the sign-in name, the password and the portal address
`http://<this computer's IP>:5001`.

**What the client sees:** the button on the client card shows exactly the client's view.

---

## 6. The client portal (for clients)

1. Open the address you were given and sign in.
2. **Overview:** how many alerts you received, how many you have not opened yet, and the latest alerts.
3. **Alerts:** every alert for your exams. **Opening an alert marks it as read**, and the operations team
   is told. There is no separate button.
4. Each alert shows its type, place, time, and the photo or video evidence.
5. **Reports:** your daily reports and the final report of each exam.

You see only your own organisation's alerts. Internal notes and operator details are never shown.

---

## 7. Reports

**Automatic:**

* **Daily report:** every evening at 20:00, each client with alerts that day receives a report of the day.
* **Final report:** the day after an exam ends, each of its clients receives one report of the whole exam.

Both appear in the client's Reports and in the dashboard's Reports list (marked **Auto**).

**By hand:** Reports › choose the exam, the period and who it is for (the client, or our team) ›
**Preview** › **Generate**. A client report can be shared or unshared at any time. Every report can be printed
or saved as PDF, or downloaded as CSV.

---

## 8. Settings

* **Connection:** the Camview address, the alarm key (or a `Bearer` token) and an optional Project lookup
  key. **Test connection** and **Test project lookup** make one real call each.
* **Monitored projects:** the projects behind your codes; add a code here too.
* **Data quality:** records Camview sent that could not be used (for example an unknown alert code).

---

## 9. Alert types

| Code | Type | Code | Type |
|---|---|---|---|
| CD | Crowd Detection | INM | Invigilator Not Moving |
| ZI | Zone Intrusion | MD | Mobile Detection |
| TP | Trunk Placed | MPS | Multiple People Standing |
| TO | Trunk Opened | FM | Furniture Movement |
| VD | Vehicle Detection | CHT | Cheating Detection |
| NP | No Person Detection | TT | Trunk Tampering |
| FD | Fire Detection | SSH | Sack Stack Height |
| HMS | Health Monitoring | SOR | Sack Outside ROI |
| CT | Camera Tampering | CE | Crowd Estimate |
|  |  | SA | Suspicious Activity |

---

## 10. Common questions

**The dashboard says "Live from Camview" but no alert is live.** "Live from Camview" means the connection
works. An alert is live only when it happened in the last 30 minutes.

**An alert shows "Unknown alert code".** Camview sent a type number that is not in the feature list. Ask
CamviewAI which feature it is; an administrator can name it in Nomenclature › Dictionary.

**"No Camview project has the code …".** The code must match the client project id in Camview exactly
(capitals, slashes, dashes).

**"Access was denied (HTTP 401/403)".** The key is wrong, or a Bearer token has expired. Paste a new one in
Settings › Connection.

**A client cannot sign in.** Check the address uses port **5001**, the login is active (Clients › client card),
and Windows Firewall allows Docker on private networks.
