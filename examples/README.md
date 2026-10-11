# Example data

`flows_to_import.json` and `flows_to_import.csv` describe the same illustrative transfers. In Flow, open **Data > Import**, choose either file, inspect the preview, and include any similar records you deliberately want to add. Amounts are decimal strings in JSON; the `units` field is required. The examples include USD and kg, which have independent balances.

`record_objects.json` shows account and saved-flow objects, including a generated flow ID and separate balances for one account in different units. `review.sql` is a corresponding illustrative SQLite dump for inspecting the table relationships. Import through the application instead of running the SQL against a personal database.

To see the Python API in use, run `python3 -m examples.two_units` from the repository root. It transfers kg and Wh between the same two accounts, prints their independent balances, and writes only to a temporary database.
