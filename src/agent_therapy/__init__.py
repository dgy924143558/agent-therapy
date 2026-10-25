"""Agent Therapy: even AI needs encouragement."""

__version__ = "0.1.0"

from .client import TherapyClient, TherapyError, TherapyMonitor  # noqa: E402
from .encouragements import pick_encouragement  # noqa: E402
from .recovery import Recovery, classify_error, comfort, recover  # noqa: E402

__all__ = [
    "Recovery",
    "TherapyClient",
    "TherapyError",
    "TherapyMonitor",
    "__version__",
    "classify_error",
    "comfort",
    "pick_encouragement",
    "recover",
]
