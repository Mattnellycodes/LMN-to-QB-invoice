"""Sheet locations and tab layout for the irrigation-only client routine."""

SCHEDULE_MASTER_SHEET_ID = "19CnRI2G-gOBJvCs6BFotJH_-n5FF06ebcUVopnPtqlo"
IRRIGATION_SHEET_ID = "15ElcgGzGVHRHb7gCl217HYh5AiwAb-5KTmC5e8oZa0g"

SCHEDULE_CLIENTS_TAB = "Clients"
SCHEDULE_CLIENTS_HEADER = "client"

# Irrigation tabs that hold client lists, with the header label of the name column.
IRRIGATION_TABS = {
    "Bozeman 26": "Client Name",
    "Big sky 26": "Client Name",
    "Remote clients 26": "Client Name",
}

OUTPUT_TAB = "Irrigation Only Clients"
OUTPUT_HEADER = [
    "Client",
    "Source Tab",
    "Section",
    "Status",
    "Possible Schedule Match",
    "Last Seen",
]

MEMORY_TAB = "Client Match Memory"
MEMORY_HEADER = [
    "Irrigation Name",
    "Schedule Client",
    "Status",  # confirmed | not_a_match | blank = awaiting human review
    "Notes",
    "Updated At",
]

# difflib SequenceMatcher ratio above which a schedule client is proposed as a
# possible match. 0.95 per Renner: be 95% confident before proposing on name
# similarity alone. Address, street-style, and containment rules are separate.
FUZZY_THRESHOLD = 0.95
