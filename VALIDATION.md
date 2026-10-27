# 本次验证记录

环境：Windows，Python 3.11.17，独立 `.venv`。实际安装版本已保存到 `requirements.lock.txt`。

## 已执行并通过

| 验证 | 实际结果 |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest -q` | **38 passed in 26.67s** |
| `.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| Ruff 静态检查 | All checks passed |
| 独立进程 HTTP 验证 | 正常退出，exit code 0 |

自动化测试覆盖：

- 信息不足 → 中断追问 → 同会话继续。
- 普通售后查询订单和政策，实际结果影响规则判断。
- 客服争议额外查询历史，识别冲突并转人工。
- 人工批准、拒绝、要求补充信息及再次审核。
- 应用关闭再创建后恢复用户追问和审核。
- 重复开始请求、重复补充请求、并发重复审批、同订单重复申请。
- 订单归属、审批权限、工具参数跨订单攻击及未知证据引用。
- 模型失败后重试、回复失败后的确定性模板降级。
- 追问及工具规划预算。
- 业务提交成功、检查点尚未保存时崩溃，恢复后不重复操作。
- 七天/三十天时间边界、高金额审核及审批后订单/金额变化。
- Streamlit 聊天、审批及网络响应丢失后的幂等重试。
- 模型适配器的 JSON 校验、有限纠正及 Function Calling 协议。

## 独立进程验证

复现命令：

```powershell
.\.venv\Scripts\python.exe scripts\verify_live.py
```

脚本启动临时后端和 Streamlit，使用临时数据库，通过真实 HTTP 验证，结束后关闭进程并清理测试数据。Windows 下关闭进程树，并确认旧后端监听消失后才启动新后端，避免把“新客户端连接旧服务”误当作重启恢复。

本次成功输出：

```json
{
  "scenario_a": "passed",
  "scenario_b": "passed",
  "scenario_c": "passed",
  "process_restart_resume": "passed",
  "duplicate_review": "passed",
  "streamlit_health": "passed",
  "mode": "demo"
}
```

## 尚未验证与明确边界

- 没有使用真实 DeepSeek 密钥进行联网模型验证。适配器测试使用模拟 API 响应，不代表真实模型准确率、延迟或费用。
- 所有端到端测试使用明确标注的 DemoModel；没有把它包装成真实 LLM 测试。
- Streamlit 交互由 AppTest 验证，另验证了实际前端服务健康状态；没有声称完成所有浏览器的视觉兼容测试。
- 不连接支付、物流或真实电商系统；“退款/换货成功”仅指本地模拟状态变更。
- 单进程执行锁用于本地演示，不支持多 worker 或分布式任务调度。
