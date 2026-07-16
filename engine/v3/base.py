from abc import ABC, abstractmethod
from typing import Any


class BaseV3Strategy(ABC):
    name: str = ""
    description: str = ""
    applies_to: tuple[str, ...] = ("stock",)
    default_weight: float = 0.05

    @abstractmethod
    def compute(self, context: dict) -> dict:
        ...

    def get_required_data(self) -> list[str]:
        return []
