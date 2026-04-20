"""Tests for LMN→QBO item resolution."""

from src.mapping.item_mapping import build_item_refs, resolve_item_ref


FALLBACK = {"value": "99", "name": "Other"}


def _cache(*pairs):
    return {name.lower(): {"value": value, "name": name} for name, value in pairs}


class TestResolveItemRef:
    def test_exact_match_wins_and_is_not_fallback(self):
        cache = _cache(("Deer Spray", "1"), ("Mulch", "2"))
        ref, is_fallback = resolve_item_ref("Deer Spray", cache, {}, FALLBACK)
        assert ref["value"] == "1"
        assert is_fallback is False

    def test_exact_match_is_case_insensitive_and_trimmed(self):
        cache = _cache(("Dump Fee", "5"))
        ref, _ = resolve_item_ref("  dump fee  ", cache, {}, FALLBACK)
        assert ref["value"] == "5"

    def test_db_override_used_when_no_exact_match(self):
        overrides = {"Deer Spray, Bozeman, ea": {"value": "42", "name": "Deer Spray"}}
        ref, is_fallback = resolve_item_ref(
            "Deer Spray, Bozeman, ea", {}, overrides, FALLBACK
        )
        assert ref["value"] == "42"
        assert is_fallback is False

    def test_exact_match_preferred_over_db_override(self):
        cache = _cache(("Foo", "cache-id"))
        overrides = {"Foo": {"value": "override-id", "name": "Foo"}}
        ref, _ = resolve_item_ref("Foo", cache, overrides, FALLBACK)
        assert ref["value"] == "cache-id"

    def test_fallback_used_when_nothing_matches(self):
        ref, is_fallback = resolve_item_ref("Unknown Item", {}, {}, FALLBACK)
        assert ref == FALLBACK
        assert is_fallback is True

    def test_empty_lookup_name_uses_fallback(self):
        ref, is_fallback = resolve_item_ref("", {}, {}, FALLBACK)
        assert ref == FALLBACK
        assert is_fallback is True


class TestBuildItemRefs:
    def test_resolves_per_line_and_collects_fallback_names(self):
        invoices = [
            {
                "line_items": [
                    {"item_lookup_name": "Deer Spray"},
                    {"item_lookup_name": "Mystery Item"},
                    {"item_lookup_name": "Mulch"},
                ]
            },
            {
                "line_items": [
                    {"item_lookup_name": "Mystery Item"},
                    {"item_lookup_name": "Dump Fee"},
                ]
            },
        ]
        cache = _cache(("Deer Spray", "1"), ("Mulch", "2"))
        overrides = {"Dump Fee": {"value": "3", "name": "Dump Fee"}}

        refs, fallback_names = build_item_refs(invoices, cache, overrides, FALLBACK)

        assert refs["Deer Spray"]["value"] == "1"
        assert refs["Mulch"]["value"] == "2"
        assert refs["Dump Fee"]["value"] == "3"
        assert refs["Mystery Item"] == FALLBACK
        assert fallback_names == {"Mystery Item"}

    def test_dedupes_across_invoices(self):
        invoices = [
            {"line_items": [{"item_lookup_name": "Foo"}, {"item_lookup_name": "Foo"}]},
            {"line_items": [{"item_lookup_name": "Foo"}]},
        ]
        refs, fallback_names = build_item_refs(invoices, {}, {}, FALLBACK)
        assert list(refs.keys()) == ["Foo"]
        assert fallback_names == {"Foo"}

    def test_ignores_empty_lookup_names(self):
        invoices = [{"line_items": [{"item_lookup_name": ""}, {"item_lookup_name": None}]}]
        refs, fallback_names = build_item_refs(invoices, {}, {}, FALLBACK)
        assert refs == {}
        assert fallback_names == set()
