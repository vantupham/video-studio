"""Provider instances: CRUD, secrets, defaults and resolution.

Security rules:
* Secret fields (``x-secret`` in the type's config schema) are written ONLY to the
  SecretStore under ``provider:<instance id>:<field>``. The ``provider_instances`` table
  stores the non-secret config plus the *names* of secret fields that are set.
* Public representations mask secrets as ``••••last4``; updates that send an omitted or
  masked value keep the stored secret.
* Secrets are never logged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from videostudio.db import ProviderInstance

from ..secretstore import SecretStore
from .base import CAPABILITIES, BaseProvider, ProviderContext, ResolvedProvider

log = logging.getLogger(__name__)

MASK = "••••"


class ProviderNotFound(KeyError):
    pass


class ProviderValidationError(ValueError):
    pass


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    return MASK + (value[-4:] if len(value) >= 8 else "")


def is_masked(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(MASK)


def secret_key(instance_id: str, field: str) -> str:
    return f"provider:{instance_id}:{field}"


def _coerce(value: Any, prop: dict[str, Any], field: str) -> Any:
    if value is None or value == "":
        return None
    kind = prop.get("type", "string")
    try:
        if kind == "integer":
            value = int(value)
        elif kind == "number":
            value = float(value)
        elif kind == "boolean":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "on")
            else:
                value = bool(value)
        elif kind == "string":
            value = str(value).strip()
        elif kind == "object" and not isinstance(value, dict):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ProviderValidationError(f"Giá trị không hợp lệ cho '{field}'") from exc
    if "enum" in prop and value not in prop["enum"]:
        options = ", ".join(map(str, prop["enum"]))
        raise ProviderValidationError(f"'{field}' phải là một trong: {options}")
    if kind in ("integer", "number"):
        if "minimum" in prop and value < prop["minimum"]:
            raise ProviderValidationError(f"'{field}' phải ≥ {prop['minimum']}")
        if "maximum" in prop and value > prop["maximum"]:
            raise ProviderValidationError(f"'{field}' phải ≤ {prop['maximum']}")
    return value


# Instances created automatically on first run so the app works without any key.
SEED_INSTANCES: list[dict[str, Any]] = [
    {"type": "mock_llm", "name": "Chế độ demo", "default": True},
    {"type": "edge_tts", "name": "Edge TTS miễn phí", "default": True},
    {"type": "mock_tts", "name": "Giọng giả lập (offline)", "default": False},
    {"type": "proportional", "name": "Ước lượng theo âm tiết", "default": True},
    {"type": "mock_media", "name": "Ảnh gradient (offline)", "default": True},
    {"type": "tesseract", "name": "Tesseract trên máy", "default": True},
    {"type": "mock_ocr", "name": "OCR giả lập (offline)", "default": False},
]


class ProviderService:
    def __init__(self, session_factory: sessionmaker[Session], providers: dict[str, BaseProvider],
                 secrets: SecretStore, models_dir: Path | None = None,
                 cache_dir: Path | None = None):
        self.session_factory = session_factory
        self.providers = providers
        self.secrets = secrets
        self.models_dir = models_dir
        self.cache_dir = cache_dir

    # ------------------------------------------------------------------ types

    def provider_type(self, type_id: str) -> BaseProvider:
        try:
            return self.providers[type_id]
        except KeyError as exc:
            raise ProviderNotFound(f"Không có loại nhà cung cấp '{type_id}'") from exc

    def types(self, capability: str | None = None) -> list[dict[str, Any]]:
        return [p.describe() for p in self.providers.values()
                if capability is None or p.capability == capability]

    # ------------------------------------------------------------------ helpers

    def _row(self, s: Session, instance_id: str) -> ProviderInstance:
        row = s.get(ProviderInstance, instance_id)
        if row is None:
            raise ProviderNotFound(f"Không tìm thấy nhà cung cấp {instance_id}")
        return row

    def _public(self, row: ProviderInstance) -> dict[str, Any]:
        provider = self.providers.get(row.type)
        secrets = {}
        for field in provider.secret_fields() if provider else row.secret_fields:
            secrets[field] = mask_secret(self.secrets.get(secret_key(row.id, field)))
        return {
            "id": row.id,
            "capability": row.capability,
            "type": row.type,
            "type_name": provider.name if provider else row.type,
            "available": provider is not None,
            "name": row.name,
            "config": dict(row.config or {}),
            "secrets": secrets,
            "is_default": row.is_default,
            "is_mock": bool(provider and provider.is_mock),
            "unofficial": bool(provider and provider.unofficial),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _split_config(self, provider: BaseProvider, config: dict[str, Any],
                      partial: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        schema = provider.config_schema()
        props: dict[str, Any] = schema.get("properties", {})
        plain: dict[str, Any] = {}
        secret_updates: dict[str, Any] = {}
        for key, value in config.items():
            prop = props.get(key)
            if prop is None:
                continue  # unknown keys are dropped
            if prop.get("x-secret"):
                if value is None or is_masked(value):
                    continue  # keep stored secret
                secret_updates[key] = str(value).strip()  # "" clears the secret
            else:
                coerced = _coerce(value, prop, key)
                if coerced is not None:
                    plain[key] = coerced
        return plain, secret_updates

    def _check_required(self, provider: BaseProvider, instance_id: str | None,
                        plain: dict[str, Any], secret_updates: dict[str, Any]) -> None:
        schema = provider.config_schema()
        props = schema.get("properties", {})
        missing = []
        for key in schema.get("required", []):
            prop = props.get(key, {})
            if prop.get("x-secret"):
                if key in secret_updates:
                    present = bool(secret_updates[key])
                else:
                    present = bool(instance_id and self.secrets.get(secret_key(instance_id, key)))
            else:
                present = plain.get(key) not in (None, "") or "default" in prop
            if not present:
                missing.append(prop.get("title", key))
        if missing:
            raise ProviderValidationError("Thiếu trường bắt buộc: " + ", ".join(missing))

    def _apply_secrets(self, instance_id: str, secret_updates: dict[str, Any]) -> None:
        for key, value in secret_updates.items():
            if value:
                self.secrets.set(secret_key(instance_id, key), value)
            else:
                self.secrets.delete(secret_key(instance_id, key))

    def _set_secret_names(self, row: ProviderInstance, provider: BaseProvider) -> None:
        row.secret_fields = [f for f in provider.secret_fields()
                             if self.secrets.get(secret_key(row.id, f))]

    # ------------------------------------------------------------------ CRUD

    def list(self, capability: str | None = None) -> list[dict[str, Any]]:
        with self.session_factory() as s:
            stmt = select(ProviderInstance).order_by(ProviderInstance.capability,
                                                     ProviderInstance.created_at)
            if capability:
                stmt = stmt.where(ProviderInstance.capability == capability)
            return [self._public(r) for r in s.scalars(stmt)]

    def get(self, instance_id: str) -> dict[str, Any]:
        with self.session_factory() as s:
            return self._public(self._row(s, instance_id))

    def create(self, type_id: str, name: str | None, config: dict[str, Any],
               make_default: bool = False) -> dict[str, Any]:
        provider = self.provider_type(type_id)
        plain, secret_updates = self._split_config(provider, config or {}, partial=False)
        self._check_required(provider, None, plain, secret_updates)
        with self.session_factory.begin() as s:
            row = ProviderInstance(capability=provider.capability, type=type_id,
                                   name=(name or provider.name).strip()[:255], config=plain)
            s.add(row)
            s.flush()
            self._apply_secrets(row.id, secret_updates)
            self._set_secret_names(row, provider)
            has_default = s.scalar(select(ProviderInstance.id).where(
                ProviderInstance.capability == provider.capability,
                ProviderInstance.is_default.is_(True), ProviderInstance.id != row.id))
            if make_default or not has_default:
                self._make_default(s, row)
            s.flush()
            return self._public(row)

    def update(self, instance_id: str, name: str | None = None,
               config: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.session_factory.begin() as s:
            row = self._row(s, instance_id)
            provider = self.provider_type(row.type)
            if name is not None and name.strip():
                row.name = name.strip()[:255]
            if config is not None:
                plain, secret_updates = self._split_config(provider, config, partial=True)
                self._check_required(provider, row.id, plain, secret_updates)
                row.config = plain
                self._apply_secrets(row.id, secret_updates)
                self._set_secret_names(row, provider)
            s.flush()
            return self._public(row)

    def delete(self, instance_id: str) -> None:
        with self.session_factory.begin() as s:
            row = self._row(s, instance_id)
            was_default, capability = row.is_default, row.capability
            provider = self.providers.get(row.type)
            for field in set(row.secret_fields or []) | set(
                    provider.secret_fields() if provider else []):
                self.secrets.delete(secret_key(row.id, field))
            s.delete(row)
            s.flush()
            if was_default:
                nxt = s.scalars(select(ProviderInstance).where(
                    ProviderInstance.capability == capability).order_by(
                    ProviderInstance.created_at)).first()
                if nxt is not None:
                    nxt.is_default = True

    def _make_default(self, s: Session, row: ProviderInstance) -> None:
        for other in s.scalars(select(ProviderInstance).where(
                ProviderInstance.capability == row.capability)):
            other.is_default = other.id == row.id

    def set_default(self, instance_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as s:
            row = self._row(s, instance_id)
            self._make_default(s, row)
            s.flush()
            return self._public(row)

    def defaults(self) -> dict[str, str | None]:
        with self.session_factory() as s:
            out: dict[str, str | None] = {c: None for c in CAPABILITIES}
            for row in s.scalars(select(ProviderInstance).where(
                    ProviderInstance.is_default.is_(True))):
                out[row.capability] = row.id
            return out

    # ------------------------------------------------------------------ resolution

    def context(self, instance_id: str) -> tuple[BaseProvider, ProviderContext, str]:
        with self.session_factory() as s:
            row = self._row(s, instance_id)
            provider = self.provider_type(row.type)
            schema_props = provider.config_schema().get("properties", {})
            config = {k: v.get("default") for k, v in schema_props.items()
                      if "default" in v and not v.get("x-secret")}
            config.update(row.config or {})
            for field in provider.secret_fields():
                value = self.secrets.get(secret_key(row.id, field))
                if value:
                    config[field] = value
            ctx = ProviderContext(config=config, instance_id=row.id, models_dir=self.models_dir,
                                  cache_dir=self.cache_dir)
            return provider, ctx, row.name

    def ephemeral_context(self, type_id: str, config: dict[str, Any],
                          instance_id: str | None = None) -> tuple[BaseProvider, ProviderContext]:
        """Context for testing an unsaved form; masked secrets fall back to stored ones."""
        provider = self.provider_type(type_id)
        props = provider.config_schema().get("properties", {})
        merged = {k: v.get("default") for k, v in props.items()
                  if "default" in v and not v.get("x-secret")}
        plain, secret_updates = self._split_config(provider, config, partial=True)
        merged.update(plain)
        for field in provider.secret_fields():
            if secret_updates.get(field):
                merged[field] = secret_updates[field]
            elif instance_id:
                stored = self.secrets.get(secret_key(instance_id, field))
                if stored:
                    merged[field] = stored
        return provider, ProviderContext(config=merged, instance_id=instance_id,
                                         models_dir=self.models_dir, cache_dir=self.cache_dir)

    def resolve(self, capability: str, *choices: dict[str, Any] | None) -> ResolvedProvider:
        """Pick the instance for ``capability``: the first choice (e.g. the generation
        dialog) wins over later ones (project settings), then the global default.
        ``model``/``voice`` are taken from the first choice that sets them for the same
        instance, then from the instance config."""
        picked: str | None = None
        model = voice = None
        for choice in choices:
            if choice and choice.get("instance_id"):
                picked = choice["instance_id"]
                break
        if picked is None:
            picked = self.defaults().get(capability)
        if picked is None:
            raise ProviderNotFound(f"Chưa cấu hình nhà cung cấp cho {capability}")
        for choice in choices:
            if not choice or choice.get("instance_id") not in (None, picked):
                continue
            model = model or choice.get("model")
            voice = voice or choice.get("voice")
        provider, ctx, name = self.context(picked)
        if provider.capability != capability:
            raise ProviderValidationError(
                f"Nhà cung cấp '{name}' không phải loại {capability}")
        model = model or ctx.get("model")
        voice = voice or ctx.get("default_voice")
        return ResolvedProvider(provider=provider, ctx=ctx, instance_id=picked,
                                instance_name=name, model=model, voice=voice)

    # ------------------------------------------------------------------ seeding

    def seed_defaults(self) -> None:
        """First run: create every seed instance. Later runs: seed capabilities that have
        no instance yet (e.g. OCR added by an update), leaving user choices alone."""
        with self.session_factory() as s:
            existing = set(s.scalars(select(ProviderInstance.capability).distinct()))
        seeded = 0
        for spec in SEED_INSTANCES:
            provider = self.providers.get(spec["type"])
            if provider is None or provider.capability in existing:
                continue
            self.create(spec["type"], spec["name"], {}, make_default=spec["default"])
            seeded += 1
        if seeded:
            log.info("Seeded %d default provider instance(s)", seeded)
