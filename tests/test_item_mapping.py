"""Tests for LMN→QBO item resolution (4-round matching)."""

from src.mapping.item_mapping import (
    build_item_refs,
    build_normalized_cache,
    canonicalize_item_name,
    resolve_item_ref,
)


FALLBACK = {"value": "99", "name": "Other"}


def _cache(*pairs):
    """Build an item_cache in the shape fetch_all_items returns."""
    return {name.lower(): {"value": value, "name": name} for name, value in pairs}


class TestCanonicalizeItemName:
    def test_strips_trailing_bracketed_unit(self):
        assert canonicalize_item_name("Mulch, Soil Pep, bulk [Yd]") == "Mulch, Soil Pep, bulk"
        assert canonicalize_item_name("Deer Spray [ea]") == "Deer Spray"

    def test_strips_trailing_parenthetical(self):
        assert canonicalize_item_name("Dump fee (maint)") == "Dump fee"
        assert canonicalize_item_name("General Maintenance (VT)") == "General Maintenance"

    def test_strips_trailing_comma_unit_in_allowlist(self):
        assert canonicalize_item_name("Fertilizer, Bagged, ea") == "Fertilizer, Bagged"
        assert canonicalize_item_name("Hedge Shearing, daily") == "Hedge Shearing"
        assert canonicalize_item_name("Concrete Delivered, yd") == "Concrete Delivered"
        assert canonicalize_item_name("Seed Mix, Lawn, lb") == "Seed Mix, Lawn"

    def test_keeps_trailing_comma_token_not_in_allowlist(self):
        # 'bagged' is a Type descriptor, not a unit — keep it.
        assert canonicalize_item_name("Fertilizer, bagged") == "Fertilizer, bagged"

    def test_unit_match_is_case_insensitive(self):
        assert canonicalize_item_name("Foo, EA") == "Foo"
        assert canonicalize_item_name("Foo, Yd") == "Foo"
        assert canonicalize_item_name("Foo, Daily") == "Foo"

    def test_iterates_multiple_layers(self):
        # Bracket → paren → comma-unit, all stripped.
        assert canonicalize_item_name("Foo [ea] (maint)") == "Foo"
        assert canonicalize_item_name("Widget, ea (maint)") == "Widget"

    def test_handles_empty_and_whitespace(self):
        assert canonicalize_item_name("") == ""
        assert canonicalize_item_name("   ") == ""
        assert canonicalize_item_name("Plain Name") == "Plain Name"


class TestBuildNormalizedCache:
    def test_builds_canonical_lookups(self):
        cache = _cache(
            ("Dump fee (maint)", "1"),
            ("Hedge Shearing, daily", "2"),
            ("Fertilizer, Bagged, ea", "3"),
        )
        norm = build_normalized_cache(cache)
        assert norm["dump fee"]["value"] == "1"
        assert norm["hedge shearing"]["value"] == "2"
        assert norm["fertilizer, bagged"]["value"] == "3"

    def test_drops_canonical_collisions_entirely(self):
        cache = _cache(
            ("Dump fee (maint)", "1"),
            ("Dump fee, ea", "2"),  # collides with #1 on canonical "dump fee"
            ("Mulch, Soil Pep, bulk, Yd", "3"),
        )
        norm = build_normalized_cache(cache)
        assert "dump fee" not in norm, "collisions must be dropped, not picked arbitrarily"
        assert norm["mulch, soil pep, bulk"]["value"] == "3"

    def test_empty_canonical_skipped(self):
        cache = _cache(
            ("[ea]", "1"),  # canonicalizes to empty string
            ("Real Item", "2"),
        )
        norm = build_normalized_cache(cache)
        assert "" not in norm
        assert norm["real item"]["value"] == "2"


class TestResolveItemRef:
    def test_round_1_exact_match_wins(self):
        cache = _cache(("Dump fee, ea", "1"), ("Dump fee (maint)", "2"))
        norm = build_normalized_cache(cache)
        ref, is_fallback = resolve_item_ref("Dump fee, ea", cache, norm, {}, FALLBACK)
        assert ref["value"] == "1"
        assert is_fallback is False

    def test_round_1_is_case_insensitive_and_trimmed(self):
        cache = _cache(("Dump Fee", "5"))
        norm = build_normalized_cache(cache)
        ref, _ = resolve_item_ref("  dump fee  ", cache, norm, {}, FALLBACK)
        assert ref["value"] == "5"

    def test_round_2_normalized_match_when_no_exact(self):
        cache = _cache(("Hedge Shearing, daily", "42"))
        norm = build_normalized_cache(cache)
        ref, is_fallback = resolve_item_ref("Hedge Shearing", cache, norm, {}, FALLBACK)
        assert ref["value"] == "42"
        assert is_fallback is False

    def test_round_2_handles_fertilizer_case_insensitive_canonical(self):
        cache = _cache(("Fertilizer, Bagged, ea", "7"))
        norm = build_normalized_cache(cache)
        ref, _ = resolve_item_ref("Fertilizer, bagged", cache, norm, {}, FALLBACK)
        assert ref["value"] == "7"

    def test_round_2_skipped_when_canonical_collision(self):
        cache = _cache(("Dump fee (maint)", "1"), ("Dump fee, ea", "2"))
        norm = build_normalized_cache(cache)
        # Neither canonical key exists in normalized cache (collision).
        ref, is_fallback = resolve_item_ref("Dump fee", cache, norm, {}, FALLBACK)
        assert is_fallback is True
        assert ref == FALLBACK

    def test_round_3_db_override_when_no_exact_or_normalized(self):
        overrides = {"Custom Thing": {"value": "42", "name": "Mapped"}}
        cache = _cache(("Other Item", "1"))
        norm = build_normalized_cache(cache)
        ref, is_fallback = resolve_item_ref(
            "Custom Thing", cache, norm, overrides, FALLBACK
        )
        assert ref["value"] == "42"
        assert is_fallback is False

    def test_normalized_match_beats_db_override(self):
        # Per user decision: automatic matches win over manual mappings.
        cache = _cache(("Hedge Shearing, daily", "auto"))
        norm = build_normalized_cache(cache)
        overrides = {"Hedge Shearing": {"value": "manual", "name": "Different"}}
        ref, _ = resolve_item_ref("Hedge Shearing", cache, norm, overrides, FALLBACK)
        assert ref["value"] == "auto"

    def test_round_4_fallback_when_all_miss(self):
        ref, is_fallback = resolve_item_ref("Unknown Item", {}, {}, {}, FALLBACK)
        assert ref == FALLBACK
        assert is_fallback is True

    def test_empty_lookup_name_uses_fallback(self):
        ref, is_fallback = resolve_item_ref("", {}, {}, {}, FALLBACK)
        assert ref == FALLBACK
        assert is_fallback is True


class TestBuildItemRefs:
    def test_resolves_per_line_via_rounds_and_collects_fallback_names(self):
        cache = _cache(
            ("Deer Spray, Bozeman, ea", "1"),
            ("Hedge Shearing, daily", "2"),
            ("Dump fee, ea", "3"),
        )
        norm = build_normalized_cache(cache)
        overrides = {"Special Override": {"value": "9", "name": "Special"}}

        invoices = [
            {"line_items": [
                {"item_lookup_name": "Deer Spray, Bozeman"},    # Round 2
                {"item_lookup_name": "Hedge Shearing"},          # Round 2
                {"item_lookup_name": "Mystery Item"},            # Round 4 (fallback)
            ]},
            {"line_items": [
                {"item_lookup_name": "Dump fee, ea"},            # Round 1
                {"item_lookup_name": "Special Override"},        # Round 3
                {"item_lookup_name": "Mystery Item"},            # dedup
            ]},
        ]

        refs, fallback_names = build_item_refs(
            invoices, cache, norm, overrides, FALLBACK
        )

        assert refs["Deer Spray, Bozeman"]["value"] == "1"
        assert refs["Hedge Shearing"]["value"] == "2"
        assert refs["Dump fee, ea"]["value"] == "3"
        assert refs["Special Override"]["value"] == "9"
        assert refs["Mystery Item"] == FALLBACK
        assert fallback_names == {"Mystery Item"}

    def test_dedupes_across_invoices(self):
        invoices = [
            {"line_items": [{"item_lookup_name": "Foo"}, {"item_lookup_name": "Foo"}]},
            {"line_items": [{"item_lookup_name": "Foo"}]},
        ]
        refs, fallback_names = build_item_refs(invoices, {}, {}, {}, FALLBACK)
        assert list(refs.keys()) == ["Foo"]
        assert fallback_names == {"Foo"}

    def test_ignores_empty_lookup_names(self):
        invoices = [
            {"line_items": [{"item_lookup_name": ""}, {"item_lookup_name": None}]}
        ]
        refs, fallback_names = build_item_refs(invoices, {}, {}, {}, FALLBACK)
        assert refs == {}
        assert fallback_names == set()
