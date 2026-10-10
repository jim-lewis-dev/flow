# Physical XTest input throughout; direct application access only reads outcomes.
def entry(key_name):
    widget = app.form_widgets[key_name]
    return widget.entry if isinstance(widget, FilterCombo) else widget


def enter(key_name, text):
    replace(entry(key_name), text)
    if isinstance(app.form_widgets[key_name], FilterCombo):
        key('Return')


def cleared():
    assert app.editing_id is None
    for field in ('from_account', 'to_account', 'amount', 'name', 'tags'):
        assert app.form_vars[field].get() == '', field
    assert app.form_vars['category'].get() == 'Uncategorized'
    assert app.form_vars['status'].get() == 'Complete'
    assert app.form_vars['units'].get() == 'USD'
    assert app.notes_text.get('1.0', 'end-1c') == ''


def modal():
    return next(w for w in app.winfo_children()
                if isinstance(w, tk.Toplevel) and w.winfo_viewable())


from PIL import ImageGrab
key("Escape")
app.geometry("1920x1200+0+0")
pump(.25)
ImageGrab.grab().save(Path(__file__).resolve().parents[1] / "docs" / "Flow_Form.png")

# Complete new flow: field changes retain the draft until Add Flow.
click(entry('from_account'))
assert popups()
replace(entry('from_account'), 'Sav')
combo = app.form_widgets['from_account']
index = combo.matches.index('Savings')
x0, y0, width, height = combo.listbox.bbox(index)
click_xy(combo.listbox.winfo_rootx() + 15, combo.listbox.winfo_rooty() + y0 + height / 2)
assert app.form_vars['from_account'].get() == 'Savings'
clean_popups('Mouse-select suggestion closes cleanly')
enter('from_account', 'Checking')
enter('to_account', 'Audit Shop')
enter('amount', '42.75')
enter('name', 'Audit Flow Alpha')
enter('category', 'Audit Category')
enter('tags', 'First tag, Second tag')
replace(app.notes_text, 'New record notes')
enter('date', '2026-10-9 13:47:26')
key('Return')
assert app.form_vars['date'].get() == '2026-10-09 13:47:26'
count_before = len(repo.list_flows())
assert str(app.save_button.cget('text')).startswith('Add Flow')
click(app.save_button)
a = flow_named('Audit Flow Alpha')
assert len(repo.list_flows()) == count_before + 1
assert a['from_account'] == 'Checking' and a['to_account'] == 'Audit Shop'
assert a['amount'] == '42.75' and a['category'] == 'Audit Category'
assert a['tags'] == ['First tag', 'Second tag'] and a['notes'] == 'New record notes'
assert a['date'].endswith('13:47:26')
assert set(a) == {'flow_id', 'date', 'from_account', 'to_account', 'amount', 'units',
                  'status', 'name', 'notes', 'category', 'tags'}
cleared()
clean_popups('Add Flow creates one full record and clears the form and suggestions')
chord('Control_L', 'z')
assert len(repo.list_flows()) == count_before
chord('Control_L', 'y')
assert repo.get_flow(a['flow_id']) == a
ok('Undo and redo preserve all fields of the added flow and its identity')

# All three views of Notes share one buffer; completion writes the record.
row(app.ledger, a['flow_id'])
click(app.notes_button)
expanded = app.expanded_notes_text
click(expanded)
chord('Control_L', 'a')
type_text('First note line\nSecond note line\nThird note line')
notes = 'First note line\nSecond note line\nThird note line'
assert app.notes_text.get('1.0', 'end-1c') == notes
assert repo.get_flow(a['flow_id'])['notes'] == 'New record notes'
key('Escape')
assert app.notes_dialog is None
assert repo.get_flow(a['flow_id'])['notes'] == notes
click(app.notes_text)
chord('Control_L', 'End')
type_text('\nFourth note line')
notes += '\nFourth note line'
for direction in ('Up', 'Up', 'Up', 'Down', 'Down', 'Down'):
    key(direction)
    assert app.notes_text.bbox('insert') is not None
click(entry('amount'))
assert repo.get_flow(a['flow_id'])['notes'] == notes
chord('Control_L', 'z')
assert repo.get_flow(a['flow_id'])['notes'] == 'First note line\nSecond note line\nThird note line'
chord('Control_L', 'y')
assert repo.get_flow(a['flow_id'])['notes'] == notes
ok('Expanded Notes saves on close; compact Notes saves on click-away and keeps multiline navigation visible')

# Date-only, midnight and seconds remain distinct during automatic editing.
row(app.ledger, a['flow_id'])
enter('date', '2026-10-03 17:18:19')
key('Return')
assert repo.get_flow(a['flow_id'])['date'] == '2026-10-03 17:18:19'
enter('date', '2026-10-3 0')
key('Return')
assert repo.get_flow(a['flow_id'])['date'].endswith(' 00:00:00')
enter('date', '2026-10-3')
key('Return')
assert len(repo.get_flow(a['flow_id'])['date']) == 10
assert app.form_vars['date'].get() == '2026-10-03'
assert 'time' not in app.form_vars and not hasattr(app, 'time_button')
ok('Date, midnight and seconds auto-save through one Date/Time field')

# Existing record edits never create a duplicate or need a save prompt.
f = flow_named('October electricity')
row(app.ledger, f['flow_id'])
assert app.editing_id == f['flow_id']
clean_popups('Selecting an existing row loads all fields without a dropdown')
enter('from_account', 'Savings')
enter('to_account', 'Audit Utility')
enter('amount', '99.25')
enter('category', 'Edited Category')
enter('tags', 'Edited tag')
replace(app.notes_text, 'Edited record notes')
enter('date', f['date'][:10])
key('Return')
click(app.form_widgets['status'])
key('Up')
key('Return')
click(entry('name'))
f2 = repo.get_flow(f['flow_id'])
assert f2['from_account'] == 'Savings' and f2['to_account'] == 'Audit Utility'
assert f2['amount'] == '99.25' and f2['category'] == 'Edited Category'
assert f2['tags'] == ['Edited tag'] and f2['notes'] == 'Edited record notes'
assert len(f2['date']) == 10 and f2['status'] == 'Complete'
assert f2['flow_id'] == f['flow_id'] and len(repo.list_flows()) == count_before + 1
assert str(app.save_button.cget('state')) == 'disabled'
ok('Top fields auto-save endpoints, amount, category, tags, notes, optional time and status on the existing ID')
b = flow_named('Boat rope and hardware')
row(app.ledger, b['flow_id'])
assert app.form_vars['from_account'].get() == 'Cash'
assert app.form_vars['category'].get() == 'Boat'
assert app.form_vars['tags'].get() == 'Weekend project'
assert app.form_vars['date'].get() == b['date']
assert app.notes_text.get('1.0', 'end-1c') == 'Illustrative demo record.'
clean_popups('Selecting the next row replaces all fields without old edits carrying over')

enter('amount', '40.00')
click(entry('from_account'))
assert popups()
chord('Control_L', 's')
assert repo.get_flow(b['flow_id'])['amount'] == '40'
clean_popups('Ctrl+S from an open dropdown completes the edit without reopening suggestions')
click(entry('category'))
assert popups()
click(app.nav_buttons['accounts'])
assert app.current_view == 'accounts'
ImageGrab.grab().save(Path(__file__).resolve().parents[1] / 'docs' / 'Flow_Accounts.png')
clean_popups('Page navigation dismisses flow suggestions')
row(app.accounts_tree, 'Cash')
replace(app.account_widgets['opening'], '181.25')
key('Return')
assert repo.get_account('Cash')['opening_amount'] == '181.25'
click(app.account_new_button)
assert app.account_vars['name'].get() == ''
row(app.accounts_tree, 'Cash')
assert app.account_vars['name'].get() == 'Cash'
ok('Account auto-save, New Account and same-row reselection work')
click(app.nav_buttons['flows'])

row(app.ledger, b['flow_id'])
enter('amount', '41.01')
row(app.ledger, f['flow_id'])
assert app.editing_id == f['flow_id'] and repo.get_flow(b['flow_id'])['amount'] == '41.01'
assert not app.tk.splitlist(app.tk.call('grab', 'current'))
chord('Control_L', 'z')
assert repo.get_flow(b['flow_id'])['amount'] == '40'
chord('Control_L', 'y')
assert repo.get_flow(b['flow_id'])['amount'] == '41.01'
ok('Row switching saves the completed field without a prompt; undo and redo target the changed record')

row(app.ledger, a['flow_id'])
click_text('Void')
assert repo.get_flow(a['flow_id'])['status'] == 'Void'
cleared()
assert not app.tk.splitlist(app.tk.call('grab', 'current'))
chord('Control_L', 'z')
assert repo.get_flow(a['flow_id'])['status'] == 'Complete'
chord('Control_L', 'y')
assert repo.get_flow(a['flow_id'])['status'] == 'Void'
ok('Void clears the form without confirmation and supports session undo/redo')

# New invalid records remain drafts; no vocabulary is persisted early.
click(app.new_flow_button)
enter('from_account', 'Checking')
enter('to_account', 'Audit Utility')
enter('amount', 'invalid')
enter('name', 'Corrected Audit Flow')
enter('category', 'Only After Add')
count = len(repo.list_flows())
click(app.save_button)
assert len(repo.list_flows()) == count and app.form_vars['amount'].get() == 'invalid'
assert 'Only After Add' not in repo.list_categories()
enter('amount', '12.34')
click(app.save_button)
c = flow_named('Corrected Audit Flow')
assert c['amount'] == '12.34'
cleared()
ok('Invalid Add Flow retains the draft without side effects; correcting the amount creates the record')

click(app.new_flow_button)
enter('name', 'Never added')
enter('amount', '55.00')
row(app.ledger, c['flow_id'])
assert app.form_vars['name'].get() == 'Corrected Audit Flow'
assert not any(flow['name'] == 'Never added' for flow in repo.list_flows())
assert not app.tk.splitlist(app.tk.call('grab', 'current'))
ok('Selecting an existing row silently drops an unfinished new-flow draft')

# The X hides a suggestion only, and that choice participates in session undo.
click(app.new_flow_button)
click(entry('category'))
combo = app.form_widgets['category']
index = combo.matches.index('Audit Category')
bounds = combo.listbox.bbox(index)
assert bounds
click_xy(combo.remove_canvas.winfo_rootx() + combo.remove_canvas.winfo_width() / 2,
         combo.listbox.winfo_rooty() + bounds[1] + bounds[3] / 2)
assert 'Audit Category' not in combo.values
assert repo.get_flow(a['flow_id'])['category'] == 'Audit Category'
chord('Control_L', 'z')
assert 'Audit Category' in combo.values
chord('Control_L', 'y')
assert 'Audit Category' not in combo.values
chord('Control_L', 'z')
assert 'Audit Category' in combo.values
assert not popups()
ok('Suggestion X removes only the choice; session undo and redo restore visibility without changing records')

# Filters retain selection identity and completed edits stay persisted.
row(app.ledger, b['flow_id'])
enter('amount', '41.50')
search = next(w for w in widgets() if w.winfo_class() == 'TEntry'
              and str(w.cget('textvariable')) == str(app.search_var))
replace(search, 'electric')
assert repo.get_flow(b['flow_id'])['amount'] == '41.5'
assert app.editing_id == b['flow_id']
replace(search, '')
assert b['flow_id'] in app.ledger_rows
ok('Filtering away an edited record saves the correct ID without a stale cell or record switch')

# Graph interaction remains usable after edit/undo/navigation.
click(app.nav_buttons['charts'])
click_text('None')
index = next(i for i in range(app.chart_accounts.size()) if app.chart_account_pairs[i] == ('Cash', 'USD'))
bounds = app.chart_accounts.bbox(index)
assert bounds
click_xy(app.chart_accounts.winfo_rootx() + 20, app.chart_accounts.winfo_rooty() + bounds[1] + bounds[3] / 2)
click_text('Update Graph')
pump(.4)
assert [account['name'] for account in app.chart_inspection_context[0]] == ['Cash']
chart = app.canvas.get_tk_widget()
click_xy(chart.winfo_rootx() + chart.winfo_width() * .55,
         chart.winfo_rooty() + chart.winfo_height() * .5)
assert 'Cash:' in app.chart_inspect.get()
ok('Graph selection and exact inspection still work after form and account edits')
assert not errors, errors
assert not [m for m in maps if m['override'] and m['geometry'].endswith('+0+0')]
ok('No callback errors or custom dropdowns first appearing at the top-left corner')
