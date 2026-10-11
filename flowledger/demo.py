"""Illustrative October-December 2026 resource flows, seeded only into an empty file.

Future demo entries are explicit individual records. There is no recurrence
generator. This file is a useful example of programmatic entry into the engine.
"""


def seed_demo(repo) -> bool:
    """Seed a completely empty repository; return whether records were inserted."""
    if repo.list_accounts() or repo.list_flows():
        return False
    anchor = "2026-10-01 00:00:00"
    with repo.atomic():
        for name, amount in [("Checking", "3200"), ("Savings", "5000"), ("Cash", "180"), ("AmEx", "-420")]:
            repo.save_account(name, opening_amount=amount, opening_at=anchor, units="USD")
        repo.save_account("Warehouse", opening_amount="20", opening_at=anchor, units="kg")
        repo.save_account("Workshop", opening_amount="0", opening_at=anchor, units="kg")
        rows = [
            ("2026-10-01 08:15", "AmEx", "Joe's Diner", "12.00", "Complete", "Breakfast at Joe's", "Dining", ["Everyday"]),
            ("2026-10-01 17:40", "AmEx", "H-E-B", "84.32", "Complete", "Groceries for the week", "Groceries", []),
            ("2026-10-01 17:40", "AmEx", "H-E-B", "16.48", "Complete", "Cleaning supplies", "Household", ["Same receipt"]),
            ("2026-10-02 09:00", "Checking", "Electric company", "97.40", "Pending", "October electricity", "Utilities", []),
            ("2026-10-02 10:30", "Cash", "West Marine", "39.95", "Complete", "Boat rope and hardware", "Boat", ["Weekend project"]),
            ("2026-10-02 12:00", "Checking", "AmEx", "420.00", "Complete", "Credit card payment", "Transfer", []),
            ("2026-10-02 12:30", "H-E-B", "AmEx", "5.00", "Complete", "Grocery refund", "Groceries", []),
            ("2026-10-03", "Checking", "Landlord", "1250.00", "Planned", "October rent", "Housing", []),
            ("2026-10-09", "Checking", "Fuel station", "55.00", "Planned", "Road trip fuel", "Fuel", ["Road trip"]),
            ("2026-10-15", "Employer", "Checking", "2200.00", "Planned", "October pay", "Wages", []),
            ("2026-10-20", "Checking", "Savings", "300.00", "Planned", "Move to savings", "Transfer", []),
            ("2026-10-30", "Employer", "Checking", "2200.00", "Planned", "Second October pay", "Wages", []),
            ("2026-11-03", "Checking", "Landlord", "1250.00", "Planned", "November rent", "Housing", []),
            ("2026-11-15", "Employer", "Checking", "2200.00", "Planned", "November pay", "Wages", []),
            ("2026-11-30", "Employer", "Checking", "2200.00", "Planned", "Second November pay", "Wages", []),
            ("2026-12-03", "Checking", "Landlord", "1250.00", "Planned", "December rent", "Housing", []),
            ("2026-12-15", "Employer", "Checking", "2200.00", "Planned", "December pay", "Wages", []),
        ]
        for date, source, destination, amount, status, name, category, tags in rows:
            repo.save_flow(dict(date=date, from_account=source, to_account=destination,
                                amount=amount, units="USD", status=status, name=name, category=category,
                                tags=tags, notes="Illustrative demo record."))
        for date, amount, status, name in [
            ("2026-10-01 14:00", "0.125", "Complete", "Material sample"),
            ("2026-10-02 11:00", "1.5", "Pending", "Material shipment"),
            ("2026-10-09", "2.25", "Planned", "Upcoming materials"),
        ]:
            repo.save_flow(dict(date=date, from_account="Warehouse", to_account="Workshop",
                                amount=amount, units="kg", status=status, name=name,
                                category="Materials", tags=["Workshop"],
                                notes="Illustrative material transfer."))
    return True
