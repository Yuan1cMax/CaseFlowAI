# CaseFlow AI

客户投诉工单智能协同与 SOP 自动化平台。它是一个独立研发、基于真实业务场景抽象的展示项目，不连接真实客户资料或生产 CRM。

## 解决的问题

客服投诉处理通常涉及风险识别、SOP 查阅、人工审批和跨系统回写。CaseFlow AI 把这些环节拆成可审计的状态机：

1. API 接收工单，使用 `Idempotency-Key` 避免重复创建。
2. 分类适配器输出类别、优先级、实体、置信度和人工审核判断。
3. SOP 检索器返回规则编号与依据，生成可审核的回复草稿。
4. 退款、投诉、赔偿、舆情等高风险工单进入人工审核队列。
5. 已批准工单进入投递队列；worker 原子认领任务，并由 CRM 适配器写入目标系统。
6. 投递失败记录错误类型并重试，达到上限后将工单标记为 `delivery_failed`。
7. 每一步写入审计事件，可按工单回放处理过程。

默认使用确定性规则分类器和模拟 CRM，因此测试可重复且不会调用外部模型或真实业务系统。生产接入时只需将 `RuleBasedAnalyzer` 和 `MockCrmAdapter` 替换为受控的 LLM/CRM 实现，并保留相同契约。

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
```

当前自动化测试覆盖高风险人工审核与投递、幂等重放和低风险自动队列。`data/synthetic_tickets.jsonl` 只包含合成样本，不能用于对外宣称真实客户数据。

## 工程边界

- SQLite/WAL 用于本地演示及并发正确性测试；正式多实例部署应迁移到 PostgreSQL，并用独立 worker/消息队列调度投递任务。
- 当前 CRM 适配器是模拟实现；不能宣称已接入企业 CRM、影刀或生产 RPA，除非实际完成集成及验收。
- 分类器为本地确定性实现；接入 LLM 前应增加脱敏、结构化输出校验、离线标注集评测与人工兜底策略。
