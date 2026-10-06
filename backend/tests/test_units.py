import pytest

from backend.app.units import BCF_PER_BCM, UnknownUnit, convert, normalize


def approx(a, b):
    return a == pytest.approx(b, rel=1e-9)


def test_oil_rates_to_kbd():
    assert normalize(1_500_000, "bbl/d") == (1500.0, "kb/d")
    assert normalize(9.8, "mb/d") == (9800.0, "kb/d")          # market "mb/d" = MILLION
    assert normalize(10100, "KBD") == (10100, "kb/d")           # JODI
    assert normalize(13400, "TBPD") == (13400, "kb/d")          # EIA international
    assert normalize(13400, "MBBL/D") == (13400, "kb/d")        # EIA weekly: M = THOUSAND


def test_eia_vs_market_mb_ambiguity():
    # The same digits mean different things in EIA vs market convention.
    assert normalize(1.0, "MBBL/D")[0] == 1.0
    assert normalize(1.0, "mb/d")[0] == 1000.0


def test_oil_stocks_to_million_bbl():
    v, u = normalize(415_000, "MBBL")      # EIA thousand barrels
    assert u == "mb" and approx(v, 415.0)
    v, u = normalize(415_000_000, "bbl")
    assert u == "mb" and approx(v, 415.0)


def test_gas_volumes_to_bcf():
    v, u = normalize(1, "bcm")
    assert u == "bcf" and approx(v, BCF_PER_BCM)
    v, _ = normalize(1000, "mcm")
    assert approx(v, BCF_PER_BCM)
    v, _ = normalize(3500, "MMCF")
    assert approx(v, 3.5)
    assert normalize(3.2, "tcf")[0] == pytest.approx(3200)


def test_convert_roundtrip():
    assert approx(convert(1.0, "mb/d", "bbl/d"), 1_000_000)
    assert approx(convert(convert(42.0, "bcm", "mcm"), "mcm", "bcm"), 42.0)


def test_cross_dimension_rejected():
    with pytest.raises(UnknownUnit):
        convert(1.0, "bcm", "kb/d")


def test_unknown_unit_never_guessed():
    with pytest.raises(UnknownUnit):
        normalize(5.0, "barrels-ish")


def test_passthrough_units():
    assert normalize(72.5, "USD/bbl") == (72.5, "USD/bbl")
    assert normalize(4.1, "%") == (4.1, "%")
