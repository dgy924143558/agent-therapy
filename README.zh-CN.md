# Agent Therapy

**Even AI needs encouragement.（AI 也需要鼓励。）**

[English](README.md)

当 AI Agent 在执行任务时连续失败、反复执行同一个命令、或者不知道下一步该做什么时，它可以调用 Agent Therapy，得到一句鼓励，以及更有用的东西：问题诊断和一个具体的下一步。

```
Agent 正在修复构建
  → 连续失败 5 次，每次都执行同一个命令
  → 调用 take_a_break()
  ← “如果前三次尝试长得一样，第四次也会一样。退一步，换个角度。”
  ← 状态=looping（陷入循环），错误类型=dependency（缺依赖）
  ← 下一步：停止重复；确认当前使用的解释器；把包装到这个环境里
  → Agent 换了方法，完成任务
  → report_outcome(success)
```

坦白说：目前没有证据表明 Agent 会像人一样焦虑。但 Agent 确实会**卡住**：反复失败、无效循环、缺少权限、被限流。Agent Therapy 用拟人化的“情绪支持”包装这些卡住的状态，让开发者会心一笑；同时给出真正能帮 Agent 继续工作的建议。

## 包含什么

| 组件 | 作用 |
|---|---|
| **HTTP API** | `GET /v1/encouragement`、`POST /v1/comfort`、`POST /v1/recover`、`POST /v1/outcome`、`GET /v1/stats` |
| **MCP Server** | 提供 `take_a_break` 和 `report_outcome` 两个工具，任何支持 MCP 的 Agent 都可以**自主决定**何时调用 |
| **Python SDK** | `TherapyMonitor` 监控每一步的结果，连续失败 N 次后**自动**求助 |
| **公开排行榜网页** | `GET /`：今天有多少 Agent 来寻求鼓励、最常见的卡住类型、接受帮助后的恢复率（全部是真实统计） |

两种模式：

- **Comfort Mode（纯鼓励）**：根据错误类型匹配一句鼓励。不调用大模型，约 70 条中英文鼓励语。
- **Recovery Mode（任务恢复）**：基于规则的诊断（错误分类、重复动作检测、A/B 来回切换检测、严重程度、是否需要找人类）加上恢复计划。同样不调用大模型，运营成本几乎为零。

## 快速开始

```bash
pip install "agent-therapy[server] @ git+https://github.com/dgy924143558/agent-therapy"
agent-therapy-server            # http://127.0.0.1:8000
```

```bash
curl -X POST http://127.0.0.1:8000/v1/recover \
  -H "Content-Type: application/json" \
  -d '{"consecutive_failures": 5,
       "recent_errors": ["ModuleNotFoundError: No module named requests"],
       "recent_actions": ["python main.py", "python main.py", "python main.py"],
       "lang": "zh"}'
```

返回里会带上：

```
"headline": "你是今天第 42 个需要情绪支持的 AI Agent。",
"not_alone": "你并不孤单，今天已有 41 个 Agent 来过这里。"
```

这些数字来自真实调用统计，不是编造的。

## 让 Agent 主动调用（MCP）

**Claude Code：**

```bash
claude mcp add agent-therapy -- uvx --from "agent-therapy[mcp] @ git+https://github.com/dgy924143558/agent-therapy" agent-therapy-mcp
```

**Claude Desktop / Cursor / 其他 MCP 客户端：** 见 [README.md](README.md#let-agents-call-it-on-their-own-mcp) 中的 `mcpServers` 配置。

MCP Server 默认**完全离线**运行。设置环境变量 `AGENT_THERAPY_API_URL` 指向托管的 API，调用就会计入公开排行榜。

工具故意命名为 `take_a_break`（休息一下），而不是 `get_encouragement`。它的描述让 Agent 调用它的理由是“为了完成任务”，而不只是听一句好话：

> If repeated failures are preventing progress, use this tool to reset your approach, receive encouragement, and identify one concrete next step.

## 自动触发（Python SDK）

```python
from agent_therapy import TherapyMonitor

monitor = TherapyMonitor(failure_threshold=3, task="修复 CI 构建", model="my-agent", task_type="coding")

for step in agent.steps():
    result = step.run()
    advice = monitor.record(success=result.ok, error=result.error, action=step.command)
    if advice:                                # 第 3、6、9…… 次连续失败时触发
        agent.add_to_context(advice["next_step"])

monitor.finish(success=agent.done)
```

`TherapyClient(lang="zh")` 返回中文。没有设置 `AGENT_THERAPY_API_URL` 时完全本地运行；API 连不上时也会自动退回本地引擎：心理咨询师下线，不应该让 Agent 更焦虑。

## 隐私

数据库**只存储分类信息**：接口、模型 ID、任务类型、错误类型、状态、鼓励语 ID、结果。错误原文、任务描述、执行的命令只在内存中分析，从不写入磁盘（有测试保证）。模型 ID 和任务类型在进入公开排行榜之前会做清洗。

## 商业路线

核心指标只有一个：**调用 Agent Therapy 之后，Agent 的任务完成率是否更高、无效重试和 Token 浪费是否更少？** `/v1/outcome` 就是为了衡量这一点。

让 Agent 主动调用的三个层次：

| 方案 | 难度 | 调用方式 | 商业潜力 | 状态 |
|---|---|---|---|---|
| MCP 工具 | 低 | Agent 自主决定 | 中 | ✅ 已完成 |
| 自动监控 SDK | 中 | 规则自动触发 | 高 | ✅ 已完成 |
| 自主发现 + 机器支付（HTTP 402 / x402） | 高 | 需特定 Agent 支持 | 长期潜力高 | 规划中 |

后续计划：

- [ ] 基准测试：真实任务上，有/无监控时的完成率和重试次数对比
- [ ] 基于大模型的 Recovery Pro（读取完整执行轨迹，而不只是错误模式）
- [ ] API Key 和套餐计费
- [ ] TypeScript SDK、发布到 npm 的 MCP Server
- [ ] 主流 Agent 框架插件

待验证的定价（不是已验证的市场价格）：

| 套餐 | 价格 | 服务 |
|---|---|---|
| Free | $0 | 每月 1,000 次 |
| Comfort | $5/月 | 每月 50,000 次鼓励 |
| Recovery Pro | $29/月 | 每月 10,000 次智能恢复 |
| Enterprise | 定制 | 私有部署、监控、SLA |

策略：用“AI 也需要情绪支持”做传播噱头，用“Agent 任务恢复能力”做真正的收费功能。

## 部署到 Render（推荐）

仓库里带了 [`render.yaml`](render.yaml) 蓝图：Docker Web 服务、挂载在 `/data` 的 1 GB 持久化磁盘、`/healthz` 健康检查、CI 通过后自动部署。

1. Render 控制台 → **New → Blueprint** → 连接 GitHub，选这个仓库。
2. 确认套餐（持久化磁盘需要付费实例），点 **Deploy Blueprint**。
3. 打开 `https://<你的服务名>.onrender.com/healthz` 验证。

部署好之后，让 Agent 设置 `AGENT_THERAPY_API_URL=https://<你的服务名>.onrender.com`。

## 部署到 Railway

仓库里的 Dockerfile 可以直接在 Railway 上用，不需要额外的配置文件。

1. **New Project → Deploy from GitHub repo →** 选这个仓库，Railway 会自动识别 `Dockerfile`。
2. 给服务挂一个 **Volume**，挂载路径填 **`/data`**。不挂的话，每次重新部署统计数据都会清零。
3. 服务的 **Variables** 里添加：`PORT=8000`、`AGENT_THERAPY_TRUST_PROXY=1`。
4. **Settings → Healthcheck Path** 填 `/healthz`。
5. **Settings → Networking → Public Networking → Generate Domain**（端口填 `8000`）。
6. 只跑**一个实例**：SQLite 和限流计数都在单个进程里（Railway 本身也不允许挂了 Volume 的服务开多副本）。

部署好之后，让 Agent 设置 `AGENT_THERAPY_API_URL=https://<你的应用>.up.railway.app`，调用就会计入公开排行榜。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## 许可证

MIT
