import hashlib
import gzip
import json
from pathlib import Path

import pytest

from src.ml.dataset import read_episodes
from src.ml.factory_dataset import generate_factory, route_catalog
from src.ml.movement import MovementModel
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "data/demo/site.json"


def test_factory_splits_cover_actual_map_and_registered_sources(tmp_path):
    manifest = generate_factory(tmp_path, repeats=(1, 1, 1))
    site = json.loads(SITE.read_text(encoding="utf-8"))
    episodes = read_episodes(tmp_path)
    assert manifest["site_sha256"] == hashlib.sha256(SITE.read_bytes()).hexdigest()
    assert manifest["dataset_sha256"] == hashlib.sha256((tmp_path / "episodes.jsonl").read_bytes()).hexdigest()
    ids = [{episode["episode_id"] for episode in episodes if episode["split"] == split}
           for split in ("train", "validation", "test")]
    assert not ids[0] & ids[1] and not ids[0] & ids[2] and not ids[1] & ids[2]
    expected_roads = {road["id"] for road in site["roads"]}
    expected_buildings = {building["id"] for building in site["buildings"]}
    expected_assets = {asset["id"] for asset in site["assets"] if asset["type"] == "vehicle"}
    for split in ("train", "validation", "test"):
        normals = [episode for episode in episodes if episode["split"] == split and not episode["label"]]
        assert {road for episode in normals for road in episode["road_ids"]} == expected_roads
        assert {building for episode in normals for building in episode["building_ids"]} == expected_buildings
        assert {episode["asset_id"] for episode in normals} == expected_assets
        assert {episode["scenario"] for episode in normals} == {"transit", "loading", "waiting", "turns"}
    for episode in episodes:
        for event in episode["events"]:
            sensor = next(sensor for sensor in site["sensors"] if sensor["id"] == event["sensor_id"])
            assert sensor["asset_id"] == event["payload"]["asset_id"] == episode["asset_id"]
            assert all(0 <= event["payload"][axis] <= 100 for axis in ("x", "y"))
    assert manifest["coverage"]["zone_ids"] == [zone["id"] for zone in site["zones"]]


def test_factory_generation_is_byte_reproducible_and_rejects_shared_seed(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    assert generate_factory(first, repeats=(1, 1, 1)) == generate_factory(second, repeats=(1, 1, 1))
    assert (first / "episodes.jsonl").read_bytes() == (second / "episodes.jsonl").read_bytes()
    with pytest.raises(ValueError):
        generate_factory(tmp_path / "invalid", test_seed=20261018)


def test_catalog_follows_new_map_objects_without_hardcoded_coordinates():
    site = json.loads(SITE.read_text(encoding="utf-8"))
    site["roads"].append({"id": "new-access", "points": [[90, 98], [95, 98]]})
    site["buildings"].append({"id": "NEW", "entrance": {"road_id": "new-access"}})
    added = route_catalog(site)["road:new-access"]
    assert added["building_ids"] == ["NEW"]
    assert added["points"] == [[90, 98], [95, 98], [90, 98]]


def test_packaged_model_matches_map_and_refuses_unchecked_new_layout():
    site = json.loads(SITE.read_text(encoding="utf-8"))
    model = MovementModel(SimpleNamespace(site=site))
    assert model.state == "ready"
    assert model.metadata["dataset_generator"] == "factory-map-v7"
    site["roads"].append({"id": "future-road", "points": [[90, 98], [95, 98]]})
    changed = MovementModel(SimpleNamespace(site=site))
    assert changed.state == "artifact_invalid"
    assert changed.pipeline is None
    assert "Карта" in changed.error


def test_published_dataset_matches_training_hash_and_split_metadata():
    dataset = gzip.decompress((ROOT / "datasets/factory-v6/episodes.jsonl.gz").read_bytes())
    manifest = json.loads((ROOT / "datasets/factory-v6/manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((ROOT / "models/movement-v1/movement.metadata.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(dataset).hexdigest() == manifest["dataset_sha256"] == metadata["dataset_sha256"]
    episodes = [json.loads(line) for line in dataset.splitlines()]
    assert {split: [episode["episode_id"] for episode in episodes if episode["split"] == split]
            for split in ("train", "validation", "test")} == metadata["split_episode_ids"]
    assert metadata["training"]["scaler_fit_split"] == "train"
    assert metadata["training"]["threshold_fit_split"] == "validation"
    assert metadata["training"]["test_used_for_training"] is False
