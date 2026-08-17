# TradeOps ERP - AI 驱动的交易履约运营中台

TradeOps ERP 是一套面向数字商品交易业务的 ERP 原型，覆盖售前线索、客户、账号库存、订单履约、售后、风控审批、SOP、需求管理、系统集成和运营分析。项目基于作者此前公司的业务经验独立设计研发，所有客户、订单、金额、使用统计和系统状态均为脱敏后的固定合成数据。

原 `ServiceOps AI` 的投诉分流、人工审批、审计、幂等与异步投递能力已保留，并成为 TradeOps ERP 的售后子域和基础工程能力。

## 在线地址

- 产品演示：`http://101.43.56.2:8898/tradeops/`
- API 文档：`http://101.43.56.2:8898/tradeops/docs`
- 个人作品集：`http://101.43.56.2:8898/`

## 为什么做这套系统

TunTunAgent 解决了售前问答与商品导购，但业务问题不会在 AI 回复结束时消失。高意向线索需要跟进，商品账号需要锁定，订单需要履约，高风险交易需要审批，异常需要进入售后，系统上线后还要观察使用率和流程瓶颈。

TradeOps ERP 将这些分散动作放进统一业务主键、确定性状态机和可回放审计轨迹中，形成以下闭环：

```text
AI 导购 -> 售前线索 -> 客户 -> 库存锁定 -> 订单风控 -> 人工审批
       -> 履约交付 -> 售后工单 -> 退款审批 -> 库存维护/回收 -> 运营复盘
```

## 可验证结果

- 11 个业务与运营模块共用统一数据源，不是互不关联的静态页面。
- 下单使用数据库事务和幂等键，非可用库存无法被重复分配。
- 风险分达到阈值时强制进入人工审批，审批前不能履约。
- 退款批准会同步更新工单、订单和库存状态，并写入跨实体审计事件。
- 公开演示只能操作 `SYN-*` 合成记录，避免误触真实数据。
- 18 项自动化测试通过，其中 9 项覆盖 ERP 跨模块业务副作用。
- PostgreSQL 用于部署，SQLite 用于本地开发和隔离测试。

上述数量是仓库当前实现和自动化测试结果，不代表真实生产用户、收入或 SLA。

## 模块地图

| 模块 | 解决的问题 | 关键控制点 |
| --- | --- | --- |
| 运营工作台 | 管理者统一查看待办、风险和履约状态 | 指标下钻到订单与审批 |
| 需求中心 | 统一收集部门痛点和版本计划 | P0/P1、负责人、验收标准 |
| 客户与线索 | AI 导购结果进入人工跟进流程 | 线索转客户保持幂等 |
| 账号库存 | 管理唯一数字商品的可用、锁定、租用和维护状态 | 事务锁定，防止重复分配 |
| 订单履约 | 管理租期、金额、押金、风险和交付 | 明确状态迁移和前置条件 |
| 售后工单 | 聚合订单、客户、SOP 和回复草稿 | 资金动作禁止 AI 自动承诺 |
| 风控审批 | 处理高风险订单与退款 | 人工最终决策、完整审计 |
| SOP | 统一处置规则和版本 | AI 可检索，业务规则仍确定性执行 |
| 集成监控 | 观察 Dify、业务 API、支付 Webhook 和通知 | 幂等、重试、降级方案 |
| 培训运营 | 记录培训、版本宣导与完成率 | 角色化培训与上线支持 |
| 运营分析 | 从使用和流程数据识别瓶颈 | 输出证据、问题和改进动作 |

## AI 的边界

AI 在项目中承担：

- 售前意图、预算与偏好提取
- 售后分类、优先级判断、SOP 推荐和回复草拟
- 风险摘要、需求摘要与流程改进建议

AI 不承担：

- 库存是否可分配
- 订单是否允许履约
- 退款金额是否批准
- 状态迁移、权限校验与审计记录

这些确定性动作由数据库约束、事务、规则阈值和人工审批控制。模型不可用时可以回退到规则分类和人工队列，不阻断核心交易主流程。

## 技术架构

```text
Browser ERP Console
        |
        v
FastAPI API / Request ID / Operator Guard
        |
        +-- TradeOps domain service
        |     +-- leads / customers / inventory / orders
        |     +-- service cases / approvals / SOP
        |     +-- requirements / integrations / operations
        |
        +-- AI adapter
        |     +-- rules fallback
        |     +-- OpenAI-compatible structured output
        |
        +-- integration adapter / durable worker
        |
        v
PostgreSQL (deploy) / SQLite (local tests)
```

## 核心状态机

订单：

```text
available inventory
  -> pending_risk -> approved -> pending_fulfillment -> active -> completed
                  -> rejected -> cancelled -> inventory released
  -> exception -> service case -> refund approval -> refunded -> maintenance
```

售后：

```text
created -> processing
        -> pending_review -> approved -> resolved
                          -> rejected -> processing
```

## 文档交付

- [产品需求说明](docs/PRODUCT_REQUIREMENTS.md)
- [验收场景](docs/ACCEPTANCE_SCENARIOS.md)
- [实施与运行手册](docs/IMPLEMENTATION_RUNBOOK.md)
- [培训与上线计划](docs/TRAINING_AND_ROLLOUT.md)
- [运营分析样例](docs/OPERATIONS_REPORT_SAMPLE.md)

## 本地运行

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
set PUBLIC_DEMO_MODE=1
.venv/Scripts/uvicorn main:app --reload --port 8000
```

打开 `http://127.0.0.1:8000`。首次进入会幂等加载固定合成数据。

运行测试：

```bash
.venv/Scripts/python -m pytest -q
```

容器部署：

```bash
docker compose up -d --build
```

## 公开演示安全边界

- 公开写操作只接受 `SYN-*` 合成实体。
- `.env`、数据库、日志和密钥不进入仓库。
- 所有外部系统名称、客户、订单和金额均为演示数据。
- `CASEFLOW_ADMIN_TOKEN` 可保护审批、履约和 worker 操作；名称为兼容旧版本保留。
- 真实环境还应接入企业 SSO/RBAC、数据库迁移工具、密钥管理、指标告警和备份恢复演练。

## 项目定位

本项目可以被准确描述为：

> 基于原公司数字商品交易业务经验，独立完成需求梳理、产品设计和工程实现的 ERP 运营中台原型；通过合成数据演示线索、库存、订单、售后、审批、集成监控与运营复盘闭环。

不能描述为原公司正式采购、真实生产运行或拥有真实经营数据。
