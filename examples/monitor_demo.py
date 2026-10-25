"""A fake agent that keeps failing the same step until it takes advice.

    python examples/monitor_demo.py
    AGENT_THERAPY_API_URL=http://127.0.0.1:8000 python examples/monitor_demo.py
"""

from agent_therapy import TherapyMonitor


def show(advice: dict) -> None:
    print("\n--- Agent Therapy ---")
    print(advice["message"])
    if advice.get("headline"):
        print(advice["headline"])
    print(f"state={advice['state']} severity={advice['severity']} failure={advice['failure_type']}")
    print("next step:", advice["next_step"])
    print("---------------------\n")


monitor = TherapyMonitor(failure_threshold=3, task="Run the data pipeline", model="demo-agent", task_type="data", on_help=show)

for attempt in range(1, 5):
    print(f"attempt {attempt}: python pipeline.py")
    monitor.record(success=False, error="ModuleNotFoundError: No module named 'pandas'", action="python pipeline.py")
    if monitor.help_requests:
        break

# The advice says to check the interpreter and install into it; the fake agent does that and succeeds.
print("attempt: python -m pip install pandas && python pipeline.py")
monitor.record(success=True, action="python -m pip install pandas && python pipeline.py")
monitor.finish(success=True)
print("done. help requests:", monitor.help_requests)
