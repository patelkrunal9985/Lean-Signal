from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional


class BaseAgent(ABC):
    """Abstract base class for all Kronos agents."""

    def __init__(self, name: str, config: dict = None):
        self.name = name
        self.config = config or {}
        from kronos.utils.logger import get_logger

        self.logger = get_logger(f"kronos.{name}")
        self.message_bus = None
        self.subagents: dict[str, BaseSubagent] = {}
        self._running = False
        self.metrics = {"tasks_processed": 0, "errors": 0, "last_run": None}

    def register_subagent(self, name: str, subagent: BaseSubagent):
        self.subagents[name] = subagent

    async def start(self):
        self._running = True
        self.logger.info(f"{self.name} started")

    async def stop(self):
        self._running = False
        self.logger.info(f"{self.name} stopped")

    @abstractmethod
    async def process(self, message: dict) -> dict:
        """Process an incoming message and return a response."""
        pass

    async def send_message(
        self, target: str, payload: dict, msg_type: str = "task"
    ) -> dict:
        """Send a message via the bus to another agent."""
        if self.message_bus:
            return await self.message_bus.send(self.name, target, payload, msg_type)
        return {"error": "No message bus connected"}

    def log_metrics(self, **kwargs):
        self.metrics.update(kwargs)
        self.logger.debug(f"Metrics: {self.metrics}")


class BaseSubagent(ABC):
    """Abstract base class for all subagents."""

    def __init__(self, name: str, parent: str, config: dict = None):
        self.name = name
        self.parent = parent
        self.config = config or {}
        from kronos.utils.logger import get_logger

        self.logger = get_logger(f"kronos.{parent}.{name}")

    @abstractmethod
    async def execute(self, context: dict) -> dict:
        """Execute the subagent's specific task."""
        pass
