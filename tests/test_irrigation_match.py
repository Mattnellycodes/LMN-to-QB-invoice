"""Tests for the irrigation-only client matching tool (tools/irrigation_match).

Fixtures are synthetic but mirror the naming patterns observed in the real
sheets; the real .xlsx snapshots contain client PII and are not committed.
"""

from tools.irrigation_match.extract import (
    extract_irrigation_clients,
    extract_schedule_clients,
)
from tools.irrigation_match.main import (
    build_memory_proposals,
    build_output_rows,
    load_memory,
)
from tools.irrigation_match.matching import (
    IrrigationClient,
    ScheduleClient,
    addresses_match,
    name_similarity,
    numbers_conflict,
    reconcile,
    street_style_match,
    tokens_contained,
)

THRESHOLD = 0.95


def schedule(*names_addresses):
    return [ScheduleClient(name=n, address=a) for n, a in names_addresses]


def irrigation(*names_addresses):
    return [
        IrrigationClient(name=n, address=a, source_tab="Test 26")
        for n, a in names_addresses
    ]


class TestGuards:
    def test_different_street_numbers_never_match(self):
        assert numbers_conflict("150 Andesite", "170 Andesite")
        assert name_similarity("150 Andesite", "170 Andesite") == 0.0

    def test_same_numbers_do_not_conflict(self):
        assert not numbers_conflict("53 Andesite Ridge", "53 Andesite")

    def test_short_names_cannot_ride_containment(self):
        assert not tokens_contained("Kim", "Kimberly, Jones")
        assert tokens_contained("Welles", "Welles, Jeffrey")


class TestRules:
    def test_street_style_match(self):
        assert street_style_match("13 Wicki Up", "13 Wickiup Trail")
        assert not street_style_match("14 Andesite", "14 Patriot Drive")

    def test_address_match_ignores_city_tail(self):
        assert addresses_match(
            "150 Andesite, Big Sky, Montana 59716", "150 Andesite Rd"
        )
        assert not addresses_match("150 Andesite", "170 Andesite")

    def test_comma_flip_is_exact_strength(self):
        assert name_similarity("Margaret Dowling", "Dowling, Margaret") == 1.0


class TestReconcile:
    def test_exact_memory_fuzzy_and_only(self):
        sched = schedule(
            ("Dowling, Margaret", ""), ("Slamowitz", ""), ("Keating, Jamie", "")
        )
        irr = irrigation(
            ("Dowling, Margaret", ""),  # exact
            ("Slamowotz", ""),  # 89% — below 0.95, no other rule
            ("Keating - Main House", ""),  # containment via base name
            ("Tinworks", ""),  # nothing close
        )
        result = reconcile(irr, sched, {}, THRESHOLD)
        assert [(c.name, s) for c, s, _ in result.matched] == [
            ("Dowling, Margaret", "Dowling, Margaret")
        ]
        assert [(c.name, s) for c, s, _ in result.needs_review] == [
            ("Keating - Main House", "Keating, Jamie")
        ]
        assert sorted(c.name for c, _ in result.irrigation_only) == [
            "Slamowotz",
            "Tinworks",
        ]

    def test_confirmed_memory_wins(self):
        sched = schedule(("Slamowitz", ""))
        irr = irrigation(("Slamowotz", ""))
        memory = {"slamowotz": ("Slamowitz", "confirmed")}
        result = reconcile(irr, sched, memory, THRESHOLD)
        assert result.matched[0][1:] == ("Slamowitz", "memory")

    def test_not_a_match_memory_forces_irrigation_only(self):
        sched = schedule(("Liam", "335 Andesite Ridge Road"))
        irr = irrigation(("Kim", "335 Andesite Ridge Road"))
        memory = {"kim": ("", "not_a_match")}
        result = reconcile(irr, sched, memory, THRESHOLD)
        assert result.irrigation_only[0][0].name == "Kim"

    def test_confirmed_client_dropped_from_schedule_resurfaces(self):
        result = reconcile(
            irrigation(("Smith - House", "")),
            schedule(("Jones, Bob", "")),
            {"smith house": ("Smith, Rob", "confirmed")},
            THRESHOLD,
        )
        client, reason = result.irrigation_only[0]
        assert client.name == "Smith - House"
        assert "no longer on schedule master" in reason

    def test_blank_memory_status_stays_in_review_without_new_proposal(self):
        sched = schedule(("Moffet", "69 Quartz Road"))
        irr = irrigation(("Moffett", "69 Quartz Road, Big Sky"))
        memory = {"moffett": ("Moffet", "")}
        result = reconcile(irr, sched, memory, THRESHOLD)
        assert result.needs_review[0][0].name == "Moffett"
        assert build_memory_proposals(result, memory, "2026-06-11") == []


class TestExtract:
    SCHEDULE_ROWS = [
        ["client", "hours", "address"],
        ["Dowling, Margaret", "4", "876 Springhill School Rd"],
        ["", "", ""],
        ["53 Andesite Ridge ", "22", "53 Andesite Ridge, Big Sky"],
    ]
    IRRIGATION_ROWS = [
        ["junk above the table", "", ""],
        ["Notes", "Client Name", "Address"],
        ["", "Adams, Matt", "10749 Bridger Canyon"],
        ["", "807.0", ""],
        ["Blowout Only Clients:", "", ""],
        ["", "Lerch, Bill", "In Canyon"],
        ["Maintence Only:", "", ""],
        ["", "Pieper, Joanne", "1429 Blackbull trail"],
        ["Past/ Inactive Clients:", "", ""],
        ["", "Blickenstaff", "1899 West Cameron Bridge"],
    ]

    def test_schedule_extraction_skips_blanks_and_trims(self):
        clients = extract_schedule_clients(self.SCHEDULE_ROWS, "client")
        assert [c.name for c in clients] == ["Dowling, Margaret", "53 Andesite Ridge"]
        assert clients[1].address == "53 Andesite Ridge, Big Sky"

    def test_irrigation_sections_and_junk_rows(self):
        clients = extract_irrigation_clients(
            self.IRRIGATION_ROWS, "Client Name", "Bozeman 26"
        )
        assert [(c.name, c.section) for c in clients] == [
            ("Adams, Matt", "Active"),
            ("Lerch, Bill", "Blowout Only"),
        ]


class TestOutput:
    def test_output_rows_and_proposals(self):
        sched = schedule(("Keating, Jamie", ""))
        irr = irrigation(("Keating - Main House", ""), ("Tinworks", ""))
        result = reconcile(irr, sched, {}, THRESHOLD)
        rows = build_output_rows(result, "2026-06-11")
        assert rows[0][0] == "Client"
        assert rows[1] == [
            "Tinworks",
            "Test 26",
            "Active",
            "Irrigation Only",
            "",
            "2026-06-11",
        ]
        assert rows[2][:5] == [
            "Keating - Main House",
            "Test 26",
            "Active",
            "Needs Review",
            "Keating, Jamie",
        ]
        proposals = build_memory_proposals(result, {}, "2026-06-11")
        assert proposals == [
            [
                "Keating - Main House",
                "Keating, Jamie",
                "",
                "name contained in schedule name",
                "2026-06-11",
            ]
        ]

    def test_load_memory_normalizes_keys_and_status(self):
        rows = [
            ["Irrigation Name", "Schedule Client", "Status", "Notes", "Updated At"],
            ["Slamowotz ", "Slamowitz", "Confirmed", "", "2026-06-11"],
            ["", "ignored", "", "", ""],
        ]
        assert load_memory(rows) == {"slamowotz": ("Slamowitz", "confirmed")}
