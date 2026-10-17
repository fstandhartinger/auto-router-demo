"""The wide IRP catalog: validation, atomic swap, last-good fallback, view building."""
import copy
import json
import time
from pathlib import Path

import pytest

from app import irp_catalog as ic

FIXTURE = json.loads((Path(__file__).parent / "irp-catalog.test.json").read_text())


def raw(data):
    return json.dumps(data).encode()


def test_fixture_is_valid():
    assert ic.validate(copy.deepcopy(FIXTURE))["schema"] == 1


@pytest.mark.parametrize("mutate,why", [
    (lambda d: d.update(schema=2), "schema"),
    (lambda d: d.update(generated_at="yesterday"), "generated_at"),
    (lambda d: d.update(models=d["models"][:5]), "at least 11"),
    (lambda d: d["models"][1].update(id=d["models"][2]["id"]), "unique"),
    (lambda d: d["models"][1].update(irp_aliases=[d["models"][0]["id"]]), "unique"),
    (lambda d: d["models"][1].update(prices={"input": -1, "output": 1}), "prices"),
    (lambda d: d["models"][1].update(prices={"input": float("nan"), "output": 1}), "prices"),
    (lambda d: d["models"][1].pop("document"), "document"),
    (lambda d: d["models"][1].update(id=""), "1-128"),
    (lambda d: d["models"][1].update(irp_aliases="oops"), "aliases"),
])
def test_invalid_catalogs_are_rejected(mutate, why):
    data = copy.deepcopy(FIXTURE)
    mutate(data)
    with pytest.raises(ic.CatalogError):
        ic.parse(json.dumps(data, allow_nan=True).encode())


def test_size_and_garbage_are_rejected():
    with pytest.raises(ic.CatalogError):
        ic.parse(b" " * (ic.MAX_BYTES + 1))
    with pytest.raises(ic.CatalogError):
        ic.parse(b"{not json")
    with pytest.raises(ic.CatalogError):
        ic.parse(b"[]")


def test_view_adds_wide_models_and_aliases(client):
    from app.engine import ENGINE
    view = ic.build_view(ENGINE.config, FIXTURE)
    names = {m.name for m in view.catalog.all()}
    assert len(names) == len(ENGINE.config.catalog.all()) + 11
    from auto_router.irp import model_index
    index = model_index(view)
    assert index["irptest/model-0"][0] is index["e2ee-irptest-model-0"][0]
    assert index["system1models.ai/tiny-free"][0] is index["tiny-alias"][0]
    assert all(m.capability for m in view.catalog.all() if m.name.startswith("irp:"))
    # the engine's own config is untouched
    assert "irp_aliases" not in next(e for e in ENGINE.config.raw["models"] if e["name"] == "tiny-free")


def test_model_without_capability_evidence_is_left_out(client):
    from app.engine import ENGINE
    data = copy.deepcopy(FIXTURE)
    data["models"][1]["document"]["model"]["benchmarks"] = {}
    view = ic.build_view(ENGINE.config, data)
    assert len(view.catalog.all()) == len(ENGINE.config.catalog.all()) + 10


def make(tmp_path, payload=FIXTURE):
    baked = tmp_path / "baked.json"
    baked.write_text(json.dumps(payload))
    catalog = ic.IrpCatalog("https://example.invalid/irp-catalog.json", baked)
    catalog.load_baked()
    return catalog


def newer():
    data = copy.deepcopy(FIXTURE)
    data["generated_at"] = "2026-10-18T00:00:00Z"
    data["models"].append({**copy.deepcopy(data["models"][1]), "id": "irptest/extra", "irp_aliases": []})
    return data


def test_refresh_swaps_in_a_valid_newer_catalog(tmp_path, monkeypatch, client):
    from app.engine import ENGINE
    catalog = make(tmp_path)
    assert catalog.loaded.source == "baked"
    data = newer()
    monkeypatch.setattr(catalog, "_download", lambda: raw(data))
    assert catalog.refresh_once(ENGINE.config) is True
    assert catalog.loaded.source == "remote" and catalog.loaded.generated_at == "2026-10-18T00:00:00Z"
    assert catalog.refresh_once(ENGINE.config) is False  # unchanged content is not a swap


@pytest.mark.parametrize("payload", [b"{broken", raw({"schema": 1}), b"x" * (ic.MAX_BYTES + 5), None])
def test_refresh_failure_keeps_the_last_good_catalog(tmp_path, monkeypatch, client, payload):
    from app.engine import ENGINE
    catalog = make(tmp_path)
    before = catalog.loaded
    def download():
        if payload is None:
            raise OSError("network down")
        return payload
    monkeypatch.setattr(catalog, "_download", download)
    assert catalog.refresh_once(ENGINE.config) is False
    assert catalog.loaded is before and catalog.last_error


def test_a_catalog_that_validates_but_cannot_build_is_not_swapped(tmp_path, monkeypatch, client):
    from app.engine import ENGINE
    catalog = make(tmp_path)
    before = catalog.loaded
    monkeypatch.setattr(catalog, "_download", lambda: raw(newer()))
    def boom(*a):
        raise RuntimeError("cannot build")
    monkeypatch.setattr(ic, "build_view", boom)
    assert catalog.refresh_once(ENGINE.config) is False and catalog.loaded is before


def test_unusable_baked_file_leaves_the_engine_view_alone(tmp_path, client):
    from app.engine import ENGINE
    catalog = ic.IrpCatalog("", tmp_path / "missing.json")
    catalog.load_baked()
    assert catalog.loaded is None and "baked catalog unusable" in catalog.last_error
    assert catalog.view(ENGINE.config) is ENGINE.config
    assert catalog.info()["catalog_generated_at"] is None
    assert catalog.refresh_once() is False  # empty URL disables refreshing


def test_view_is_cached_until_the_catalog_changes(tmp_path, monkeypatch, client):
    from app.engine import ENGINE
    catalog = make(tmp_path)
    first = catalog.view(ENGINE.config)
    assert catalog.view(ENGINE.config) is first
    monkeypatch.setattr(catalog, "_download", lambda: raw(newer()))
    catalog.refresh_once(ENGINE.config)
    assert catalog.view(ENGINE.config) is not first


def test_staleness_follows_the_catalog_age(client):
    from app.engine import ENGINE
    old = copy.deepcopy(FIXTURE)
    old["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30 * 86400))
    fresh = copy.deepcopy(FIXTURE)
    fresh["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stale = [m for m in ic.build_view(ENGINE.config, old).catalog.all() if m.name.startswith("irp:")]
    current = [m for m in ic.build_view(ENGINE.config, fresh).catalog.all() if m.name.startswith("irp:")]
    assert all(m.evidence_stale for m in stale) and not any(m.evidence_stale for m in current)
