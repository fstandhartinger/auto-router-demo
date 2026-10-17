"""The wide model catalog the IRP endpoint answers for (the playground catalog is untouched).

``irp-catalog.json`` lists the 11 playground models plus every model that AntSeed sellers list and Benchmark
Heaven has capability evidence for. Each non-core entry carries the minimal Benchmark Heaven document its
ModelInfo is built from, so the endpoint needs no benchmark call at request time. A builder publishes the
file to the ``irp-catalog`` branch; this module loads the baked copy at start, then refreshes from
``IRP_CATALOG_URL`` every hour. Any download, validation or build failure keeps the last good catalog.

Prices stored in the file are only needed to construct a ModelInfo. IRP rankings use the caller's prices.

File format (schema 1)::

    {"schema": 1, "generated_at": "2026-10-17T12:00:00Z",
     "models": [{"id": "vendor/slug",             # primary IRP id, unique across ids and aliases
                 "core": true,                     # playground model: resolved from the engine, aliases only
                 "irp_aliases": ["other-spelling"],
                 "bench_id": "family::variant",    # Benchmark Heaven id
                 "label": "Display name",
                 "prices": {"input": 1.0, "output": 2.0},   # per 1M tokens, ModelInfo construction only
                 "context_tokens": 128000,
                 "document": {"model": {...}}}]}   # minimal Benchmark Heaven document (not for core entries)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from auto_router.bench import BenchmarkClient, Provenance
from auto_router.catalog import Catalog
from auto_router.config import RouterConfig, build_model
from auto_router.irp import model_id

from .settings import DATA

log = logging.getLogger("demo.irp_catalog")

SCHEMA = 1
MAX_BYTES = 5_000_000
MIN_MODELS = 11
REFRESH_SECONDS = 3600.0
#: Past this age the benchmark documents inside the file count as stale (capabilities are discounted).
STALE_AFTER_SECONDS = 7 * 86400.0
DEFAULT_URL = "https://raw.githubusercontent.com/fstandhartinger/auto-router-demo/irp-catalog/irp-catalog.json"
BAKED = DATA / "irp-catalog.json"


class CatalogError(ValueError):
    """The candidate catalog is unusable; the previous one stays in force."""


def _timestamp(value) -> float:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        raise CatalogError("generated_at must be a UTC timestamp like 2026-10-17T12:00:00Z")


def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate(data) -> dict:
    """Structural check of a parsed catalog. Returns it unchanged or raises CatalogError."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise CatalogError(f"schema must be {SCHEMA}")
    _timestamp(data.get("generated_at"))
    models = data.get("models")
    if not isinstance(models, list) or len(models) < MIN_MODELS:
        raise CatalogError(f"models must be a list of at least {MIN_MODELS}")
    seen: set[str] = set()
    for i, entry in enumerate(models):
        where = f"models[{i}]"
        if not isinstance(entry, dict):
            raise CatalogError(f"{where} must be an object")
        aliases = entry.get("irp_aliases", [])
        if not isinstance(aliases, list):
            raise CatalogError(f"{where}.irp_aliases must be a list")
        ids = [entry.get("id"), *aliases]
        for one in ids:
            if not isinstance(one, str) or not 1 <= len(one) <= 128:
                raise CatalogError(f"{where}: ids and aliases must be strings of 1-128 characters")
            if one in seen:
                raise CatalogError(f"{where}: id {one!r} is not unique")
            seen.add(one)
        if not isinstance(entry.get("bench_id"), (str, type(None))):
            raise CatalogError(f"{where}.bench_id must be a string")
        if entry.get("core") is True:
            continue
        prices = entry.get("prices")
        if not isinstance(prices, dict) or not _finite(prices.get("input")) or not _finite(prices.get("output")):
            raise CatalogError(f"{where}.prices needs finite nonnegative input and output")
        doc = entry.get("document")
        if not isinstance(entry.get("bench_id"), str) or not isinstance(doc, dict) \
                or not isinstance(doc.get("model"), dict):
            raise CatalogError(f"{where} needs bench_id and a benchmark document")
        if "context_tokens" in entry and (type(entry["context_tokens"]) is not int or entry["context_tokens"] < 1):
            raise CatalogError(f"{where}.context_tokens must be a positive integer")
    return data


def parse(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise CatalogError("catalog exceeds 5 MB")
    try:
        return validate(json.loads(raw))
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError("catalog is not valid JSON")


class _DocBench(BenchmarkClient):
    """A benchmark client that answers only from the documents shipped inside the catalog."""

    def __init__(self, documents: dict[str, dict], fetched_at: float):
        super().__init__(offline=True, snapshot=False)
        self.documents = documents
        self.fetched_at = fetched_at

    def fetch(self, path: str):
        prefix = "/api/models/"
        doc = self.documents.get(urllib.parse.unquote(path[len(prefix):])) if path.startswith(prefix) else None
        if doc is None:
            return None, Provenance("irp-catalog", path, "missing")
        age = max(0.0, time.time() - self.fetched_at)
        source = "cache" if age < STALE_AFTER_SECONDS else "stale-cache"
        return doc, Provenance("irp-catalog", path, source, age, self.fetched_at)


def _name(entry_id: str) -> str:
    return "irp:" + re.sub(r"[^a-z0-9.]+", "-", entry_id.lower()).strip("-")


def build_view(base: RouterConfig, catalog: dict) -> RouterConfig:
    """The playground models plus the catalog's models, as one router configuration.

    Playground ("core") models keep their engine-built ModelInfo and gain the catalog's aliases. A model
    whose benchmark document yields no capability evidence is left out rather than guessed.
    """
    fetched_at = _timestamp(catalog["generated_at"])
    raw = [dict(e) for e in base.raw.get("models", [])]
    models = list(base.catalog.all())
    by_id = {}
    for model in models:
        entry = next((e for e in raw if e.get("name") == model.name), {})
        by_id[model_id(model, entry)] = entry
    documents = {e["bench_id"]: e["document"] for e in catalog["models"] if e.get("document")}
    bench = _DocBench(documents, fetched_at)
    for entry in catalog["models"]:
        if entry.get("core"):
            target = by_id.get(entry["id"])
            if target is not None:
                target["irp_aliases"] = list(entry.get("irp_aliases") or [])
            continue
        config_entry = {"name": _name(entry["id"]), "upstream_id": entry["id"], "irp_model_id": entry["id"],
                        "irp_aliases": list(entry.get("irp_aliases") or []), "bench_id": entry["bench_id"],
                        "prices": dict(entry["prices"])}
        if entry.get("context_tokens"):
            config_entry["context_tokens"] = entry["context_tokens"]
        try:
            model = build_model(config_entry, {}, bench)
        except ValueError:
            continue
        if not model.capability:
            continue
        models.append(model)
        raw.append(config_entry)
    return RouterConfig(providers=base.providers, catalog=Catalog(models), subscriptions={},
                        policy=base.policy, raw={"models": raw})


@dataclass(frozen=True)
class Loaded:
    data: dict
    generated_at: str
    source: str


class IrpCatalog:
    """Holds the catalog in force. Replacing it is one reference assignment, so readers never see half of one."""

    def __init__(self, url: str | None = None, baked: Path = BAKED):
        self.url = DEFAULT_URL if url is None else url.strip()
        self.baked = baked
        self.loaded: Loaded | None = None
        self.last_error = ""
        self.last_refresh = 0.0
        self._view: tuple[Loaded, RouterConfig, RouterConfig] | None = None

    # -- loading -----------------------------------------------------------
    def load_baked(self) -> None:
        try:
            data = parse(self.baked.read_bytes())
        except (OSError, CatalogError) as exc:
            self.last_error = f"baked catalog unusable: {exc}"
            log.error(self.last_error)
            return
        self.loaded = Loaded(data, data["generated_at"], "baked")

    def _download(self) -> bytes:
        req = urllib.request.Request(self.url, headers={"User-Agent": "auto-router-demo/irp-catalog"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(MAX_BYTES + 1)
        return raw

    def refresh_once(self, base: RouterConfig | None = None) -> bool:
        """Fetch, validate and (if it builds) swap in the published catalog. True when it changed."""
        if not self.url:
            return False
        self.last_refresh = time.time()
        try:
            data = parse(self._download())
            if base is not None:
                build_view(base, data)  # a catalog that cannot be built must not replace a working one
        except Exception as exc:  # noqa: BLE001 - any failure keeps the last good catalog
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("irp catalog refresh failed, keeping the last good one: %s", self.last_error)
            return False
        self.last_error = ""
        if self.loaded and self.loaded.data == data:
            return False
        self.loaded = Loaded(data, data["generated_at"], "remote")
        return True

    async def refresh_loop(self, get_base, interval: float = REFRESH_SECONDS) -> None:
        while True:
            await asyncio.to_thread(self.refresh_once, get_base())
            await asyncio.sleep(interval)

    # -- views -------------------------------------------------------------
    def view(self, base: RouterConfig) -> RouterConfig:
        """The router configuration for the catalog now in force, rebuilt only when it or the engine changed."""
        loaded = self.loaded
        if loaded is None:
            return base
        cached = self._view
        if cached is not None and cached[0] is loaded and cached[1] is base:
            return cached[2]
        view = build_view(base, loaded.data)
        self._view = (loaded, base, view)
        return view

    def info(self, view: RouterConfig | None = None) -> dict:
        loaded = self.loaded
        return {"catalog_generated_at": loaded.generated_at if loaded else None,
                "catalog_models": len(view.catalog.all()) if view is not None else 0,
                "catalog_source": loaded.source if loaded else "none"}


def from_env() -> IrpCatalog:
    """``IRP_CATALOG_URL`` unset means the published default; set to an empty string it disables refreshing."""
    baked = os.environ.get("IRP_CATALOG_FILE")  # tests point this at a hermetic file
    catalog = IrpCatalog(os.environ.get("IRP_CATALOG_URL"), Path(baked) if baked else BAKED)
    catalog.load_baked()
    return catalog


CATALOG = from_env()
