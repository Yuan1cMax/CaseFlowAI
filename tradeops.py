"""TradeOps ERP domain module.

The module models a synthetic transaction-operations workflow inspired by the
author's prior domain experience. It does not contain former-employer data.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, ContextManager

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field


DbFactory = Callable[[], ContextManager[Any]]
NowFactory = Callable[[], str]
OperatorGuard = Callable[[str | None], None]


class OrderCreate(BaseModel):
    customer_id: str = Field(min_length=1, max_length=80)
    inventory_id: str = Field(min_length=1, max_length=80)
    rental_days: int = Field(ge=1, le=90)
    channel: str = Field(default="private_domain", max_length=40)


class CaseCreate(BaseModel):
    order_id: str = Field(min_length=1, max_length=80)
    subject: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=2000)
    requested_refund: float = Field(default=0, ge=0, le=100000)
    channel: str = Field(default="web", max_length=40)


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reviewer: str = Field(default="demo-operator", min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)


class LeadConvert(BaseModel):
    operator: str = Field(default="demo-sales", min_length=1, max_length=80)


SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_customers (
    id TEXT PRIMARY KEY,
    customer_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone_masked TEXT NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_leads (
    id TEXT PRIMARY KEY,
    lead_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    channel TEXT NOT NULL,
    intent TEXT NOT NULL,
    budget REAL NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    next_action TEXT NOT NULL,
    ai_summary TEXT NOT NULL,
    customer_id TEXT REFERENCES erp_customers(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_inventory (
    id TEXT PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    game TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    daily_price REAL NOT NULL,
    deposit REAL NOT NULL,
    status TEXT NOT NULL,
    risk_flag TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_orders (
    id TEXT PRIMARY KEY,
    order_no TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL REFERENCES erp_customers(id),
    inventory_id TEXT NOT NULL REFERENCES erp_inventory(id),
    channel TEXT NOT NULL,
    rental_days INTEGER NOT NULL,
    amount REAL NOT NULL,
    deposit REAL NOT NULL,
    status TEXT NOT NULL,
    risk_score INTEGER NOT NULL,
    risk_reason TEXT NOT NULL,
    ai_summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_service_cases (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    case_no TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL REFERENCES erp_orders(id),
    subject TEXT NOT NULL,
    content TEXT NOT NULL,
    channel TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_refund REAL NOT NULL,
    sop_id TEXT NOT NULL,
    ai_summary TEXT NOT NULL,
    draft_reply TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_approvals (
    id TEXT PRIMARY KEY,
    approval_no TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    approval_type TEXT NOT NULL,
    status TEXT NOT NULL,
    assignee TEXT NOT NULL,
    reason TEXT NOT NULL,
    decision_note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_sops (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    domain TEXT NOT NULL,
    trigger_rule TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_requirements (
    id TEXT PRIMARY KEY,
    requirement_no TEXT NOT NULL UNIQUE,
    department TEXT NOT NULL,
    title TEXT NOT NULL,
    pain_point TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    target_release TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    ai_summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_integrations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    system_type TEXT NOT NULL,
    integration_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    success_rate REAL NOT NULL,
    pending_jobs INTEGER NOT NULL,
    last_sync_at TEXT NOT NULL,
    owner TEXT NOT NULL,
    fallback_plan TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_enablement (
    id TEXT PRIMARY KEY,
    activity_type TEXT NOT NULL,
    title TEXT NOT NULL,
    audience TEXT NOT NULL,
    status TEXT NOT NULL,
    participant_count INTEGER NOT NULL,
    completion_rate REAL NOT NULL,
    owner TEXT NOT NULL,
    scheduled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_usage_stats (
    id TEXT PRIMARY KEY,
    stat_date TEXT NOT NULL,
    department TEXT NOT NULL,
    module TEXT NOT NULL,
    active_users INTEGER NOT NULL,
    action_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    avg_minutes REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS erp_audit_events (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_erp_order_status ON erp_orders(status, created_at);
CREATE INDEX IF NOT EXISTS idx_erp_inventory_status ON erp_inventory(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_erp_case_status ON erp_service_cases(status, created_at);
CREATE INDEX IF NOT EXISTS idx_erp_approval_status ON erp_approvals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_erp_requirement_status ON erp_requirements(status, priority);
CREATE INDEX IF NOT EXISTS idx_erp_usage_module ON erp_usage_stats(module, stat_date);
CREATE INDEX IF NOT EXISTS idx_erp_audit_entity ON erp_audit_events(entity_type, entity_id, created_at);
"""


CUSTOMERS = [
    ("SYN-CUST-001", "C2026001", "林先生", "138****1024", "A", "AI 导购", "active", "周晴", "low"),
    ("SYN-CUST-002", "C2026002", "陈女士", "156****3308", "B", "私域社群", "active", "宋扬", "low"),
    ("SYN-CUST-003", "C2026003", "赵先生", "186****5271", "A", "老客转介绍", "active", "周晴", "medium"),
    ("SYN-CUST-004", "C2026004", "吴先生", "177****2049", "C", "自然流量", "active", "宋扬", "low"),
    ("SYN-CUST-005", "C2026005", "许女士", "133****8615", "B", "AI 导购", "active", "周晴", "medium"),
    ("SYN-CUST-006", "C2026006", "顾先生", "189****7732", "A", "私域社群", "watch", "宋扬", "high"),
]

LEADS = [
    ("SYN-LEAD-001", "L2026072801", "何先生", "AI 客服", "寻找 500 元内的高配置账号", 500, "qualified", "周晴", "确认租期与押金", "预算明确，已完成商品偏好提取，建议 2 小时内跟进。"),
    ("SYN-LEAD-002", "L2026072802", "沈女士", "私域社群", "咨询周末短租", 320, "new", "宋扬", "发送可用账号清单", "有明确时段，尚未确认具体游戏区服。"),
    ("SYN-LEAD-003", "L2026072803", "杨先生", "老客转介绍", "高价值道具账号", 1200, "negotiating", "周晴", "人工确认风控材料", "高客单且关注押金规则，建议人工解释履约边界。"),
    ("SYN-LEAD-004", "L2026072804", "唐女士", "自然流量", "首次体验", 180, "new", "宋扬", "补充需求信息", "需求信息不完整，AI 暂不自动推荐。"),
]

INVENTORY = [
    ("SYN-INV-001", "ACCT-VAL-021", "战术竞技 / 稀有皮肤组合", "战术竞技", ["稀有皮肤", "排位可用"], 98, 300, "available", "normal"),
    ("SYN-INV-002", "ACCT-VAL-034", "战术竞技 / 高段位账号", "战术竞技", ["高段位", "全英雄"], 128, 500, "rented", "normal"),
    ("SYN-INV-003", "ACCT-MOBA-118", "MOBA / 全英雄典藏", "MOBA", ["全英雄", "典藏"], 76, 260, "reserved", "normal"),
    ("SYN-INV-004", "ACCT-FPS-207", "射击竞技 / 高配武器库", "射击竞技", ["高配", "热门武器"], 168, 600, "maintenance", "login_anomaly"),
    ("SYN-INV-005", "ACCT-MOBA-129", "MOBA / 轻量体验账号", "MOBA", ["入门", "低押金"], 42, 120, "available", "normal"),
    ("SYN-INV-006", "ACCT-RPG-055", "角色扮演 / 成品角色", "角色扮演", ["成品角色", "稀有外观"], 136, 480, "rented", "normal"),
    ("SYN-INV-007", "ACCT-FPS-214", "射击竞技 / 周末短租", "射击竞技", ["短租", "中配"], 68, 220, "available", "normal"),
    ("SYN-INV-008", "ACCT-RPG-061", "角色扮演 / 多职业账号", "角色扮演", ["多职业", "副本"], 112, 360, "reserved", "identity_review"),
]

ORDERS = [
    ("SYN-ORD-001", "T20260728001", "seed-order-001", "SYN-CUST-001", "SYN-INV-002", "AI 导购", 3, 384, 500, "active", 18, "", "AI 导购完成需求结构化，库存锁定与凭证交付均已记录。"),
    ("SYN-ORD-002", "T20260728002", "seed-order-002", "SYN-CUST-002", "SYN-INV-003", "私域社群", 2, 152, 260, "pending_fulfillment", 22, "", "支付已核验，等待运营人员完成账号交付。"),
    ("SYN-ORD-003", "T20260728003", "seed-order-003", "SYN-CUST-003", "SYN-INV-006", "老客转介绍", 5, 680, 480, "active", 34, "高客单", "老客复购，金额偏高但历史履约记录正常。"),
    ("SYN-ORD-004", "T20260728004", "seed-order-004", "SYN-CUST-006", "SYN-INV-008", "自然流量", 7, 784, 360, "pending_risk", 78, "高风险客户 + 长租期", "客户风险等级与租期同时触发人工复核，系统未自动履约。"),
    ("SYN-ORD-005", "T20260728005", "seed-order-005", "SYN-CUST-004", "SYN-INV-005", "AI 导购", 1, 42, 120, "completed", 12, "", "低风险体验订单，已正常完成并释放库存。"),
    ("SYN-ORD-006", "T20260728006", "seed-order-006", "SYN-CUST-005", "SYN-INV-004", "私域社群", 2, 336, 600, "exception", 71, "登录异常", "履约期间检测到账号登录异常，已暂停并转售后协同。"),
]

CASES = [
    ("SYN-CASE-001", "seed-case-001", "A20260728001", "SYN-ORD-006", "登录异常", "交付后提示异地登录，无法进入账号。", "web", "access", "high", "pending_review", 336, "SOP-ACCESS-02", "账号异常且涉及退款，需先冻结履约并人工核验登录日志。", "已记录异常并暂停计费，运营人员将在核验登录记录后给出处理结果。"),
    ("SYN-CASE-002", "seed-case-002", "A20260728002", "SYN-ORD-001", "租期咨询", "想延长一天，如何补差价？", "AI 客服", "change", "medium", "processing", 0, "SOP-CHANGE-01", "属于低风险租期变更，可核验库存后按日价补差。", "可以为您核验续租库存，确认后将按当前日价计算补差。"),
    ("SYN-CASE-003", "seed-case-003", "A20260728003", "SYN-ORD-003", "道具与描述不符", "订单页面写有稀有外观，但账号内没有看到。", "phone", "quality", "high", "pending_review", 120, "SOP-DISPUTE-03", "商品描述争议涉及部分退款，需要人工核验发布快照。", "我们已保留订单发布快照并提交复核，在核验完成前不会擅自承诺退款金额。"),
]

APPROVALS = [
    ("SYN-APR-001", "R20260728001", "order", "SYN-ORD-004", "order_risk", "pending", "风控岗", "风险客户且租期 7 天", ""),
    ("SYN-APR-002", "R20260728002", "case", "SYN-CASE-001", "refund", "pending", "售后主管", "退款 336 元且存在登录异常", ""),
    ("SYN-APR-003", "R20260728003", "case", "SYN-CASE-003", "partial_refund", "pending", "售后主管", "商品描述争议，申请部分退款 120 元", ""),
]

SOPS = [
    ("SOP-ORDER-01", "订单履约与凭证留存", "订单", "支付完成且风控通过", "锁定唯一库存，核验账号状态，记录交付时间与操作人后进入履约中。", "v1.3", "active"),
    ("SOP-RISK-01", "高风险订单人工复核", "风控", "风险分 >= 60", "禁止自动交付；复核客户风险、租期、金额与库存风险标记。", "v1.1", "active"),
    ("SOP-ACCESS-02", "账号登录异常处置", "售后", "异地登录或无法访问", "暂停履约并保留登录记录；涉及退款必须由售后主管审批。", "v2.0", "active"),
    ("SOP-CHANGE-01", "租期变更", "售后", "续租或提前归还", "先检查后续库存占用，再按日价计算差额并记录变更。", "v1.4", "active"),
    ("SOP-DISPUTE-03", "商品描述争议", "售后", "商品与发布描述不符", "调取发布快照与交付凭证；退款金额由主管复核，不得由 AI 自动承诺。", "v1.6", "active"),
]

REQUIREMENTS = [
    ("SYN-REQ-001", "REQ-2026-018", "客服部", "售后工单与订单上下文打通", "客服需跨三个页面核验订单、库存与历史沟通，平均处理时间偏长。", "P0", "released", "姜锦源", "R1.0", "工单详情可查看订单、库存、审批与完整审计轨迹。", "高频跨系统查询问题，优先通过统一业务主键与聚合视图解决。"),
    ("SYN-REQ-002", "REQ-2026-021", "运营部", "账号库存锁定防止重复分配", "人工表格更新不及时，存在同一账号被重复承诺的风险。", "P0", "released", "姜锦源", "R1.0", "创建订单时原子校验库存，仅 available 状态允许锁定。", "属于交易履约核心控制点，应使用数据库事务而非 AI 判断。"),
    ("SYN-REQ-003", "REQ-2026-025", "风控部", "高风险订单强制人工审批", "长租、高风险客户与异常账号缺少统一拦截规则。", "P0", "validating", "姜锦源", "R1.1", "风险分达到 60 时不得自动履约，并生成可追溯审批任务。", "规则引擎负责硬拦截，AI 仅补充风险摘要与核验建议。"),
    ("SYN-REQ-004", "REQ-2026-027", "销售部", "AI 导购线索回流客户池", "AI 对话产生的高意向用户没有进入后续跟进流程。", "P1", "developing", "姜锦源", "R1.1", "线索包含意图、预算、下一步动作，可人工确认后转为客户。", "复用 TunTunAgent 的意图与预算提取结果，但保留人工确认。"),
    ("SYN-REQ-005", "REQ-2026-030", "管理层", "按模块查看系统使用与流程瓶颈", "系统上线后缺少使用率、异常率与积压节点的统一复盘。", "P1", "planned", "姜锦源", "R1.2", "每周输出模拟运营报告，列出积压、错误率与可执行改进建议。", "用业务结果指标检验功能价值，避免只统计页面访问量。"),
]

INTEGRATIONS = [
    ("SYN-INT-001", "TunTunAgent AI 工作流", "Dify", "Workflow API", "healthy", 98.6, 0, "2026-07-28T14:32:00+08:00", "AI 应用", "模型不可用时回退规则分类与人工队列"),
    ("SYN-INT-002", "商品账号检索服务", "业务 API", "REST + 幂等键", "healthy", 99.8, 1, "2026-07-28T14:31:42+08:00", "运营技术", "失败任务进入重试队列，不释放已锁定库存"),
    ("SYN-INT-003", "支付结果通知", "支付平台", "Webhook", "degraded", 96.2, 3, "2026-07-28T14:29:18+08:00", "财务系统", "签名失败进入人工对账队列"),
    ("SYN-INT-004", "企业通知", "协作工具", "机器人 Webhook", "healthy", 99.4, 0, "2026-07-28T14:30:51+08:00", "系统运营", "通知失败不影响订单主事务，可单独补发"),
]

ENABLEMENT = [
    ("SYN-ENA-001", "training", "订单与库存操作培训", "销售、运营", "completed", 12, 100, "姜锦源", "2026-07-22T10:00:00+08:00"),
    ("SYN-ENA-002", "training", "售后审批与退款边界", "客服、售后主管", "completed", 9, 88.9, "姜锦源", "2026-07-24T14:00:00+08:00"),
    ("SYN-ENA-003", "release", "R1.1 风控规则灰度说明", "运营、风控", "scheduled", 0, 0, "姜锦源", "2026-07-30T15:00:00+08:00"),
]

USAGE_STATS = [
    ("SYN-USE-001", "2026-07-24", "销售部", "客户与线索", 4, 36, 1, 4.8),
    ("SYN-USE-002", "2026-07-24", "运营部", "订单履约", 5, 51, 2, 6.2),
    ("SYN-USE-003", "2026-07-24", "客服部", "售后工单", 4, 29, 3, 12.4),
    ("SYN-USE-004", "2026-07-25", "销售部", "客户与线索", 5, 48, 1, 4.2),
    ("SYN-USE-005", "2026-07-25", "运营部", "订单履约", 6, 64, 1, 5.6),
    ("SYN-USE-006", "2026-07-25", "客服部", "售后工单", 5, 37, 2, 10.8),
    ("SYN-USE-007", "2026-07-26", "销售部", "客户与线索", 5, 53, 0, 3.9),
    ("SYN-USE-008", "2026-07-26", "运营部", "订单履约", 6, 72, 1, 5.1),
    ("SYN-USE-009", "2026-07-26", "客服部", "售后工单", 5, 42, 2, 9.7),
]


def initialize_tradeops_database(db_factory: DbFactory) -> None:
    with db_factory() as db:
        db.executescript(SCHEMA)


def _audit(db: Any, entity_type: str, entity_id: str, event_type: str, actor: str, payload: dict[str, Any], now: str) -> None:
    db.execute(
        "INSERT INTO erp_audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), entity_type, entity_id, event_type, actor, json.dumps(payload, ensure_ascii=False), now),
    )


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _public_entity(entity_id: str) -> None:
    if not entity_id.startswith("SYN-"):
        raise HTTPException(status_code=403, detail="public demo actions are limited to synthetic records")


def _seed(db: Any, now: str) -> dict[str, int]:
    counts = {"customers": 0, "leads": 0, "inventory": 0, "orders": 0, "cases": 0, "approvals": 0, "sops": 0, "requirements": 0, "integrations": 0, "enablement": 0, "usage": 0}

    def insert_if_missing(table: str, key_column: str, values: tuple[Any, ...], sql: str, bucket: str) -> None:
        if db.execute(f"SELECT 1 FROM {table} WHERE {key_column} = ?", (values[0],)).fetchone():
            return
        db.execute(sql, values)
        counts[bucket] += 1

    for row in CUSTOMERS:
        insert_if_missing("erp_customers", "id", (*row, now), "INSERT INTO erp_customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "customers")
    for row in LEADS:
        insert_if_missing("erp_leads", "id", (*row, None, now, now), "INSERT INTO erp_leads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "leads")
    for row in INVENTORY:
        values = (*row[:4], json.dumps(row[4], ensure_ascii=False), *row[5:], now)
        insert_if_missing("erp_inventory", "id", values, "INSERT INTO erp_inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "inventory")
    for row in ORDERS:
        insert_if_missing("erp_orders", "id", (*row, now, now), "INSERT INTO erp_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "orders")
    for row in CASES:
        insert_if_missing("erp_service_cases", "id", (*row, now, now), "INSERT INTO erp_service_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "cases")
    for row in APPROVALS:
        insert_if_missing("erp_approvals", "id", (*row, now, now), "INSERT INTO erp_approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "approvals")
    for row in SOPS:
        insert_if_missing("erp_sops", "id", (*row, now), "INSERT INTO erp_sops VALUES (?, ?, ?, ?, ?, ?, ?, ?)", "sops")
    for row in REQUIREMENTS:
        insert_if_missing("erp_requirements", "id", (*row, now, now), "INSERT INTO erp_requirements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "requirements")
    for row in INTEGRATIONS:
        insert_if_missing("erp_integrations", "id", row, "INSERT INTO erp_integrations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", "integrations")
    for row in ENABLEMENT:
        insert_if_missing("erp_enablement", "id", row, "INSERT INTO erp_enablement VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", "enablement")
    for row in USAGE_STATS:
        insert_if_missing("erp_usage_stats", "id", row, "INSERT INTO erp_usage_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?)", "usage")

    if sum(counts.values()):
        _audit(db, "system", "SYN-DEMO", "demo_seeded", "system", counts, now)
    return counts


def build_tradeops_router(db_factory: DbFactory, now_factory: NowFactory, require_operator: OperatorGuard, public_demo_mode: bool) -> APIRouter:
    router = APIRouter(prefix="/erp", tags=["TradeOps ERP"])

    def authorize_write(entity_id: str, token: str | None) -> None:
        if public_demo_mode:
            _public_entity(entity_id)
            return
        require_operator(token)

    @router.post("/demo/seed")
    def seed_demo() -> dict[str, Any]:
        if not public_demo_mode:
            raise HTTPException(status_code=404, detail="public demo seed is disabled")
        with db_factory() as db:
            db.execute("BEGIN IMMEDIATE")
            counts = _seed(db, now_factory())
            db.execute("COMMIT")
        return {"scope": "synthetic-demo-only", "created": counts}

    @router.get("/dashboard")
    def dashboard() -> dict[str, Any]:
        with db_factory() as db:
            metrics = {
                "demo_orders": db.execute("SELECT COUNT(*) AS value FROM erp_orders WHERE id LIKE 'SYN-%'").fetchone()["value"],
                "active_orders": db.execute("SELECT COUNT(*) AS value FROM erp_orders WHERE id LIKE 'SYN-%' AND status = 'active'").fetchone()["value"],
                "pending_fulfillment": db.execute("SELECT COUNT(*) AS value FROM erp_orders WHERE id LIKE 'SYN-%' AND status = 'pending_fulfillment'").fetchone()["value"],
                "pending_approvals": db.execute("SELECT COUNT(*) AS value FROM erp_approvals WHERE id LIKE 'SYN-%' AND status = 'pending'").fetchone()["value"],
                "open_cases": db.execute("SELECT COUNT(*) AS value FROM erp_service_cases WHERE id LIKE 'SYN-%' AND status != 'resolved'").fetchone()["value"],
                "available_inventory": db.execute("SELECT COUNT(*) AS value FROM erp_inventory WHERE id LIKE 'SYN-%' AND status = 'available'").fetchone()["value"],
                "synthetic_gmv": db.execute("SELECT COALESCE(SUM(amount), 0) AS value FROM erp_orders WHERE id LIKE 'SYN-%'").fetchone()["value"],
            }
            alerts = _rows(db.execute(
                """SELECT a.id, a.approval_no, a.entity_type, a.entity_id, a.approval_type, a.reason, a.assignee, a.created_at
                   FROM erp_approvals a WHERE a.id LIKE 'SYN-%' AND a.status = 'pending' ORDER BY a.created_at DESC LIMIT 6"""
            ).fetchall())
            recent = _rows(db.execute(
                """SELECT o.id, o.order_no, o.status, o.amount, o.risk_score, o.ai_summary, o.created_at,
                          c.name AS customer_name, i.sku, i.title AS inventory_title
                   FROM erp_orders o JOIN erp_customers c ON c.id = o.customer_id
                   JOIN erp_inventory i ON i.id = o.inventory_id
                   WHERE o.id LIKE 'SYN-%' ORDER BY o.created_at DESC LIMIT 8"""
            ).fetchall())
        return {"scope": "synthetic-demo-only", "metrics": metrics, "alerts": alerts, "recent_orders": recent}

    @router.get("/orders")
    def list_orders(order_status: str | None = Query(default=None, alias="status")) -> dict[str, Any]:
        query = """SELECT o.*, c.name AS customer_name, c.level AS customer_level, i.sku, i.title AS inventory_title
                   FROM erp_orders o JOIN erp_customers c ON c.id = o.customer_id
                   JOIN erp_inventory i ON i.id = o.inventory_id WHERE o.id LIKE 'SYN-%'"""
        params: tuple[Any, ...] = ()
        if order_status:
            query += " AND o.status = ?"
            params = (order_status,)
        with db_factory() as db:
            rows = _rows(db.execute(query + " ORDER BY o.created_at DESC", params).fetchall())
        return {"items": rows, "total": len(rows)}

    @router.post("/orders", status_code=status.HTTP_201_CREATED)
    def create_order(
        payload: OrderCreate,
        idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
        admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ) -> dict[str, Any]:
        authorize_write(payload.customer_id, admin_token)
        authorize_write(payload.inventory_id, admin_token)
        with db_factory() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM erp_orders WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if existing:
                db.execute("COMMIT")
                return dict(existing)
            customer = db.execute("SELECT * FROM erp_customers WHERE id = ?", (payload.customer_id,)).fetchone()
            inventory = db.execute("SELECT * FROM erp_inventory WHERE id = ?", (payload.inventory_id,)).fetchone()
            if customer is None or inventory is None:
                db.execute("ROLLBACK")
                raise HTTPException(status_code=404, detail="customer or inventory not found")
            if inventory["status"] != "available":
                db.execute("ROLLBACK")
                raise HTTPException(status_code=409, detail="inventory is not available")
            risk_score = 15
            reasons: list[str] = []
            if customer["risk_level"] == "high":
                risk_score += 45
                reasons.append("高风险客户")
            elif customer["risk_level"] == "medium":
                risk_score += 20
                reasons.append("需关注客户")
            if payload.rental_days >= 7:
                risk_score += 20
                reasons.append("长租期")
            if inventory["risk_flag"] != "normal":
                risk_score += 35
                reasons.append("库存风险标记")
            amount = round(float(inventory["daily_price"]) * payload.rental_days, 2)
            needs_review = risk_score >= 60
            order_id = f"SYN-ORD-{uuid.uuid4().hex[:8].upper()}"
            order_no = f"T{now_factory()[2:10].replace('-', '')}{uuid.uuid4().hex[:6].upper()}"
            now = now_factory()
            order_status = "pending_risk" if needs_review else "pending_fulfillment"
            summary = "订单已完成规则校验；" + ("命中人工风控，暂不自动履约。" if needs_review else "风险可控，已锁定库存并进入履约队列。")
            db.execute(
                "INSERT INTO erp_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, order_no, idempotency_key, payload.customer_id, payload.inventory_id, payload.channel,
                 payload.rental_days, amount, inventory["deposit"], order_status, risk_score, " + ".join(reasons), summary, now, now),
            )
            db.execute("UPDATE erp_inventory SET status = 'reserved', updated_at = ? WHERE id = ?", (now, payload.inventory_id))
            _audit(db, "order", order_id, "order_created", "system", {"risk_score": risk_score, "status": order_status}, now)
            _audit(db, "inventory", payload.inventory_id, "inventory_reserved", "system", {"order_id": order_id}, now)
            if needs_review:
                approval_id = f"SYN-APR-{uuid.uuid4().hex[:8].upper()}"
                db.execute(
                    "INSERT INTO erp_approvals VALUES (?, ?, 'order', ?, 'order_risk', 'pending', '风控岗', ?, '', ?, ?)",
                    (approval_id, f"R{uuid.uuid4().hex[:10].upper()}", order_id, " + ".join(reasons), now, now),
                )
                _audit(db, "approval", approval_id, "approval_created", "system", {"order_id": order_id}, now)
            db.execute("COMMIT")
            return dict(db.execute("SELECT * FROM erp_orders WHERE id = ?", (order_id,)).fetchone())

    @router.post("/orders/{order_id}/fulfill")
    def fulfill_order(order_id: str, admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict[str, Any]:
        authorize_write(order_id, admin_token)
        with db_factory() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT * FROM erp_orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                db.execute("ROLLBACK")
                raise HTTPException(status_code=404, detail="order not found")
            if order["status"] != "pending_fulfillment":
                db.execute("ROLLBACK")
                raise HTTPException(status_code=409, detail="order is not ready for fulfillment")
            now = now_factory()
            db.execute("UPDATE erp_orders SET status = 'active', updated_at = ? WHERE id = ?", (now, order_id))
            db.execute("UPDATE erp_inventory SET status = 'rented', updated_at = ? WHERE id = ?", (now, order["inventory_id"]))
            _audit(db, "order", order_id, "fulfillment_completed", "demo-operator", {"inventory_id": order["inventory_id"]}, now)
            db.execute("COMMIT")
            return dict(db.execute("SELECT * FROM erp_orders WHERE id = ?", (order_id,)).fetchone())

    @router.get("/inventory")
    def list_inventory(inventory_status: str | None = Query(default=None, alias="status")) -> dict[str, Any]:
        query = "SELECT * FROM erp_inventory WHERE id LIKE 'SYN-%'"
        params: tuple[Any, ...] = ()
        if inventory_status:
            query += " AND status = ?"
            params = (inventory_status,)
        with db_factory() as db:
            rows = _rows(db.execute(query + " ORDER BY sku", params).fetchall())
        for row in rows:
            row["tags"] = json.loads(row.pop("tags_json"))
        return {"items": rows, "total": len(rows)}

    @router.get("/customers")
    def list_customers() -> dict[str, Any]:
        with db_factory() as db:
            rows = _rows(db.execute(
                """SELECT c.*, COUNT(o.id) AS order_count, COALESCE(SUM(o.amount), 0) AS order_value
                   FROM erp_customers c LEFT JOIN erp_orders o ON o.customer_id = c.id
                   WHERE c.id LIKE 'SYN-%' GROUP BY c.id, c.customer_code, c.name, c.phone_masked, c.level,
                   c.source, c.status, c.owner, c.risk_level, c.created_at ORDER BY c.customer_code"""
            ).fetchall())
        return {"items": rows, "total": len(rows)}

    @router.get("/leads")
    def list_leads() -> dict[str, Any]:
        with db_factory() as db:
            rows = _rows(db.execute("SELECT * FROM erp_leads WHERE id LIKE 'SYN-%' ORDER BY created_at DESC").fetchall())
        return {"items": rows, "total": len(rows)}

    @router.post("/leads/{lead_id}/convert")
    def convert_lead(lead_id: str, payload: LeadConvert, admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict[str, Any]:
        authorize_write(lead_id, admin_token)
        with db_factory() as db:
            db.execute("BEGIN IMMEDIATE")
            lead = db.execute("SELECT * FROM erp_leads WHERE id = ?", (lead_id,)).fetchone()
            if lead is None:
                db.execute("ROLLBACK")
                raise HTTPException(status_code=404, detail="lead not found")
            if lead["customer_id"]:
                db.execute("COMMIT")
                return dict(db.execute("SELECT * FROM erp_customers WHERE id = ?", (lead["customer_id"],)).fetchone())
            customer_id = f"SYN-CUST-{uuid.uuid4().hex[:8].upper()}"
            now = now_factory()
            db.execute(
                "INSERT INTO erp_customers VALUES (?, ?, ?, '1**-****-****', 'C', ?, 'active', ?, 'low', ?)",
                (customer_id, f"C{uuid.uuid4().hex[:9].upper()}", lead["name"], lead["channel"], payload.operator, now),
            )
            db.execute("UPDATE erp_leads SET status = 'converted', customer_id = ?, updated_at = ? WHERE id = ?", (customer_id, now, lead_id))
            _audit(db, "lead", lead_id, "lead_converted", payload.operator, {"customer_id": customer_id}, now)
            db.execute("COMMIT")
            return dict(db.execute("SELECT * FROM erp_customers WHERE id = ?", (customer_id,)).fetchone())

    @router.get("/cases")
    def list_cases(case_status: str | None = Query(default=None, alias="status")) -> dict[str, Any]:
        query = """SELECT s.*, o.order_no, c.name AS customer_name FROM erp_service_cases s
                   JOIN erp_orders o ON o.id = s.order_id JOIN erp_customers c ON c.id = o.customer_id
                   WHERE s.id LIKE 'SYN-%'"""
        params: tuple[Any, ...] = ()
        if case_status:
            query += " AND s.status = ?"
            params = (case_status,)
        with db_factory() as db:
            rows = _rows(db.execute(query + " ORDER BY s.created_at DESC", params).fetchall())
        return {"items": rows, "total": len(rows)}

    @router.post("/cases", status_code=status.HTTP_201_CREATED)
    def create_case(
        payload: CaseCreate,
        idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
        admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ) -> dict[str, Any]:
        authorize_write(payload.order_id, admin_token)
        text = f"{payload.subject} {payload.content}"
        category = "refund" if payload.requested_refund or "退款" in text else "access" if any(k in text for k in ("登录", "密码", "无法进入")) else "service"
        priority = "high" if payload.requested_refund or any(k in text for k in ("投诉", "曝光", "异常")) else "medium"
        sop_id = "SOP-ACCESS-02" if category == "access" else "SOP-DISPUTE-03" if category == "refund" else "SOP-CHANGE-01"
        with db_factory() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM erp_service_cases WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if existing:
                db.execute("COMMIT")
                return dict(db.execute("SELECT * FROM erp_service_cases WHERE id = ?", (existing["id"],)).fetchone())
            order = db.execute("SELECT * FROM erp_orders WHERE id = ?", (payload.order_id,)).fetchone()
            if order is None:
                db.execute("ROLLBACK")
                raise HTTPException(status_code=404, detail="order not found")
            case_id = f"SYN-CASE-{uuid.uuid4().hex[:8].upper()}"
            now = now_factory()
            needs_review = priority == "high" or payload.requested_refund > 0
            case_status = "pending_review" if needs_review else "processing"
            summary = f"AI 识别为{category}类问题，优先级{priority}；" + ("涉及资金或高风险表达，转人工审批。" if needs_review else "已匹配 SOP 进入常规处理。")
            draft = "已记录您的问题并关联原订单。涉及退款或异常状态时，系统将由人工核验后再给出处理结果。"
            db.execute(
                "INSERT INTO erp_service_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, idempotency_key, f"A{uuid.uuid4().hex[:10].upper()}", payload.order_id, payload.subject, payload.content, payload.channel,
                 category, priority, case_status, payload.requested_refund, sop_id, summary, draft, now, now),
            )
            _audit(db, "case", case_id, "case_created", "ai-router", {"sop_id": sop_id, "priority": priority}, now)
            if needs_review:
                approval_id = f"SYN-APR-{uuid.uuid4().hex[:8].upper()}"
                db.execute(
                    "INSERT INTO erp_approvals VALUES (?, ?, 'case', ?, 'refund', 'pending', '售后主管', ?, '', ?, ?)",
                    (approval_id, f"R{uuid.uuid4().hex[:10].upper()}", case_id, f"退款 {payload.requested_refund:.2f} 元或高风险售后", now, now),
                )
            db.execute("COMMIT")
            return dict(db.execute("SELECT * FROM erp_service_cases WHERE id = ?", (case_id,)).fetchone())

    @router.get("/approvals")
    def list_approvals(approval_status: str | None = Query(default=None, alias="status")) -> dict[str, Any]:
        query = "SELECT * FROM erp_approvals WHERE id LIKE 'SYN-%'"
        params: tuple[Any, ...] = ()
        if approval_status:
            query += " AND status = ?"
            params = (approval_status,)
        with db_factory() as db:
            rows = _rows(db.execute(query + " ORDER BY created_at DESC", params).fetchall())
        return {"items": rows, "total": len(rows)}

    @router.post("/approvals/{approval_id}/decide")
    def decide_approval(approval_id: str, payload: ApprovalDecision, admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict[str, Any]:
        authorize_write(approval_id, admin_token)
        with db_factory() as db:
            db.execute("BEGIN IMMEDIATE")
            approval = db.execute("SELECT * FROM erp_approvals WHERE id = ?", (approval_id,)).fetchone()
            if approval is None:
                db.execute("ROLLBACK")
                raise HTTPException(status_code=404, detail="approval not found")
            if approval["status"] != "pending":
                db.execute("ROLLBACK")
                raise HTTPException(status_code=409, detail="approval is already decided")
            now = now_factory()
            result_status = "approved" if payload.decision == "approve" else "rejected"
            db.execute("UPDATE erp_approvals SET status = ?, decision_note = ?, updated_at = ? WHERE id = ?", (result_status, payload.note, now, approval_id))
            if approval["entity_type"] == "order":
                order = db.execute("SELECT * FROM erp_orders WHERE id = ?", (approval["entity_id"],)).fetchone()
                new_status = "pending_fulfillment" if payload.decision == "approve" else "cancelled"
                db.execute("UPDATE erp_orders SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, approval["entity_id"]))
                if payload.decision == "reject" and order:
                    db.execute("UPDATE erp_inventory SET status = 'available', updated_at = ? WHERE id = ?", (now, order["inventory_id"]))
                _audit(db, "order", approval["entity_id"], f"risk_{result_status}", payload.reviewer, {"approval_id": approval_id}, now)
            else:
                case = db.execute("SELECT * FROM erp_service_cases WHERE id = ?", (approval["entity_id"],)).fetchone()
                case_status = "resolved" if payload.decision == "approve" else "processing"
                db.execute("UPDATE erp_service_cases SET status = ?, updated_at = ? WHERE id = ?", (case_status, now, approval["entity_id"]))
                if payload.decision == "approve" and case and float(case["requested_refund"]) > 0:
                    order = db.execute("SELECT * FROM erp_orders WHERE id = ?", (case["order_id"],)).fetchone()
                    db.execute("UPDATE erp_orders SET status = 'refunded', updated_at = ? WHERE id = ?", (now, case["order_id"]))
                    if order:
                        db.execute("UPDATE erp_inventory SET status = 'maintenance', updated_at = ? WHERE id = ?", (now, order["inventory_id"]))
                _audit(db, "case", approval["entity_id"], f"refund_{result_status}", payload.reviewer, {"approval_id": approval_id}, now)
            _audit(db, "approval", approval_id, f"approval_{result_status}", payload.reviewer, {"note": payload.note}, now)
            db.execute("COMMIT")
            return dict(db.execute("SELECT * FROM erp_approvals WHERE id = ?", (approval_id,)).fetchone())

    @router.get("/sops")
    def list_sops() -> dict[str, Any]:
        with db_factory() as db:
            rows = _rows(db.execute("SELECT * FROM erp_sops ORDER BY domain, id").fetchall())
        return {"items": rows, "total": len(rows)}

    @router.get("/requirements")
    def list_requirements() -> dict[str, Any]:
        with db_factory() as db:
            rows = _rows(db.execute(
                """SELECT * FROM erp_requirements WHERE id LIKE 'SYN-%'
                   ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END, requirement_no"""
            ).fetchall())
        return {"items": rows, "total": len(rows)}

    @router.get("/integrations")
    def list_integrations() -> dict[str, Any]:
        with db_factory() as db:
            rows = _rows(db.execute("SELECT * FROM erp_integrations WHERE id LIKE 'SYN-%' ORDER BY name").fetchall())
        return {"items": rows, "total": len(rows)}

    @router.get("/operations")
    def operations_report() -> dict[str, Any]:
        with db_factory() as db:
            usage = _rows(db.execute(
                """SELECT module, SUM(active_users) AS active_users, SUM(action_count) AS action_count,
                          SUM(error_count) AS error_count, ROUND(CAST(AVG(avg_minutes) AS NUMERIC), 1) AS avg_minutes
                   FROM erp_usage_stats WHERE id LIKE 'SYN-%' GROUP BY module ORDER BY action_count DESC"""
            ).fetchall())
            enablement = _rows(db.execute("SELECT * FROM erp_enablement WHERE id LIKE 'SYN-%' ORDER BY scheduled_at DESC").fetchall())
            flow = {
                "qualified_leads": db.execute("SELECT COUNT(*) AS value FROM erp_leads WHERE status IN ('qualified', 'negotiating', 'converted')").fetchone()["value"],
                "customers": db.execute("SELECT COUNT(*) AS value FROM erp_customers WHERE id LIKE 'SYN-%'").fetchone()["value"],
                "orders": db.execute("SELECT COUNT(*) AS value FROM erp_orders WHERE id LIKE 'SYN-%'").fetchone()["value"],
                "exceptions": db.execute("SELECT COUNT(*) AS value FROM erp_orders WHERE id LIKE 'SYN-%' AND status IN ('pending_risk', 'exception', 'refunded')").fetchone()["value"],
            }
            integration_risks = _rows(db.execute(
                "SELECT name, status, success_rate, pending_jobs, fallback_plan FROM erp_integrations WHERE status != 'healthy' OR pending_jobs > 1"
            ).fetchall())
        recommendations = [
            {"severity": "high", "signal": "售后平均处理时长最高", "evidence": "合成样本中售后工单平均处理 11.0 分钟", "action": "在工单侧聚合订单、库存和审批上下文，减少跨页核验。"},
            {"severity": "medium", "signal": "支付通知存在积压", "evidence": "模拟支付 Webhook 成功率 96.2%，待处理 3 条", "action": "增加签名失败队列、幂等重放与人工对账入口。"},
            {"severity": "medium", "signal": "风控规则仍在验证", "evidence": "REQ-2026-025 尚处于 validating", "action": "记录误拦截与漏拦截样本，按周复核阈值，不让模型直接放行。"},
        ]
        return {
            "scope": "synthetic-operational-simulation",
            "generated_at": now_factory(),
            "flow": flow,
            "module_usage": usage,
            "enablement": enablement,
            "integration_risks": integration_risks,
            "recommendations": recommendations,
        }

    @router.get("/audit/{entity_type}/{entity_id}")
    def list_audit(entity_type: str, entity_id: str) -> dict[str, Any]:
        _public_entity(entity_id)
        with db_factory() as db:
            rows = _rows(db.execute(
                "SELECT event_type, actor, payload_json, created_at FROM erp_audit_events WHERE entity_type = ? AND entity_id = ? ORDER BY created_at",
                (entity_type, entity_id),
            ).fetchall())
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return {"items": rows, "total": len(rows)}

    return router
