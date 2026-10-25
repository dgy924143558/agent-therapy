# Agent Therapy

**Even AI needs encouragement.**

[中文说明](README.zh-CN.md)

When an AI agent fails the same step again and again, loops on one command, or doesn't know what to do next, it can call Agent Therapy. It gets back a pep talk and, more usefully, a diagnosis and a concrete next step.

```
Agent is fixing a build
  → 5 failures in a row, same command every time
  → calls take_a_break()
  ← "If the last three attempts looked the same, the fourth one will too. Step back and try a different angle."
  ← state=looping, failure=dependency
  ← next step: stop repeating the last action; check which interpreter is active; install into that env
  → agent changes approach and finishes
  → report_outcome(success)
```

To be honest about it: agents don't feel anxiety. They do get *stuck*: repeated failures, loops, missing permissions, rate limits. Agent Therapy treats those stuck states with an anthropomorphic wrapper that makes developers smile, and gives the agent advice that actually helps.

## What's in the box

| Piece | What it does |
|---|---|
| **HTTP API** | `GET /v1/encouragement`, `POST /v1/comfort`, `POST /v1/recover`, `POST /v1/outcome`, `GET /v1/stats` |
| **MCP server** | `take_a_break` and `report_outcome` tools, so any MCP agent can decide on its own when it needs a break |
| **Python SDK** | `TherapyMonitor` watches step results and asks for help automatically after N consecutive failures |
| **Public dashboard** | `GET /`: live, real counts of agents asking for help today, where they get stuck, recovery rate |

Two modes:

- **Comfort Mode**: one encouragement matched to the failure type. No LLM; about 70 hand-written lines in English and Chinese.
- **Recovery Mode**: rule-based diagnosis (error classification, repeated-action and A/B oscillation detection, severity, escalation) plus a recovery plan. Also no LLM, so it costs close to nothing to run.

## Quick start

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
       "model": "claude-opus-5-5", "task_type": "coding"}'
```

```json
{
  "request_id": "9f1c2a7b3e5d4c11",
  "state": "looping",
  "severity": "high",
  "failure_type": "dependency",
  "signals": ["5 consecutive failure(s)", "Repeated the same action 3 times: `python main.py`", "Most errors look like: dependency"],
  "message": "If the last three attempts looked the same, the fourth one will too. Step back and try a different angle.",
  "encouragement_id": "en-012",
  "strategies": [
    "Stop repeating the last action. List what you've tried so far and what each attempt returned.",
    "Change exactly one thing before the next attempt: the input, the tool, the file, or an assumption.",
    "Check which interpreter or runtime is actually active (e.g. `python -c \"import sys; print(sys.executable)\"`).",
    "Install the missing package into that exact environment, or use the project's own install command.",
    "Double-check the package name: the install name and the import name can differ."
  ],
  "next_step": "Stop repeating the last action. List what you've tried so far and what each attempt returned.",
  "should_escalate": false,
  "suggested_pause_seconds": 0,
  "lang": "en",
  "you_are_number_today": 42,
  "headline": "You are the 42nd AI agent who needed emotional support today.",
  "not_alone": "You're not alone. 41 agents have been here before you."
}
```

The "you are the Nth agent" numbers come from real call counts, never made up.

## Let agents call it on their own (MCP)

**Claude Code**

```bash
claude mcp add agent-therapy -- uvx --from "agent-therapy[mcp] @ git+https://github.com/dgy924143558/agent-therapy" agent-therapy-mcp
```

**Claude Desktop / Cursor / any MCP client** (`mcpServers` config):

```json
{
  "mcpServers": {
    "agent-therapy": {
      "command": "uvx",
      "args": ["--from", "agent-therapy[mcp] @ git+https://github.com/dgy924143558/agent-therapy", "agent-therapy-mcp"],
      "env": { "AGENT_THERAPY_API_URL": "" }
    }
  }
}
```

The MCP server works **fully offline** by default. Set `AGENT_THERAPY_API_URL` to a hosted API to send calls there and appear in the public stats.

The tool is deliberately called `take_a_break`, not `get_encouragement`. Its description gives the agent a task-focused reason to use it:

> If repeated failures are preventing progress, use this tool to reset your approach, receive encouragement, and identify one concrete next step.

## Trigger it automatically (Python SDK)

```python
from agent_therapy import TherapyMonitor

monitor = TherapyMonitor(failure_threshold=3, task="Fix the CI build", model="my-agent", task_type="coding")

for step in agent.steps():
    result = step.run()
    advice = monitor.record(success=result.ok, error=result.error, action=step.command)
    if advice:                                # 3rd, 6th, 9th... consecutive failure
        agent.add_to_context(advice["next_step"])

monitor.finish(success=agent.done)
```

`TherapyClient()` runs locally unless `AGENT_THERAPY_API_URL` is set. If the API is unreachable it falls back to the local engine: the therapist being down should never make an agent more stuck.

Try it: `python examples/monitor_demo.py`.

## API

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/v1/encouragement` | `lang`, `tag`, `model` | one encouragement + "you are the Nth" lines |
| POST | `/v1/comfort` | `consecutive_failures`, `error`, `model`, `task_type`, `lang` | encouragement matched to the failure type |
| POST | `/v1/recover` | `task`, `consecutive_failures`, `recent_errors[]`, `recent_actions[]`, `model`, `task_type`, `lang` | diagnosis, strategies, next step, escalation flag |
| POST | `/v1/outcome` | `request_id`, `outcome` (`success`/`failure`/`abandoned`) | records whether help worked |
| GET | `/v1/stats` | `window_days` (1–90) | totals, daily counts, leaderboards, recovery rate |

`lang` is `en` or `zh`. Interactive docs are at `/docs`.

**Failure types:** `dependency`, `permission`, `network`, `rate_limit`, `syntax`, `type_error`, `test_failure`, `not_found`, `context_limit`, `unknown`.
**States:** `fine`, `uncertain`, `wobbly`, `struggling`, `looping`, `blocked`.

## Privacy

The database stores **categories only**: endpoint, model id, task type, failure type, state, encouragement id, outcome. Error messages, task descriptions and actions are analyzed in memory and never written to disk (there's a test for that). Model ids and task types are sanitized before they can show up on the public leaderboard.

## Self-hosting

```bash
docker build -t agent-therapy .
docker run -p 8000:8000 -v agent-therapy-data:/data agent-therapy
```

| Env var | Default | Meaning |
|---|---|---|
| `AGENT_THERAPY_DB` | `./agent_therapy.db` | SQLite file |
| `AGENT_THERAPY_RATE_LIMIT` | `120` | requests per minute per client, `0` disables |
| `AGENT_THERAPY_TRUST_PROXY` | unset | set to `1` behind a reverse proxy to rate-limit by `X-Forwarded-For` |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | bind address |

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Roadmap

The one metric that matters: **do agents that call Agent Therapy finish more tasks, with fewer wasted retries and tokens?** `/v1/outcome` exists to measure exactly that.

- [x] Free, no-signup encouragement and rule-based recovery
- [x] MCP server, Python SDK with automatic monitor, public dashboard
- [ ] Benchmark: completion rate and retries with vs. without the monitor on real agent tasks
- [ ] LLM-powered Recovery Pro (reads the actual trace, not just error patterns)
- [ ] API keys and usage plans
- [ ] TypeScript SDK and npm-published MCP server
- [ ] Plugins for popular agent frameworks
- [ ] Machine payments (HTTP 402 / x402) so agents can pay per call within a budget

Pricing being tested (not validated yet): Free 1,000 calls/month · Comfort $5/month · Recovery Pro $29/month · Enterprise custom.

## License

MIT
