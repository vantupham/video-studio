"""Plugin discovery.

Plugins come from three places, in order:
1. Built-in plugins shipped with the backend.
2. Python entry points in the ``videostudio.plugins`` group.
3. ``<data_dir>/plugins``: either ``<name>.py`` or ``<name>/plugin.py``.

Every plugin module exposes ``register(registry: PluginRegistry) -> None``.
A broken plugin is logged and skipped; it never prevents startup.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from videostudio.providers.base import BaseProvider

from .base import InputAdapter, Publisher, Renderer, SourceRef

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "videostudio.plugins"


@dataclass
class PluginLoadError:
    origin: str
    error: str


@dataclass
class PluginRegistry:
    input_adapters: dict[str, InputAdapter] = field(default_factory=dict)
    # Provider *types* of every capability (llm, tts, alignment, media), keyed by type id.
    providers: dict[str, BaseProvider] = field(default_factory=dict)
    renderers: dict[str, Renderer] = field(default_factory=dict)
    publishers: dict[str, Publisher] = field(default_factory=dict)
    origins: dict[str, str] = field(default_factory=dict)
    errors: list[PluginLoadError] = field(default_factory=list)
    _current_origin: str = "builtin"

    def _add(self, bucket: dict[str, Any], proto: type, plugin: Any, kind: str) -> None:
        if not isinstance(plugin, proto) or not getattr(plugin, "id", None):
            raise TypeError(f"{plugin!r} does not implement {proto.__name__}")
        if plugin.id in bucket:
            log.warning("%s %r from %s overrides an existing one", kind, plugin.id,
                        self._current_origin)
        bucket[plugin.id] = plugin
        self.origins[f"{kind}:{plugin.id}"] = self._current_origin

    def add_input_adapter(self, adapter: InputAdapter) -> None:
        self._add(self.input_adapters, InputAdapter, adapter, "input_adapter")

    def add_provider(self, provider: BaseProvider) -> None:
        """Register a provider type (subclass of ``LLMProvider``, ``TTSProvider``,
        ``AlignmentProvider`` or ``MediaProvider``)."""
        self._add(self.providers, BaseProvider, provider, "provider")

    # Kept for plugin code written against the phase-0 API.
    add_llm_provider = add_provider
    add_tts_provider = add_provider

    def providers_for(self, capability: str) -> list[BaseProvider]:
        return [p for p in self.providers.values() if p.capability == capability]

    def add_renderer(self, renderer: Renderer) -> None:
        self._add(self.renderers, Renderer, renderer, "renderer")

    def add_publisher(self, publisher: Publisher) -> None:
        self._add(self.publishers, Publisher, publisher, "publisher")

    def get_input_adapter(self, adapter_id: str) -> InputAdapter:
        try:
            return self.input_adapters[adapter_id]
        except KeyError as exc:
            raise KeyError(f"Unknown input adapter: {adapter_id}") from exc

    def find_input_adapter(self, source: SourceRef) -> InputAdapter | None:
        for adapter in self.input_adapters.values():
            try:
                if adapter.can_handle(source):
                    return adapter
            except Exception:  # noqa: BLE001 - a plugin bug must not break detection
                log.exception("can_handle failed for %s", adapter.id)
        return None

    def _run_register(self, origin: str, register: Callable[[PluginRegistry], None]) -> None:
        self._current_origin = origin
        try:
            register(self)
        except Exception as exc:  # noqa: BLE001
            log.exception("Plugin %s failed to register", origin)
            self.errors.append(PluginLoadError(origin=origin, error=repr(exc)))
        finally:
            self._current_origin = "builtin"

    def load_entry_points(self) -> None:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            origin = f"entry_point:{ep.name}"
            try:
                register = ep.load()
            except Exception as exc:  # noqa: BLE001
                log.exception("Cannot load entry point %s", ep.name)
                self.errors.append(PluginLoadError(origin=origin, error=repr(exc)))
                continue
            self._run_register(origin, register)

    def load_directory(self, plugins_dir: Path) -> None:
        if not plugins_dir.is_dir():
            return
        for entry in sorted(plugins_dir.iterdir()):
            if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                module_file, name = entry, entry.stem
            elif entry.is_dir() and (entry / "plugin.py").is_file():
                module_file, name = entry / "plugin.py", entry.name
            else:
                continue
            origin = f"dir:{entry.name}"
            module_name = f"videostudio_userplugin_{name}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, module_file)
                if spec is None or spec.loader is None:
                    raise ImportError(f"cannot load {module_file}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                register = module.register
            except Exception as exc:  # noqa: BLE001
                log.exception("Cannot load plugin %s", module_file)
                self.errors.append(PluginLoadError(origin=origin, error=repr(exc)))
                continue
            self._run_register(origin, register)


def register_builtins(registry: PluginRegistry) -> None:
    from .adapters.markdown import MarkdownAdapter
    from .adapters.script import ScriptAdapter

    registry.add_input_adapter(ScriptAdapter())
    registry.add_input_adapter(MarkdownAdapter())
    from videostudio.pipeline.renderer_ffmpeg import FFmpegRenderer
    from videostudio.providers import builtin_providers

    for provider in builtin_providers():
        registry.add_provider(provider)
    registry.add_renderer(FFmpegRenderer())


def build_registry(plugins_dir: Path | None = None) -> PluginRegistry:
    registry = PluginRegistry()
    register_builtins(registry)
    registry.load_entry_points()
    if plugins_dir is not None:
        registry.load_directory(plugins_dir)
    return registry
