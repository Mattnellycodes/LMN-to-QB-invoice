"""Tests for the temporary hardcoded Excel price-list lookup."""

from __future__ import annotations

from src.pricing.hardcoded_price_list import load_price_lookup


def test_real_workbook_loads_user_confirmed_aliases():
    load_price_lookup.cache_clear()
    lookup = load_price_lookup()

    deer = lookup.resolve("Deer Spray")
    dump_fee = lookup.resolve("Dump fee, ea [ea]")
    rotor = lookup.resolve("Rotor, 5004, ea [ea]")
    hedge = lookup.resolve("Hedge Shearing [Day]")

    assert deer is not None
    assert deer.lookup_name == "Deer Spray, Bozeman, ea"
    assert dump_fee is not None
    assert dump_fee.lookup_name == "Dump Fee Bozeman, ea"
    assert rotor is not None
    assert rotor.lookup_name == "5004 with Street L, ea"
    assert hedge is not None
    assert hedge.min_quantity == 1.0

