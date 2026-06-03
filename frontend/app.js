const state = {
  workers: [],
  incidents: [],
  alerts: [],
  inboundSms: [],
  settings: {},
};

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};

const fmt = (value) => (value ? new Date(value).toLocaleString() : "-");

const badge = (value) => `<span class="badge ${String(value).toLowerCase()}">${String(value).replace("_", " ")}</span>`;

async function loadAll() {
  const [workers, incidents, alerts, briefings, inboundSms, settings] = await Promise.all([
    api("/api/workers"),
    api("/api/incidents"),
    api("/api/alerts"),
    api("/api/briefings"),
    api("/api/sms/inbound"),
    api("/api/settings"),
  ]);
  state.workers = workers;
  state.incidents = incidents;
  state.alerts = alerts;
  state.inboundSms = inboundSms;
  state.settings = settings;
  renderWorkers();
  renderIncidents();
  renderAlerts();
  renderBriefings(briefings);
  renderInboundSms();
  renderIntegrationStatus();
  renderMetrics();
  fillWorkerSelects();
}

function renderMetrics() {
  document.querySelector("#checkedInCount").textContent = state.workers.filter((w) => w.status === "checked_in").length;
  document.querySelector("#missedCount").textContent = state.workers.filter((w) => w.status === "missed").length;
  document.querySelector("#openIncidentCount").textContent = state.incidents.filter((i) => i.status !== "Resolved").length;
  document.querySelector("#alertCount").textContent = state.alerts.length;
}

function renderWorkers() {
  document.querySelector("#workerRows").innerHTML = state.workers
    .map(
      (worker) => {
        const action = worker.status === "checked_in" ? "check_out" : "check_in";
        const label = worker.status === "checked_in" ? "Check out" : "Check in";
        return `
        <tr>
          <td><strong>${worker.name}</strong><br>${worker.phone}</td>
          <td>${worker.site}</td>
          <td>${worker.shift_start}-${worker.shift_end}</td>
          <td>${badge(worker.status)}</td>
          <td>${fmt(worker.last_check_in)}</td>
          <td><button class="action-btn worker-action-btn" type="button" data-phone="${worker.phone}" data-action="${action}">${label}</button></td>
        </tr>
      `;
      },
    )
    .join("");
}

function renderIncidents() {
  document.querySelector("#incidentList").innerHTML =
    state.incidents
      .map(
        (incident) => `
          <article class="item">
            <strong>${incident.category} ${badge(incident.severity)}</strong>
            <p>${incident.description}</p>
            <p>${incident.worker_name || incident.worker_phone} at ${incident.site} - ${fmt(incident.created_at)}</p>
            <div class="item-actions">
              ${incident.status !== "Resolved" ? `<button class="action-btn incident-resolve-btn" type="button" data-incident-id="${incident.id}">Resolve</button>` : `<span class="badge resolved">Resolved</span>`}
              <button class="action-btn incident-edit-btn" type="button" data-incident-id="${incident.id}">Edit</button>
              <button class="action-btn danger-btn incident-delete-btn" type="button" data-incident-id="${incident.id}">Delete</button>
            </div>
          </article>
        `,
      )
      .join("") || `<p>No incidents yet.</p>`;
}

function renderAlerts() {
  document.querySelector("#alertList").innerHTML =
    state.alerts
      .map(
        (alert) => `
          <article class="item">
            <strong>${alert.kind.replace("_", " ")}</strong>
            <p>${alert.message}</p>
            <p>Supervisor ${alert.supervisor_phone} - ${fmt(alert.created_at)}</p>
          </article>
        `,
      )
      .join("") || `<p>No alerts queued.</p>`;
}

function renderBriefings(payload) {
  document.querySelector("#briefingList").innerHTML = Object.entries(payload.briefings)
    .map(([code, text]) => `<article class="item"><strong>${payload.languages[code]}</strong><p>${text}</p></article>`)
    .join("");
}

function renderIntegrationStatus() {
  const settings = state.settings;
  document.querySelector("#integrationStatus").innerHTML = `
    <article class="item">
      <strong>${settings.africastalking_environment || "sandbox"} ${settings.sms_ready ? badge("ready") : badge("queued")}</strong>
      <p>Username: ${settings.africastalking_username || "-"}</p>
      <p>Sender ID: ${settings.sms_sender_id || "Not set"}</p>
      <p>Shortcode: ${settings.sms_shortcode || "Not set"}</p>
      <p>Auto-send alerts: ${settings.sms_auto_send ? "On" : "Off"}</p>
    </article>
  `;
}

function renderInboundSms() {
  document.querySelector("#inboundSmsList").innerHTML =
    state.inboundSms
      .map(
        (sms) => `
          <article class="item">
            <strong>${sms.sender} to ${sms.recipient || "-"}</strong>
            <p>${sms.text}</p>
            <p>${fmt(sms.created_at)}</p>
          </article>
        `,
      )
      .join("") || `<p>No inbound SMS yet.</p>`;
}

function fillWorkerSelects() {
  const options = state.workers.map((worker) => `<option value="${worker.phone}">${worker.name} - ${worker.site}</option>`).join("");
  document.querySelector("#incidentWorker").innerHTML = options;
  document.querySelector("#ussdPhone").innerHTML = options;
}

const scanStatus = document.querySelector("#scanStatus");
const scanBtn = document.querySelector("#scanBtn");

async function scanMissedCheckins() {
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning...";
  scanStatus.textContent = "Looking for missed worker check-ins...";
  try {
    const result = await api("/api/missed-checkins/scan", { method: "POST" });
    await loadAll();
    if (result.created_alerts > 0) {
      scanStatus.textContent = `${result.created_alerts} missed check-in alert${result.created_alerts === 1 ? "" : "s"} created.`;
    } else {
      scanStatus.textContent = "No new missed check-ins found.";
    }
  } catch (error) {
    scanStatus.textContent = `Scan failed: ${error.message}`;
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan missed check-ins";
  }
}

document.querySelector("#scanBtn").addEventListener("click", scanMissedCheckins);

document.querySelector("#checkInFirst").addEventListener("click", async () => {
  if (!state.workers[0]) return;
  await api("/api/checkins", {
    method: "POST",
    body: JSON.stringify({ worker_phone: state.workers[0].phone, action: "check_in" }),
  });
  await loadAll();
});

document.querySelector("#incidentForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/api/incidents", {
    method: "POST",
    body: JSON.stringify({
      worker_phone: document.querySelector("#incidentWorker").value,
      category: document.querySelector("#incidentCategory").value,
      severity: document.querySelector("#incidentSeverity").value,
      description: document.querySelector("#incidentDescription").value,
    }),
  });
  await loadAll();
});

document.querySelector("#ussd").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new URLSearchParams();
  form.set("phoneNumber", document.querySelector("#ussdPhone").value);
  form.set("text", document.querySelector("#ussdText").value);
  const response = await fetch("/ussd", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  document.querySelector("#ussdOutput").textContent = await response.text();
  await loadAll();
});

document.querySelector("#workerRows").addEventListener("click", async (event) => {
  const button = event.target.closest("button.worker-action-btn");
  if (!button) return;
  button.disabled = true;
  await api("/api/checkins", {
    method: "POST",
    body: JSON.stringify({ worker_phone: button.dataset.phone, action: button.dataset.action }),
  });
  await loadAll();
});

document.querySelector("#incidentList").addEventListener("click", async (event) => {
  const resolveButton = event.target.closest("button.incident-resolve-btn");
  const editButton = event.target.closest("button.incident-edit-btn");
  const deleteButton = event.target.closest("button.incident-delete-btn");

  if (resolveButton) {
    resolveButton.disabled = true;
    await api(`/api/incidents/${resolveButton.dataset.incidentId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "Resolved" }),
    });
    await loadAll();
    return;
  }

  if (editButton) {
    const incidentId = editButton.dataset.incidentId;
    const incident = state.incidents.find((item) => String(item.id) === incidentId);
    if (!incident) return;

    const description = prompt("Edit incident description", incident.description);
    if (description === null) return;
    const trimmedDescription = description.trim();
    if (!trimmedDescription) {
      alert("Description cannot be empty.");
      return;
    }

    const updates = { description: trimmedDescription };
    const category = prompt("Edit category", incident.category);
    if (category !== null && category.trim()) {
      updates.category = category.trim();
    }
    const severity = prompt("Edit severity (low, medium, high, critical)", incident.severity);
    if (severity !== null && severity.trim()) {
      updates.severity = severity.trim();
    }

    editButton.disabled = true;
    await api(`/api/incidents/${incidentId}`, {
      method: "PATCH",
      body: JSON.stringify(updates),
    });
    await loadAll();
    return;
  }

  if (deleteButton) {
    const incidentId = deleteButton.dataset.incidentId;
    if (!confirm("Delete this incident report? This cannot be undone.")) return;
    deleteButton.disabled = true;
    await api(`/api/incidents/${incidentId}`, { method: "DELETE" });
    await loadAll();
    return;
  }
});

loadAll().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="item">Failed to load SafetyPing: ${error.message}</p>`);
});
