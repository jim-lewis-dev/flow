# Example data

## Three days off grid

From the repository root:

```bash
python3 -m examples.camp
./flow camp
```

This creates a separate `camp.sqlite3` in the current directory and refuses to overwrite an existing file. You can supply another destination: `python3 -m examples.camp /path/to/weekend.sqlite3`.

The camp begins with **600 Wh in Battery** and **30 L in Fresh tank**. Over October 1-3, solar input falls while energy use stays at 560 Wh/day. The first day is Complete, the second Pending, and the third Planned. These are ordinary editable flows, not a weather model or automatic schedule.

Try these views:

- **Graphs:** select Battery / Wh and choose Including Planned. Its end-of-day balance falls from 560 to 400 to **20 Wh**. Fresh tank / L ends at **6 L**.
- **Experimental:** choose Wh and Including Planned, then play. Energy moves from Sun through Battery and the appliances to Heat. Step to October 1 at 18:00 for the map shown in the main README. Four different quantities leave Battery at 18:00 each day; their moving dots have different sizes. Drag accounts into your own arrangement; the layout stays with the file.
- **Water:** set Experimental Units to L. Fresh tank supplies Drinking, Cooking, and Washing; used wash water moves to Waste tank. Cooking participates in both resources without adding Wh to litres.
- **Flows:** filter From to Battery and Category to Energy use to inspect demand. Filter Status to Planned to edit the third day's assumptions.

Sun has an unknown opening balance because it marks the external energy source. The battery, appliances, heat, and water accounts have known starting balances. Heat is a destination for used electrical energy; it is still measured in Wh, with no conversion to another unit.

## Import a few records

`flows_to_import.json` and `flows_to_import.csv` describe the same illustrative transfers. In Flow, open **Data > Import**, choose either file, inspect the preview, and include any similar records you deliberately want to add. Amounts are decimal strings in JSON; the `units` field is required. The examples include USD and kg, which have independent balances. These files contain flows only; set starting balances in Accounts to graph balances.

## Inspect the model

`record_objects.json` shows account and saved-flow objects, including a generated flow ID and separate balances for one account in different units. `review.sql` is a corresponding illustrative SQLite dump for inspecting the table relationships. Import through the application instead of running the SQL against a personal database.

To see the Python API in use, run `python3 -m examples.two_units` from the repository root. It transfers kg and Wh between the same two accounts, prints their independent balances, and writes only to a temporary database.
