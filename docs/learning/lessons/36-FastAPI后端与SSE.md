# 第 36 课：FastAPI 后端与 SSE

## 1. 解决什么问题

命令行适合学习和调试，但外部程序需要稳定的 HTTP 请求格式。现在提供普通回答接口和按步骤发送事件的流式接口，不做前端。

## 2. 架构位置

`web/api.py` 是薄适配层，两种接口都调用已有 `run_workflow()`。`WorkflowResult` 仍是统一输出；没有另写第二套 Agent 图。

## 3. 本次文件与方法

- `web/api.py`：`AskRequest` 校验问题与会话 ID；`create_app()` 创建 `/health`、`/v1/ask`、`/v1/ask/stream`。
- `workflow.py` 与 `agents/harness.py`：可选 `event_sink` 将实际执行事件送到 SSE。
- `pyproject.toml`、`uv.lock`：锁定 FastAPI、Uvicorn 和测试用 httpx。

## 4. 调用链与数据载体

JSON 问题 → `AskRequest` → `run_workflow()` → `WorkflowResult` → JSON 回答。流式接口另把 Harness 事件送入队列，按 `step`、`result`、`done` 事件发出；错误时发 `error`，不返回原始模型异常文本。

## 5. 关键执行过程

先运行 `uv run uvicorn taobao_agent.web.api:app --host 127.0.0.1 --port 8000`：在本机启动接口。再访问 `/health`：只验证服务启动；POST `/v1/ask` 才会调用模型和数据链。`/v1/ask/stream` 会发送实际 Harness 步骤事件，结果仍在最后一个 `result` 中。

## 6. 为什么这样设计

FastAPI 是原文 Web 后端要求；前端按用户要求省略。SSE 适合单向展示执行进度。接口默认只绑定本机；当前没有身份认证与租户隔离，不应直接暴露到公网。

## 7. 测试与限制

最终 47 项离线测试通过，FastAPI `TestClient` 检查健康、JSON、SSE 与错误输入。没有 API Key 的本轮未验证真实 HTTP→Qwen 链路；在线评测留到第 37 课。SSE 不是逐 Token 输出，事件为节点/工具执行记录；同步模型调用的超时不能强制中断底层线程。

## 8. 相关知识

第十二章评估：接口测试只证明协议与本地假模型链路，业务效果要用 Golden Dataset 和真实模型另测。SSE 用法依照 [FastAPI 官方文档](https://fastapi.tiangolo.com/tutorial/server-sent-events/)。

## 9. 面试知识点

解释 API 层与 Agent 工作流分离、请求校验、执行事件与结果事件的区别、SSE 的单向传输限制。这是常见技术知识点。

## 10. 下一课前置条件

第 37 课由我们一起运行真实模型评测，扩大问题集并复盘失败；本课不把离线通过称为线上可靠。
