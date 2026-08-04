from datetime import datetime, timezone

import pytest

from eq import parse


def test_normalise_longitude_leaves_positive_values_alone():
    assert parse.normalise_longitude(177.65) == pytest.approx(177.65)


def test_normalise_longitude_unwraps_antimeridian():
    assert parse.normalise_longitude(-179.5) == pytest.approx(180.5)


def test_parse_returns_one_record_per_row(sample_csv):
    records = parse.parse_catalogue_csv(sample_csv)
    assert len(records) == 2


def test_parse_types_and_values(sample_csv):
    first = parse.parse_catalogue_csv(sample_csv)[0]
    assert first["publicid"] == "2026p083320"
    assert first["origintime"] == datetime(2026, 1, 31, 19, 53, 16, 616000, tzinfo=timezone.utc)
    assert first["magnitude"] == pytest.approx(3.2135171596)
    assert first["depth"] == pytest.approx(35.0411077)
    assert first["depthtype"] == ""


def test_parse_preserves_depthtype_flag(sample_csv):
    second = parse.parse_catalogue_csv(sample_csv)[1]
    assert second["depthtype"] == "operator assigned"


def test_parse_applies_longitude_convention(sample_csv):
    second = parse.parse_catalogue_csv(sample_csv)[1]
    assert second["longitude"] == pytest.approx(180.5)


def test_parse_handles_empty_body():
    assert parse.parse_catalogue_csv("") == []


def test_parse_skips_rows_missing_required_fields():
    text = (
        "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth,"
        "magnitudetype,depthtype,evaluationstatus,evaluationmode\n"
        "a,2026-01-01T00:00:00.000Z,2026-01-01T00:00:00.000Z,175.0,-41.0,,10.0,MLv,,confirmed,manual\n"
    )
    assert parse.parse_catalogue_csv(text) == []


def test_parse_raises_on_malformed_required_value():
    text = (
        "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth,"
        "magnitudetype,depthtype,evaluationstatus,evaluationmode\n"
        "a,2026-01-01T00:00:00.000Z,2026-01-01T00:00:00.000Z,175.0,-41.0,abc,10.0,MLv,,confirmed,manual\n"
    )
    with pytest.raises(ValueError):
        parse.parse_catalogue_csv(text)


def test_parse_handles_missing_modificationtime():
    text = (
        "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth,"
        "magnitudetype,depthtype,evaluationstatus,evaluationmode\n"
        "a,2026-01-01T00:00:00.000Z,,175.0,-41.0,3.5,10.0,MLv,,confirmed,manual\n"
    )
    records = parse.parse_catalogue_csv(text)
    assert len(records) == 1
    assert records[0]["publicid"] == "a"
    assert records[0]["origintime"] == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert records[0]["modificationtime"] is None
    assert records[0]["magnitude"] == pytest.approx(3.5)
