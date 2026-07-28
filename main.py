"""TradeOps ERP API with a backward-compatible ServiceOps workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Protocol

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError

from tradeops import build_tradeops_router, initialize_tradeops_database

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite-only local development remains supported.
    psycopg = None
    dict_row = None


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tradeops")

DATABASE_URL = os.getenv("CASEFLOW_DATABASE_URL", "").strip()
DATABASE_PATH = Path(os.getenv("CASEFLOW_DATABASE", "caseflow.db"))
DEMO_DATA_PATH = Path(__file__).with_name("data") / "synthetic_tickets.jsonl"
MAX_JOB_ATTEMPTS = int(os.getenv("MAX_JOB_ATTEMPTS", "3"))
CRM_WEBHOOK_URL = os.getenv("CRM_WEBHOOK_URL", "").strip()
CRM_WEBHOOK_TOKEN = os.getenv("CRM_WEBHOOK_TOKEN", "").strip()
CASEFLOW_ADMIN_TOKEN = os.getenv("CASEFLOW_ADMIN_TOKEN", "").strip()
PUBLIC_DEMO_MODE = os.getenv("PUBLIC_DEMO_MODE", "0") == "1"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"


class TicketCreate(BaseModel):
    customer_id: str = Field(min_length=1, max_length=80)
    subject: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)
    channel: str = Field(default="web", max_length=40)


class ReviewDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=800)


class TicketResponse(BaseModel):
    id: str
    status: TicketStatus
    priority: Priority
    category: str
    confidence: float
    requires_human_review: bool
    sop_citations: list[str]
    draft_reply: str
    delivery_job_id: str | None


class DashboardTicket(BaseModel):
    """Safe dashboard projection for public synthetic-demo records only."""

    id: str
    subject: str
    content_preview: str
    channel: str
    status: TicketStatus
    priority: Priority
    category: str
    confidence: float
    requires_human_review: bool
    review_reason: str
    sop_citation: str
    draft_reply: str
    created_at: str


class AnalysisPayload(BaseModel):
    category: str = Field(pattern="^(refund|delivery|quality|service|other)$")
    priority: Priority
    sentiment: str = Field(pattern="^(neutral|negative)$")
    confidence: float = Field(ge=0, le=1)
    entities: dict[str, str] = Field(default_factory=dict)
    requires_human_review: bool
    review_reason: str = ""


@dataclass(frozen=True)
class Analysis:
    category: str
    priority: Priority
    sentiment: str
    confidence: float
    entities: dict[str, str]
    requires_human_review: bool
    review_reason: str


class RuleBasedAnalyzer:
    """Deterministic local stand-in for a structured LLM extraction adapter.

    It makes local demos and tests reproducible. In a live deployment this
    adapter can be replaced by an LLM that returns the same Analysis contract.
    """

    CATEGORY_KEYWORDS = {
        "refund": ("退款", "退货", "赔偿", "扣款"),
        "delivery": ("物流", "快递", "延迟", "未收到"),
        "quality": ("质量", "破损", "损坏", "故障"),
        "service": ("客服", "态度", "投诉", "人工"),
    }
    CRITICAL_KEYWORDS = ("报警", "起诉", "曝光", "人身", "监管")
    HIGH_KEYWORDS = ("退款", "赔偿", "严重", "投诉", "欺诈", "维权")

    def analyze(self, subject: str, content: str) -> Analysis:
        text = f"{subject} {content}".lower()
        category = next(
            (name for name, keywords in self.CATEGORY_KEYWORDS.items() if any(word in text for word in keywords)),
            "other",
        )
        critical = any(word in text for word in self.CRITICAL_KEYWORDS)
        high = any(word in text for word in self.HIGH_KEYWORDS)
        priority = Priority.CRITICAL if critical else Priority.HIGH if high else Priority.MEDIUM
        requires_review = priority in (Priority.HIGH, Priority.CRITICAL) or category == "refund"
        entities: dict[str, str] = {}
        if "订单" in text:
            entities["order_reference"] = "customer-mentioned-order"
        if "退款" in text or "赔偿" in text:
            entities["money_request"] = "present"
        return Analysis(
            category=category,
            priority=priority,
            sentiment="negative" if high or critical else "neutral",
            confidence=0.86 if category != "other" else 0.62,
            entities=entities,
            requires_human_review=requires_review,
            review_reason="financial_or_reputational_risk" if requires_review else "",
        )


class Analyzer(Protocol):
    def analyze(self, subject: str, content: str) -> Analysis: ...


class OpenAICompatibleAnalyzer:
    """Adapter for an OpenAI-compatible structured-output endpoint.

    The response is validated before it can influence workflow routing. This
    prevents malformed model output from silently bypassing human review.
    """

    SYSTEM_PROMPT = """You classify a customer complaint for a workflow system.
Return JSON only with: category(refund|delivery|quality|service|other),
priority(low|medium|high|critical), sentiment(neutral|negative), confidence(0-1),
entities(object of string values), requires_human_review(boolean), review_reason(string).
Refund, compensation, legal, safety, reputation, or fraud topics require human review."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.api_key = api_key
        self.model = model

    def analyze(self, subject: str, content: str) -> Analysis:
        try:
            response = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps({"subject": subject, "content": content}, ensure_ascii=False)},
                    ],
                },
                timeout=20,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            payload = AnalysisPayload.model_validate_json(raw)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise RuntimeError("structured analyzer unavailable or invalid") from exc
        return Analysis(**payload.model_dump())


def build_analyzer() -> Analyzer:
    if os.getenv("ANALYZER_MODE", "rules").lower() != "openai_compatible":
        return RuleBasedAnalyzer()
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not (base_url and api_key and model):
        raise RuntimeError("OpenAI-compatible analyzer requires LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL")
    return OpenAICompatibleAnalyzer(base_url, api_key, model)


SOP_RULES = {
    "refund": ("SOP-REFUND-01", "核验订单与支付状态；未经人工审核不得承诺金额或时限。"),
    "delivery": ("SOP-DELIVERY-02", "核验物流轨迹，向客户说明下一次可查询时间。"),
    "quality": ("SOP-QUALITY-03", "收集图片和批次信息，转质量工单。"),
    "service": ("SOP-SERVICE-04", "先致歉并记录问题，不与客户争辩。"),
    "other": ("SOP-GENERAL-01", "记录事实、确认诉求并转交相应队列。"),
}


def retrieve_sop(category: str) -> tuple[str, str]:
    return SOP_RULES.get(category, SOP_RULES["other"])


def draft_reply(analysis: Analysis, sop_guidance: str) -> str:
    if analysis.requires_human_review:
        return f"很抱歉给您带来困扰。我们已记录您的反馈并提交专人核验处理。{sop_guidance}"
    return f"已收到您的反馈，我们会根据流程核验并尽快跟进。{sop_guidance}"


class DatabaseConnection:
    """Small DB-API compatibility layer for SQLite demos and PostgreSQL deploys."""

    def __init__(self, connection: Any, postgres: bool) -> None:
        self.connection = connection
        self.postgres = postgres

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> Any:
        if self.postgres:
            if query == "BEGIN IMMEDIATE":
                query = "BEGIN"
            query = query.replace("%", "%%")
            query = query.replace("?", "%s")
        return self.connection.execute(query, parameters)

    def executescript(self, script: str) -> None:
        if not self.postgres:
            self.connection.executescript(script)
            return
        for statement in script.split(";"):
            if statement.strip():
                self.connection.execute(statement)

    def close(self) -> None:
        self.connection.close()


@contextmanager
def database() -> Iterator[DatabaseConnection]:
    if DATABASE_URL.startswith(("postgres://", "postgresql://")):
        if psycopg is None:
            raise RuntimeError("psycopg is required when CASEFLOW_DATABASE_URL targets PostgreSQL")
        connection = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
        wrapped = DatabaseConnection(connection, postgres=True)
    else:
        connection = sqlite3.connect(DATABASE_PATH, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        wrapped = DatabaseConnection(connection, postgres=False)
    try:
        yield wrapped
    finally:
        wrapped.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def initialize_database() -> None:
    with database() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                customer_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                confidence REAL NOT NULL,
                requires_human_review INTEGER NOT NULL,
                review_reason TEXT NOT NULL,
                entities_json TEXT NOT NULL,
                sop_id TEXT NOT NULL,
                draft_reply TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL REFERENCES tickets(id),
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_jobs (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL UNIQUE REFERENCES tickets(id),
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ticket_status ON tickets(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_job_state ON delivery_jobs(state, updated_at);
            """
        )


def audit(db: DatabaseConnection, ticket_id: str, event_type: str, actor: str, payload: dict[str, Any]) -> None:
    db.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), ticket_id, event_type, actor, json.dumps(payload, ensure_ascii=False), utc_now()),
    )


def ticket_response(db: DatabaseConnection, ticket_id: str) -> TicketResponse:
    row = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    job = db.execute("SELECT id FROM delivery_jobs WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return TicketResponse(
        id=row["id"],
        status=TicketStatus(row["status"]),
        priority=Priority(row["priority"]),
        category=row["category"],
        confidence=row["confidence"],
        requires_human_review=bool(row["requires_human_review"]),
        sop_citations=[row["sop_id"]],
        draft_reply=row["draft_reply"],
        delivery_job_id=job["id"] if job else None,
    )


def dashboard_ticket(row: Any) -> DashboardTicket:
    return DashboardTicket(
        id=row["id"],
        subject=row["subject"],
        content_preview=row["content"][:140],
        channel=row["channel"],
        status=TicketStatus(row["status"]),
        priority=Priority(row["priority"]),
        category=row["category"],
        confidence=row["confidence"],
        requires_human_review=bool(row["requires_human_review"]),
        review_reason=row["review_reason"],
        sop_citation=row["sop_id"],
        draft_reply=row["draft_reply"],
        created_at=row["created_at"],
    )


class CrmAdapter(Protocol):
    def upsert(self, ticket_id: str, payload: dict[str, Any]) -> str: ...


class MockCrmAdapter:
    """A replaceable CRM adapter; no external customer records are used in this demo."""

    def upsert(self, ticket_id: str, payload: dict[str, Any]) -> str:
        if os.getenv("CASEFLOW_FORCE_CRM_FAILURE") == "1":
            raise RuntimeError("simulated crm delivery failure")
        digest = hashlib.sha256(f"{ticket_id}:{payload['customer_id']}".encode()).hexdigest()[:12]
        return f"demo-crm-{digest}"


class HttpCrmAdapter:
    """Sends an idempotent delivery request to a CRM/RPA integration webhook."""

    def __init__(self, webhook_url: str, token: str) -> None:
        self.webhook_url = webhook_url
        self.token = token

    def upsert(self, ticket_id: str, payload: dict[str, Any]) -> str:
        headers = {"Idempotency-Key": ticket_id}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = httpx.post(
            self.webhook_url,
            headers=headers,
            json={"ticket_id": ticket_id, **payload},
            timeout=10,
        )
        response.raise_for_status()
        reference = response.json().get("reference")
        if not isinstance(reference, str) or not reference:
            raise RuntimeError("CRM webhook response missing reference")
        return reference


def build_crm_adapter() -> CrmAdapter:
    return HttpCrmAdapter(CRM_WEBHOOK_URL, CRM_WEBHOOK_TOKEN) if CRM_WEBHOOK_URL else MockCrmAdapter()


analyzer = build_analyzer()
crm = build_crm_adapter()
_init_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    with _init_lock:
        initialize_database()
        initialize_tradeops_database(database)
    yield


app = FastAPI(title="TradeOps ERP", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    forwarded_prefix = request.headers.get("X-Forwarded-Prefix", "").rstrip("/")
    if forwarded_prefix.startswith("/"):
        request.scope["root_path"] = forwarded_prefix
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info("request_id=%s method=%s path=%s status=%s", request_id, request.method, request.url.path, response.status_code)
    return response


@app.get("/health")
def health() -> dict[str, str]:
    with database() as db:
        db.execute("SELECT 1").fetchone()
    backend = "postgresql" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "sqlite"
    return {"status": "ok", "database": "ok", "database_backend": backend, "mode": "synthetic-demo"}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).with_name("index.html"))


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/dashboard/summary")
def dashboard_summary() -> dict[str, Any]:
    """Return only synthetic demo records, even when production data shares the database."""
    with database() as db:
        rows = db.execute(
            "SELECT * FROM tickets WHERE customer_id LIKE ? ORDER BY created_at DESC LIMIT 30",
            ("synthetic-%",),
        ).fetchall()
    tickets = [dashboard_ticket(row).model_dump(mode="json") for row in rows]
    counts = {status.value: 0 for status in TicketStatus}
    priorities = {priority.value: 0 for priority in Priority}
    for ticket in tickets:
        counts[ticket["status"]] += 1
        priorities[ticket["priority"]] += 1
    return {
        "scope": "synthetic-demo-only",
        "tickets": tickets,
        "counts": counts,
        "priorities": priorities,
        "requires_review": sum(ticket["requires_human_review"] for ticket in tickets),
    }


@app.post("/demo/seed")
def seed_synthetic_demo_data() -> dict[str, Any]:
    """Idempotently load the checked-in synthetic cases for a reproducible public demo."""
    if not PUBLIC_DEMO_MODE:
        raise HTTPException(status_code=404, detail="public demo seed is disabled")
    if not DEMO_DATA_PATH.exists():
        raise HTTPException(status_code=500, detail="synthetic demo data is unavailable")

    loaded = 0
    for index, line in enumerate(DEMO_DATA_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = TicketCreate.model_validate_json(line)
        create_ticket(payload, idempotency_key=f"serviceops-demo-v1-{index:02d}")
        loaded += 1
    summary = dashboard_summary()
    return {"loaded": loaded, "scope": summary["scope"], "summary": summary}


@app.post("/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(payload: TicketCreate, idempotency_key: str = Header(min_length=8, alias="Idempotency-Key")):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM tickets WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing:
            db.execute("COMMIT")
            return ticket_response(db, existing["id"])

        try:
            analysis = analyzer.analyze(payload.subject, payload.content)
        except RuntimeError as exc:
            db.execute("ROLLBACK")
            raise HTTPException(status_code=503, detail="classification service unavailable") from exc
        sop_id, guidance = retrieve_sop(analysis.category)
        ticket_id = str(uuid.uuid4())
        now = utc_now()
        current_status = TicketStatus.PENDING_REVIEW if analysis.requires_human_review else TicketStatus.APPROVED
        db.execute(
            """INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticket_id, idempotency_key, payload.customer_id, payload.subject, payload.content, payload.channel,
                current_status, analysis.category, analysis.priority, analysis.confidence, int(analysis.requires_human_review),
                analysis.review_reason, json.dumps(analysis.entities, ensure_ascii=False), sop_id,
                draft_reply(analysis, guidance), now, now,
            ),
        )
        audit(db, ticket_id, "ticket_created", "system", {"analysis": asdict(analysis), "sop_id": sop_id})
        if not analysis.requires_human_review:
            job_id = str(uuid.uuid4())
            db.execute("INSERT INTO delivery_jobs VALUES (?, ?, ?, 0, '', ?, ?)", (job_id, ticket_id, "pending", now, now))
            audit(db, ticket_id, "delivery_queued", "system", {"job_id": job_id, "reason": "low_risk_auto_approval"})
        db.execute("COMMIT")
        return ticket_response(db, ticket_id)


@app.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: str):
    with database() as db:
        return ticket_response(db, ticket_id)


@app.get("/tickets/{ticket_id}/audit")
def get_audit(ticket_id: str) -> list[dict[str, Any]]:
    with database() as db:
        if not db.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone():
            raise HTTPException(status_code=404, detail="ticket not found")
        rows = db.execute("SELECT event_type, actor, payload_json, created_at FROM audit_events WHERE ticket_id = ? ORDER BY created_at", (ticket_id,)).fetchall()
    return [{"event_type": row["event_type"], "actor": row["actor"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]} for row in rows]


def require_operator(token: str | None) -> None:
    if CASEFLOW_ADMIN_TOKEN and (not token or not hmac.compare_digest(token, CASEFLOW_ADMIN_TOKEN)):
        raise HTTPException(status_code=401, detail="valid X-Admin-Token required")


@app.post("/tickets/{ticket_id}/review", response_model=TicketResponse)
def review_ticket(ticket_id: str, decision: ReviewDecision, admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    require_operator(admin_token)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="ticket not found")
        if row["status"] != TicketStatus.PENDING_REVIEW:
            db.execute("ROLLBACK")
            raise HTTPException(status_code=409, detail="ticket is not awaiting review")
        now = utc_now()
        if decision.decision == "reject":
            db.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?", (TicketStatus.REJECTED, now, ticket_id))
            audit(db, ticket_id, "review_rejected", decision.reviewer, {"note": decision.note})
        else:
            db.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?", (TicketStatus.APPROVED, now, ticket_id))
            job_id = str(uuid.uuid4())
            db.execute("INSERT INTO delivery_jobs VALUES (?, ?, 'pending', 0, '', ?, ?)", (job_id, ticket_id, now, now))
            audit(db, ticket_id, "review_approved", decision.reviewer, {"note": decision.note, "job_id": job_id})
        db.execute("COMMIT")
        return ticket_response(db, ticket_id)


@app.post("/demo/tickets/{ticket_id}/approve", response_model=TicketResponse)
def approve_synthetic_demo_ticket(ticket_id: str):
    if not PUBLIC_DEMO_MODE:
        raise HTTPException(status_code=404, detail="public demo approval is disabled")
    with database() as db:
        ticket = db.execute("SELECT customer_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        if not ticket["customer_id"].startswith("synthetic-"):
            raise HTTPException(status_code=403, detail="only synthetic demo tickets can use this endpoint")
    return review_ticket(
        ticket_id,
        ReviewDecision(decision="approve", reviewer="public-demo-reviewer", note="synthetic demonstration approval"),
        admin_token=CASEFLOW_ADMIN_TOKEN,
    )


def process_one_delivery_job() -> dict[str, str]:
    """Claim one job atomically. A production worker invokes the same operation on a schedule."""
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM delivery_jobs WHERE state = 'pending' AND attempts < ? ORDER BY created_at LIMIT 1", (MAX_JOB_ATTEMPTS,)).fetchone()
        if job is None:
            db.execute("COMMIT")
            return {"result": "no_pending_job"}
        db.execute("UPDATE delivery_jobs SET state = 'processing', attempts = attempts + 1, updated_at = ? WHERE id = ? AND state = 'pending'", (utc_now(), job["id"]))
        db.execute("COMMIT")

    with database() as db:
        ticket = db.execute("SELECT * FROM tickets WHERE id = ?", (job["ticket_id"],)).fetchone()
    try:
        crm_ref = crm.upsert(job["ticket_id"], {"customer_id": ticket["customer_id"], "category": ticket["category"]})
    except Exception as exc:
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT attempts FROM delivery_jobs WHERE id = ?", (job["id"],)).fetchone()
            exhausted = current is not None and current["attempts"] >= MAX_JOB_ATTEMPTS
            db.execute(
                "UPDATE delivery_jobs SET state = ?, last_error = ?, updated_at = ? WHERE id = ?",
                ("failed" if exhausted else "pending", type(exc).__name__, utc_now(), job["id"]),
            )
            if exhausted:
                db.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?", (TicketStatus.DELIVERY_FAILED, utc_now(), job["ticket_id"]))
                audit(db, job["ticket_id"], "delivery_failed", "worker", {"job_id": job["id"], "error_type": type(exc).__name__})
            else:
                audit(db, job["ticket_id"], "delivery_retry_scheduled", "worker", {"job_id": job["id"], "error_type": type(exc).__name__})
            db.execute("COMMIT")
        raise HTTPException(status_code=502, detail="crm delivery failed; retry state recorded") from exc

    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE delivery_jobs SET state = 'completed', updated_at = ? WHERE id = ?", (utc_now(), job["id"]))
        db.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?", (TicketStatus.DELIVERED, utc_now(), job["ticket_id"]))
        audit(db, job["ticket_id"], "crm_delivered", "worker", {"job_id": job["id"], "crm_reference": crm_ref})
        db.execute("COMMIT")
    return {"result": "delivered", "job_id": job["id"], "crm_reference": crm_ref}


@app.post("/jobs/run-once")
def run_one_delivery_job(admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict[str, str]:
    require_operator(admin_token)
    return process_one_delivery_job()


app.include_router(build_tradeops_router(database, utc_now, require_operator, PUBLIC_DEMO_MODE))
