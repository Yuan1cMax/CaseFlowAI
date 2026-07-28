# CaseFlow AI

客户投诉工单智能协同与 SOP 自动化平台。它是一个独立研发、基于真实业务场景抽象的展示项目，不连接真实客户资料或生产 CRM。

- 在线演示：`http://101.43.56.2:8898/caseflow/`
- GitHub：`https://github.com/Yuan1cMax/CaseFlowAI`

## 解决的问题

客服投诉处理通常涉及风险识别、SOP 查阅、人工审批和跨系统回写。CaseFlow AI 把这些环节拆成可审计的状态机：

1. API 接收工单，使用 `Idempotency-Key` 避免重复创建。
2. 分类适配器输出类别、优先级、实体、置信度和人工审核判断。
3. SOP 检索器返回规则编号与依据，生成可审核的回复草稿。
4. 退款、投诉、赔偿、舆情等高风险工单进入人工审核队列。
5. 已批准工单进入投递队列；worker 原子认领任务，并由 CRM 适配器以 `Idempotency-Key` 写入目标系统。
6. 投递失败记录错误类型并重试，达到上限后将工单标记为 `delivery_failed`。
7. 每一步写入审计事件，可按工单回放处理过程。

默认使用确定性规则分类器和模拟 CRM，因此测试可重复且不会调用外部模型或真实业务系统。部署时可切换为 PostgreSQL、OpenAI-compatible 结构化分析器和受令牌保护的 CRM Webhook，三者均保持同一业务契约。

## 运行

```bash
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8010
```

健康检查：`GET http://127.0.0.1:8010/health`

创建高风险工单：

```bash
curl -X POST http://127.0.0.1:8010/tickets \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-refund-0001" \
  -d '{"customer_id":"synthetic-customer-01","subject":"退款投诉","content":"商品破损，我要求退款并准备投诉","channel":"web"}'
```

## 验证

```bash
python -m pytest -q
python evaluate.py
```

当前 6 项自动化测试覆盖高风险人工审核与投递、幂等重放、低风险自动队列、审核令牌鉴权、投递失败终态及公开演示边界。`evaluate.py` 对 10 条合成人工标注样本运行规则基线，当前分类、优先级和人工审核判断各为 100%；该结果仅代表该小型合成集，不能外推为真实客户数据或模型效果。

## 容器化部署

`docker-compose.yml` 运行 PostgreSQL、API 与独立 worker。先复制 `.env.docker.example` 为 `.env` 并生成随机 `POSTGRES_PASSWORD`、`CASEFLOW_ADMIN_TOKEN`：

```bash
docker-compose up -d --build
docker-compose ps
curl http://127.0.0.1:8010/health
```

生产环境的审核和手工投递接口需使用 `X-Admin-Token`。worker 从数据库中轮询待处理任务，数据库是状态真相来源，因此 API/worker 重启不会丢失已入库任务。`CRM_WEBHOOK_URL` 为空时启用本地模拟 CRM；填写该项前必须确认对方接口具备鉴权、幂等和最小化数据接收能力。

## 模型接入

默认 `ANALYZER_MODE=rules`，用于可重复演示。设置 `ANALYZER_MODE=openai_compatible`，并提供 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 后，可启用兼容 OpenAI Chat Completions 的结构化输出适配器。模型响应必须通过 Pydantic 校验；解析失败或上游不可用时，工单创建会返回 `503`，不会默认为低风险自动放行。

## 工程边界

- SQLite/WAL 用于本地演示及并发正确性测试；Docker 部署使用 PostgreSQL 与独立 worker，但尚未完成多实例压测和灾备演练。
- 默认 CRM 适配器为模拟实现；`HttpCrmAdapter` 已实现标准 Webhook 契约，但不能宣称已接入企业 CRM、影刀或生产 RPA，除非实际完成集成及验收。
- 默认分类器为本地确定性实现；OpenAI-compatible 适配器已包含结构化输出校验，但尚未在真实敏感数据上评测。接入前仍应完成脱敏、扩大标注集与人工兜底策略。
