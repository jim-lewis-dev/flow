"""Create an illustrative three-day camp with independent energy and water flows.

Run from the repository root: python3 -m examples.camp [camp.sqlite3]
The destination must not already exist. Personal and demo databases are untouched.
"""

import argparse
from pathlib import Path

from flowledger.core import Repository, format_quantity, ledger_balance_at


POSITIONS = {
    "Sun": (0.10, 0.48),
    "Battery": (0.34, 0.48),
    "Fridge": (0.65, 0.13),
    "Laptop": (0.65, 0.36),
    "Cooking": (0.65, 0.61),
    "Lighting": (0.65, 0.85),
    "Heat": (0.91, 0.48),
    "Fresh tank": (0.12, 0.48),
    "Drinking": (0.40, 0.20),
    "Washing": (0.40, 0.80),
    "Waste tank": (0.80, 0.80)
}


def create_camp(path: str | Path) -> Path:
    """Write one new dataset; never replace or add to an existing file."""
    path = Path(path).expanduser().resolve()
    # Exclusive creation also prevents a race from replacing another file.
    with path.open("xb"):
        pass
    repo = None
    try:
        repo = Repository(path)
        with repo.atomic():
            opening = "2026-10-01 00:00:00"
            for name, amount in (("Sun", None), ("Battery", "600"),
                                 ("Fridge", "0"), ("Laptop", "0"),
                                 ("Cooking", "0"), ("Lighting", "0"), ("Heat", "0")):
                repo.save_account(name, opening_amount=amount, opening_at=opening, units="Wh")
            for name, amount in (("Fresh tank", "30"), ("Drinking", "0"),
                                 ("Cooking", "0"), ("Washing", "0"), ("Waste tank", "0")):
                repo.save_account(name, opening_amount=amount, opening_at=opening, units="L")

            def flow(day, clock, source, destination, amount, units, status, name, category):
                repo.save_flow(dict(date=f"2026-10-{day:02d} {clock}",
                                    from_account=source, to_account=destination,
                                    amount=amount, units=units, status=status,
                                    name=name, category=category, tags=["Camp", f"Day {day}"],
                                    notes="Illustrative quantities. Each unit is tracked independently."))

            for day, status, morning_sun, afternoon_sun in (
                    (1, "Complete", "300", "220"),
                    (2, "Pending", "240", "160"),
                    (3, "Planned", "100", "80")):
                flow(day, "08:00", "Sun", "Battery", morning_sun, "Wh", status,
                     "Morning solar input", "Charging")
                flow(day, "14:00", "Sun", "Battery", afternoon_sun, "Wh", status,
                     "Afternoon solar input", "Charging")
                for clock, released, destination, amount, name in (
                        ("09:00", "09:05", "Fridge", "80", "Morning refrigeration"),
                        ("09:00", "09:05", "Laptop", "45", "Morning work"),
                        ("12:00", "12:05", "Cooking", "120", "Lunch"),
                        ("18:00", "18:05", "Fridge", "100", "Evening refrigeration"),
                        ("18:00", "18:05", "Laptop", "50", "Evening work"),
                        ("18:00", "18:05", "Cooking", "150", "Dinner"),
                        ("18:00", "18:05", "Lighting", "15", "Evening lights")):
                    flow(day, clock, "Battery", destination, amount, "Wh", status,
                         name, "Energy use")
                    flow(day, released, destination, "Heat", amount, "Wh", status,
                         f"{name}: released heat", "Released heat")
                for clock, destination, amount, name in (
                        ("09:00", "Drinking", "2.5", "Drinking water"),
                        ("12:00", "Cooking", "1.5", "Meal preparation"),
                        ("18:00", "Washing", "4", "Washing up")):
                    flow(day, clock, "Fresh tank", destination, amount, "L", status,
                         name, "Water use")
                flow(day, "18:05", "Washing", "Waste tank", "4", "L", status,
                     "Collected wash water", "Wastewater")
            repo.save_map_positions(POSITIONS)
        return path
    except Exception:
        if repo is not None:
            repo.close()
            repo = None
        path.unlink(missing_ok=True)
        raise
    finally:
        if repo is not None:
            repo.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="camp.sqlite3", type=Path)
    args = parser.parse_args()
    try:
        path = create_camp(args.path)
    except FileExistsError:
        parser.exit(1, f"Already exists: {args.path}. Choose another filename.\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Could not create camp: {exc}\n")
    repo = Repository(path)
    try:
        flows = repo.list_flows()
        statuses = ("Complete", "Pending", "Planned")
        print(f"Created {path}\n{len(flows)} flows across three days; energy and water stay separate.")
        for name, units in (("Battery", "Wh"), ("Fresh tank", "L")):
            remaining = ledger_balance_at(repo.get_account(name, units), flows,
                                          "2026-10-03 23:59:59", statuses)
            print(f"Including planned flows: {name} = {format_quantity(remaining)} {units}")
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
