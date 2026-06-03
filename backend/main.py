from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.ussd import build_ussd_response


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "safetyping.db"
STATIC_DIR = BASE_DIR / "frontend"
EAT = timezone(timedelta(hours=3))
AT_API_BASE_URL = "https://api.africastalking.com/version1"
AT_CONTENT_BASE_URL = "https://content.africastalking.com/version1"
AT_SANDBOX_MESSAGING_URL = "https://api.sandbox.africastalking.com/version1/messaging"

LANGUAGES = {
    "en": "English",
    "sw": "Kiswahili",
    "sheng": "Sheng",
}

BRIEFINGS = {
    "en": "Wear PPE, report hazards early, and check out before leaving site.",
    "sw": "Vaa vifaa vya usalama, ripoti hatari mapema, na jiondoe kabla ya kuondoka site.",
    "sheng": "Vaa gear ya safety, sema hazard mapema, na check out kabla uondoke site.",
}


def load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


def getenv_any(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


class Settings(BaseModel):
    africastalking_username: str = getenv_any("AFRICASTALKING_USERNAME", "SANDBOX_USERNAME", default="sandbox")
    africastalking_api_key: str | None = getenv_any("AFRICASTALKING_API_KEY", "SANDBOX_API_KEY")
    africastalking_environment: Literal["sandbox", "production"] = (
        "production" if os.getenv("AFRICASTALKING_ENVIRONMENT", "sandbox").lower() == "prod" else os.getenv(
            "AFRICASTALKING_ENVIRONMENT", "sandbox"
        ).lower()
    )
    at_messaging_url: str | None = os.getenv("AT_MESSAGING_URL")
    sms_sender_id: str | None = getenv_any("AFRICASTALKING_SENDER_ID", "SMS_SENDER_ID")
    sms_shortcode: str | None = getenv_any("AFRICASTALKING_SHORTCODE", "SMS_SHORTCODE", default="70896")
    sms_keyword: str | None = os.getenv("AFRICASTALKING_KEYWORD")
    sms_auto_send: bool = os.getenv("SAFETYPING_SMS_AUTO_SEND", "true").lower() == "true"
    sms_dry_run: bool = os.getenv("SAFETYPING_SMS_DRY_RUN", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def messaging_url(self) -> str:
        if self.at_messaging_url:
            return self.at_messaging_url
        if self.africastalking_environment == "sandbox":
            return AT_SANDBOX_MESSAGING_URL
        return f"{AT_API_BASE_URL}/messaging"

    @property
    def sender_id(self) -> str | None:
        if self.africastalking_environment == "sandbox":
            return self.sms_shortcode
        return self.sms_sender_id


settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("safetyping.sms")


def now_iso() -> str:
    return datetime.now(EAT).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workers (
                phone TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                site TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                supervisor_phone TEXT NOT NULL,
                shift_start TEXT NOT NULL,
                shift_end TEXT NOT NULL,
                last_check_in TEXT,
                last_check_out TEXT,
                status TEXT NOT NULL DEFAULT 'expected'
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_phone TEXT NOT NULL,
                category TEXT NOT NULL,
                site TEXT NOT NULL,
                description TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_phone TEXT NOT NULL,
                supervisor_phone TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Queued',
                provider_response TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inbound_sms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                text TEXT NOT NULL,
                link_id TEXT,
                created_at TEXT NOT NULL,
                raw_payload TEXT
            );
            """
        )
        existing_columns = {
            column["name"]
            for column in conn.execute("PRAGMA table_info(alerts)").fetchall()
        }
        if "provider_response" not in existing_columns:
            conn.execute("ALTER TABLE alerts ADD COLUMN provider_response TEXT")
        existing = conn.execute("SELECT COUNT(*) AS count FROM workers").fetchone()["count"]
        if existing == 0:
            conn.executemany(
                """
                INSERT INTO workers
                (phone, name, site, language, supervisor_phone, shift_start, shift_end, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("+254700111001", "Amina Otieno", "Westlands Tower", "sw", "+254722900001", "08:00", "17:00", "expected"),
                    ("+254700111002", "Brian Mwangi", "Mlolongo Bypass", "en", "+254722900001", "07:30", "16:30", "expected"),
                    ("+254700111003", "Kevin Barasa", "Kilimani Estate", "sheng", "+254733800002", "08:00", "17:00", "expected"),
                ],
            )


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        found = conn.execute(query, params).fetchone()
        return dict(found) if found else None


class WorkerCreate(BaseModel):
    phone: str = Field(min_length=7)
    name: str = Field(min_length=2)
    site: str = Field(min_length=2)
    language: Literal["en", "sw", "sheng"] = "en"
    supervisor_phone: str = Field(min_length=7)
    shift_start: str = "08:00"
    shift_end: str = "17:00"


class IncidentCreate(BaseModel):
    worker_phone: str
    category: str
    description: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"


class CheckInCreate(BaseModel):
    worker_phone: str
    action: Literal["check_in", "check_out"] = "check_in"


class IncidentStatus(BaseModel):
    status: Literal["Open", "Reviewing", "Resolved"]


class SmsSendRequest(BaseModel):
    to: list[str] = Field(min_length=1)
    message: str = Field(min_length=1, max_length=640)
    mode: Literal["bulk", "premium"] = "bulk"


def at_headers() -> dict[str, str]:
    if not settings.africastalking_api_key:
        raise RuntimeError("AFRICASTALKING_API_KEY is not configured")
    return {
        "apiKey": settings.africastalking_api_key,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def post_form(url: str, data: dict[str, Any]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, headers=at_headers(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return {"status_code": response.status, "body": json.loads(body) if body else {}}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return {"status_code": exc.code, "body": body}


def get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers=at_headers(), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return {"status_code": response.status, "body": json.loads(body) if body else {}}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return {"status_code": exc.code, "body": body}


def send_sms(to: list[str], message: str, mode: Literal["bulk", "premium"] = "bulk") -> dict[str, Any]:
    if settings.sms_dry_run:
        logger.info("DRY RUN - would send SMS to %s: %s", ",".join(to), message)
        return {"status_code": 200, "body": {"dryRun": True, "to": to, "message": message}}
    if not settings.sms_auto_send:
        logger.info("AUTO_SEND disabled - skipping SMS to %s", ",".join(to))
        return {"status_code": 202, "body": {"skipped": True, "reason": "auto_send_disabled", "to": to}}
    if mode == "premium":
        if not settings.sms_shortcode or not settings.sms_keyword:
            raise RuntimeError("Premium SMS requires AFRICASTALKING_SHORTCODE and AFRICASTALKING_KEYWORD")
        return post_form(
            f"{AT_CONTENT_BASE_URL}/messaging",
            {
                "username": settings.africastalking_username,
                "to": ",".join(to),
                "message": message,
                "from": settings.sms_shortcode,
                "bulkSMSMode": 0,
                "keyword": settings.sms_keyword,
            },
        )
    if settings.africastalking_environment == "sandbox":
        data: dict[str, Any] = {
            "username": settings.africastalking_username,
            "to": ",".join(to),
            "message": message,
            "bulkSMSMode": 1,
        }
        if settings.sender_id:
            data["from"] = settings.sender_id
        response = post_form(settings.messaging_url, data)
        logger.info("Sandbox SMS send to %s returned %s", ",".join(to), response["status_code"])
        return response
    data = {
        "username": settings.africastalking_username,
        "phoneNumber": to,
        "message": message,
        "bulkSMSMode": 1,
        "enqueue": 1,
    }
    if settings.sms_sender_id:
        data["senderId"] = settings.sms_sender_id
    response = post_form(f"{AT_API_BASE_URL}/messaging/bulk", data)
    logger.info("Production SMS send to %s returned %s", ",".join(to), response["status_code"])
    return response


def queue_alert(worker_phone: str, supervisor_phone: str, kind: str, message: str) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO alerts (worker_phone, supervisor_phone, kind, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (worker_phone, supervisor_phone, kind, message, now_iso()),
        )
        alert_id = cursor.lastrowid
    if settings.africastalking_api_key or settings.sms_dry_run:
        deliver_alert(alert_id)
    return alert_id


def deliver_alert(alert_id: int) -> dict[str, Any]:
    alert = row("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    try:
        response = send_sms([alert["supervisor_phone"]], alert["message"])
        ok = 200 <= response["status_code"] < 300
        status = "Sent" if ok else "Failed"
    except Exception as exc:
        response = {"error": str(exc)}
        status = "Failed"
    with connect() as conn:
        conn.execute(
            "UPDATE alerts SET status = ?, provider_response = ? WHERE id = ?",
            (status, json.dumps(response), alert_id),
        )
    return {"alert": row("SELECT * FROM alerts WHERE id = ?", (alert_id,)), "provider": response}


def normalize_phone_number(phone_number: str) -> str:
    phone_number = phone_number.strip()
    return phone_number if phone_number.startswith("+") else f"+{phone_number}"


def save_inbound_sms(
    provider_id: str,
    sender: str,
    recipient: str,
    text: str,
    link_id: str,
    created_at: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO inbound_sms (provider_id, sender, recipient, text, link_id, created_at, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider_id,
                sender,
                recipient,
                text,
                link_id,
                created_at,
                json.dumps(
                    {
                        "id": provider_id,
                        "from": sender,
                        "to": recipient,
                        "text": text,
                        "date": created_at,
                        "linkId": link_id,
                    }
                ),
            ),
        )


async def missed_check_loop() -> None:
    while True:
        try:
            scan_missed_checkins()
        except Exception as exc:
            print(f"missed check-in scan failed: {exc}")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(missed_check_loop())
    yield
    task.cancel()


init_db()

app = FastAPI(title="SafetyPing", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now_iso()}


@app.get("/health")
def simple_health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/api/settings")
def public_settings() -> dict[str, Any]:
    return {
        "africastalking_environment": settings.africastalking_environment,
        "africastalking_username": settings.africastalking_username,
        "has_api_key": bool(settings.africastalking_api_key),
        "at_messaging_url": settings.messaging_url,
        "active_sender": settings.sender_id,
        "sms_sender_id": settings.sms_sender_id,
        "sms_shortcode": settings.sms_shortcode,
        "sms_keyword": settings.sms_keyword,
        "sms_auto_send": settings.sms_auto_send,
        "sms_dry_run": settings.sms_dry_run,
        "sms_ready": bool(settings.africastalking_api_key),
    }


@app.get("/api/workers")
def list_workers() -> list[dict[str, Any]]:
    return rows("SELECT * FROM workers ORDER BY site, name")


@app.post("/api/workers")
def create_worker(payload: WorkerCreate) -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO workers
            (phone, name, site, language, supervisor_phone, shift_start, shift_end, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT status FROM workers WHERE phone = ?), 'expected'))
            """,
            (
                payload.phone,
                payload.name,
                payload.site,
                payload.language,
                payload.supervisor_phone,
                payload.shift_start,
                payload.shift_end,
                payload.phone,
            ),
        )
    return get_worker(payload.phone)


def get_worker(phone: str) -> dict[str, Any]:
    worker = row("SELECT * FROM workers WHERE phone = ?", (phone,))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return worker


@app.post("/api/checkins")
def record_checkin(payload: CheckInCreate) -> dict[str, Any]:
    worker = get_worker(payload.worker_phone)
    column = "last_check_in" if payload.action == "check_in" else "last_check_out"
    status = "checked_in" if payload.action == "check_in" else "checked_out"
    with connect() as conn:
        conn.execute(
            f"UPDATE workers SET {column} = ?, status = ? WHERE phone = ?",
            (now_iso(), status, payload.worker_phone),
        )
    return {"message": f"{worker['name']} {status.replace('_', ' ')}", "worker": get_worker(payload.worker_phone)}


@app.get("/api/incidents")
def list_incidents() -> list[dict[str, Any]]:
    return rows(
        """
        SELECT incidents.*, workers.name AS worker_name
        FROM incidents
        LEFT JOIN workers ON workers.phone = incidents.worker_phone
        ORDER BY incidents.created_at DESC
        """
    )


@app.post("/api/incidents")
def create_incident(payload: IncidentCreate) -> dict[str, Any]:
    worker = get_worker(payload.worker_phone)
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO incidents (worker_phone, category, site, description, severity, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (payload.worker_phone, payload.category, worker["site"], payload.description, payload.severity, now_iso()),
        )
        incident_id = cursor.lastrowid
    queue_alert(
        payload.worker_phone,
        worker["supervisor_phone"],
        "incident",
        f"{payload.severity.upper()} incident from {worker['name']} at {worker['site']}: {payload.category}",
    )
    return row("SELECT * FROM incidents WHERE id = ?", (incident_id,))


@app.patch("/api/incidents/{incident_id}")
def update_incident(incident_id: int, payload: IncidentStatus) -> dict[str, Any]:
    with connect() as conn:
        conn.execute("UPDATE incidents SET status = ? WHERE id = ?", (payload.status, incident_id))
    updated = row("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    return updated


@app.get("/api/alerts")
def list_alerts() -> list[dict[str, Any]]:
    return rows("SELECT * FROM alerts ORDER BY created_at DESC")


@app.post("/api/alerts/{alert_id}/send")
def send_alert(alert_id: int) -> dict[str, Any]:
    return deliver_alert(alert_id)


@app.post("/api/sms/send")
def api_send_sms(payload: SmsSendRequest) -> dict[str, Any]:
    return send_sms(payload.to, payload.message, payload.mode)


@app.get("/api/sms/received")
def fetch_received_sms(last_received_id: int = 0, keyword: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "username": settings.africastalking_username,
        "lastReceivedId": last_received_id,
    }
    if keyword:
        params["keyword"] = keyword
    if settings.sms_shortcode:
        params["shortCode"] = settings.sms_shortcode
    return get_json(f"{AT_API_BASE_URL}/messaging", params)


@app.get("/api/sms/inbound")
def list_inbound_sms() -> list[dict[str, Any]]:
    return rows("SELECT * FROM inbound_sms ORDER BY created_at DESC")


@app.post("/sms", response_class=PlainTextResponse)
def sms_callback(
    id: str = Form(""),
    from_: str = Form("", alias="from"),
    to: str = Form(""),
    text: str = Form(""),
    date: str = Form(""),
    linkId: str = Form(""),
) -> str:
    created_at = date or now_iso()
    save_inbound_sms(id, from_, to, text, linkId, created_at)
    return "OK"


@app.post("/sms_callback")
def sms_echo_callback(
    background_tasks: BackgroundTasks,
    sender: str = Form("", alias="from"),
    text: str = Form(""),
    to: str = Form(""),
    id: str = Form(""),
    date: str = Form(""),
    linkId: str = Form(""),
) -> dict[str, str]:
    sender = normalize_phone_number(sender)
    created_at = date or now_iso()
    logger.debug("Received SMS from %s: %s", sender, text)
    save_inbound_sms(id, sender, to, text, linkId, created_at)
    if text and (settings.africastalking_api_key or settings.sms_dry_run):
        background_tasks.add_task(send_sms, [sender], text)
    return {"status": "accepted"}


@app.post("/api/missed-checkins/scan")
def scan_missed_checkins() -> dict[str, Any]:
    created = 0
    today = datetime.now(EAT).date()
    pending_alerts: list[tuple[str, str, str]] = []
    with connect() as conn:
        workers = conn.execute("SELECT * FROM workers").fetchall()
        for worker in workers:
            shift_start = datetime.combine(today, datetime.strptime(worker["shift_start"], "%H:%M").time(), tzinfo=EAT)
            grace_deadline = shift_start + timedelta(minutes=15)
            has_checked_in_today = bool(
                worker["last_check_in"] and parse_iso(worker["last_check_in"]).date() == today
            )
            already_alerted = conn.execute(
                """
                SELECT 1 FROM alerts
                WHERE worker_phone = ? AND kind = 'missed_checkin' AND date(created_at) = date(?)
                """,
                (worker["phone"], now_iso()),
            ).fetchone()
            if datetime.now(EAT) >= grace_deadline and not has_checked_in_today and not already_alerted:
                pending_alerts.append(
                    (
                        worker["phone"],
                        worker["supervisor_phone"],
                        f"{worker['name']} missed the {worker['shift_start']} check-in at {worker['site']}.",
                    )
                )
                conn.execute("UPDATE workers SET status = 'missed' WHERE phone = ?", (worker["phone"],))
                created += 1
    for worker_phone, supervisor_phone, message in pending_alerts:
        queue_alert(worker_phone, supervisor_phone, "missed_checkin", message)
    return {"created_alerts": created}


@app.get("/api/briefings")
def daily_briefings() -> dict[str, Any]:
    return {
        "date": datetime.now(EAT).date().isoformat(),
        "languages": LANGUAGES,
        "briefings": BRIEFINGS,
    }


@app.post("/ussd", response_class=PlainTextResponse)
def ussd(
    sessionId: str = Form("demo"),
    serviceCode: str = Form("*384*77#"),
    phoneNumber: str = Form("+254700111001"),
    text: str = Form(""),
) -> str:
    del sessionId, serviceCode
    worker = row("SELECT * FROM workers WHERE phone = ?", (phoneNumber,))
    if not worker:
        return "END You are not registered for SafetyPing. Please contact your site supervisor."

    return build_ussd_response(
        text=text,
        worker=worker,
        briefings=BRIEFINGS,
        record_checkin=lambda action: record_checkin(
            CheckInCreate(worker_phone=phoneNumber, action=action)
        ),
        create_incident=lambda category, severity, description: create_incident(
            IncidentCreate(
                worker_phone=phoneNumber,
                category=category,
                severity=severity,
                description=description,
            )
        ),
    )
