from __future__ import annotations

from importlib import resources

from flowsage_graph.ingest import load_events


def test_bundled_sample_data_is_complete() -> None:
    with resources.as_file(
        resources.files("flowsage_backend.resources.sample_data")
    ) as sample_dir:
        events = load_events(sample_dir / "events.jsonl")
        screenshots = sorted(p.name for p in (sample_dir / "screenshots").glob("*.png"))

    assert len(events) == 44
    assert screenshots == ["01_cart.png", "02_shipping.png", "03_confirm.png"]
