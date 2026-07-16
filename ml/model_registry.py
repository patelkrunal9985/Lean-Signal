"""Model registry — persists and loads trained models with versioning."""
import json
import pickle
import os
from pathlib import Path
from datetime import datetime
from typing import Any
from kronos.utils.config import MODEL_DIR


class ModelRegistry:
    """Versioned model persistence."""

    def __init__(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        self.registry_path = MODEL_DIR / "model_registry.json"
        self._registry = self._load_registry()

    def _load_registry(self) -> dict:
        if self.registry_path.exists():
            try:
                with open(self.registry_path) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_registry(self):
        with open(self.registry_path, "w") as f:
            json.dump(self._registry, f, indent=2)

    def save(self, name: str, model: Any, metadata: dict = None) -> str:
        version = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_path = MODEL_DIR / f"{name}_{version}.pkl"

        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        entry = {
            "name": name,
            "version": version,
            "path": str(model_path),
            "saved_at": datetime.now().isoformat(),
            "metadata": metadata or {},
        }

        if name not in self._registry:
            self._registry[name] = []
        self._registry[name].append(entry)
        self._save_registry()

        return version

    def load(self, name: str, version: str = None) -> Any | None:
        entries = self._registry.get(name, [])
        if not entries:
            return None

        if version:
            entry = next((e for e in entries if e["version"] == version), None)
        else:
            entry = entries[-1]

        if not entry:
            return None

        model_path = Path(entry["path"])
        if not model_path.exists():
            return None

        try:
            with open(model_path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def get_versions(self, name: str) -> list[dict]:
        return self._registry.get(name, [])

    def get_latest_metadata(self, name: str) -> dict:
        entries = self._registry.get(name, [])
        if entries:
            return entries[-1].get("metadata", {})
        return {}

    def delete_old_versions(self, name: str, keep: int = 3):
        entries = self._registry.get(name, [])
        if len(entries) <= keep:
            return

        for entry in entries[:-keep]:
            model_path = Path(entry["path"])
            if model_path.exists():
                model_path.unlink()

        self._registry[name] = entries[-keep:]
        self._save_registry()
