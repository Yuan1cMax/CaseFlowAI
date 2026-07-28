# TradeOps ERP 验收场景

## AC-01 低风险订单自动进入履约队列

- Given：客户风险低、账号库存可用、租期 2 天。
- When：销售提交订单。
- Then：订单状态为 `pending_fulfillment`，库存状态为 `reserved`，写入订单和库存审计事件。

## AC-02 防止库存重复分配

- Given：账号已处于 `reserved` 或 `rented`。
- When：另一订单尝试使用同一库存。
- Then：返回冲突，不创建订单，不改变原库存状态。

## AC-03 高风险订单人工审批

- Given：高风险客户且租期 7 天，风险分达到 60。
- When：创建订单。
- Then：订单进入 `pending_risk` 并生成审批；审批前履约接口拒绝执行。

## AC-04 驳回后释放库存

- Given：高风险订单已锁定库存并等待审批。
- When：风控驳回。
- Then：订单变为 `cancelled`，库存恢复 `available`，审计轨迹记录操作人。

## AC-05 审批后完成履约

- Given：订单处于 `pending_risk`。
- When：风控批准，运营确认交付。
- Then：订单先变为 `pending_fulfillment`，再变为 `active`；库存变为 `rented`。

## AC-06 售后退款联动

- Given：履约中订单产生登录异常并申请退款。
- When：售后主管批准退款。
- Then：工单变为 `resolved`，订单变为 `refunded`，库存进入 `maintenance`，资金动作不由 AI 自动执行。

## AC-07 线索转客户幂等

- Given：同一条 AI 导购线索。
- When：操作员重复点击转客户。
- Then：仅创建一个客户档案，两次返回相同客户 ID。

## AC-08 运营分析输出

- Given：固定合成的使用、异常和集成数据。
- When：生成运营报告。
- Then：报告包含模块使用量、处理时长、集成风险、证据和明确改进动作，并标注为合成模拟。

## 自动化覆盖

`tests/test_tradeops.py` 覆盖以上主链；`tests/test_workflow.py` 保留原 ServiceOps 的分类、审批、异步投递和公开数据隔离回归。
