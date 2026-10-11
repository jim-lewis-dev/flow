"""Use Flow's repository to move two independent resources.

Run from the repository root: python3 -m examples.two_units
This example stores its database in a temporary directory.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from flowledger.core import Repository, ledger_balance_at


def main():
    with TemporaryDirectory() as directory:
        repo = Repository(Path(directory) / "resources.sqlite3")
        try:
            opening = "2026-10-01"
            for unit, amount in (("kg", "20"), ("Wh", "100")):
                repo.save_account("Depot", opening_amount=amount, opening_at=opening, units=unit)
                repo.save_account("Lab", opening_amount="0", opening_at=opening, units=unit)

            for unit, amount, name in (("kg", "0.125", "Sample"), ("Wh", "8", "Energy")):
                repo.save_flow({
                    "date": "2026-10-03 09:15:06",
                    "name": name,
                    "from_account": "Depot",
                    "to_account": "Lab",
                    "amount": amount,
                    "units": unit,
                    "status": "Complete",
                    "category": "Research",
                    "tags": ["Experiment"],
                    "notes": "",
                })

            flows = repo.list_flows()
            for unit in ("kg", "Wh"):
                for name in ("Depot", "Lab"):
                    account = repo.get_account(name, unit)
                    balance = ledger_balance_at(account, flows, "2026-10-03 12:00:00")
                    print(f"{name}: {balance} {unit}")
        finally:
            repo.close()


if __name__ == "__main__":
    main()
