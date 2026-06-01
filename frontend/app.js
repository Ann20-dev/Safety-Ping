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
      (worker) => `
        <tr>
          <td><strong>${worker.name}</strong><br>${worker.phone}</td>
          <td>${worker.site}</td>
          <td>${worker.shift_start}-${worker.shift_end}</td>
          <td>${badge(worker.status)}</td>
          <td>${fmt(worker.last_check_in)}</td>
        </tr>
      `,
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

document.querySelector("#scanBtn").addEventListener("click", async () => {
  await api("/api/missed-checkins/scan", { method: "POST" });
  await loadAll();
});

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

loadAll().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="item">Failed to load SafetyPing: ${error.message}</p>`);
});
