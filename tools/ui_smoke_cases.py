# All manipulations below are physical XTest events; direct reads inspect outcomes.
def entry(key_name):
    w=app.form_widgets[key_name]
    return w.entry if isinstance(w,FilterCombo) else w
def enter(key_name,text):
    replace(entry(key_name),text)
    if isinstance(app.form_widgets[key_name],FilterCombo):key('Return')
def current_fields():return {k:v.get() for k,v in app.form_vars.items()}
def cleared():
    assert app.editing_id is None,('editing_id',app.editing_id,current_fields())
    for k in ['from_account','to_account','amount','name','tags','time']:
        assert app.form_vars[k].get()=='',(k,current_fields())
    assert app.form_vars['category'].get()=='Uncategorized'
    assert app.form_vars['status'].get()=='Complete'
    assert app.notes_text.get('1.0','end-1c')==''
def modal():
    return next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel) and w.winfo_viewable())
# Full new entry, use both suggestion click and typed-new acceptance.
click(entry('from_account'));assert popups()
assert maps and maps[-1]['geometry'].split('+')[1:] != ['0','0'],maps
replace(entry('from_account'),'Sav')
# Click a filtered existing value.
w=app.form_widgets['from_account'];idx=w.matches.index('Savings')
x0,y0,width,height=w.listbox.bbox(idx)
click_xy(w.listbox.winfo_rootx()+15,w.listbox.winfo_rooty()+y0+height/2)
assert app.form_vars['from_account'].get()=='Savings',(app.form_vars['from_account'].get(),w.matches,str(app.focus_get()),maps)
clean_popups('Mouse-select suggestion closes cleanly')
enter('from_account','Checking');enter('to_account','Audit Shop')
enter('amount','42.75');enter('name','Audit Flow Alpha');enter('category','Audit Category');enter('tags','First tag, Second tag')
replace(app.notes_text,'New record notes')
click(app.time_button);d=modal();time_entry=next(w for w in widgets(d) if w.winfo_class()=='TEntry')
replace(time_entry,'13:47:26');click_text('Apply',d)
assert app.form_vars['time'].get()=='13:47:26'
click(app.save_button);pump(.2)
a=flow_named('Audit Flow Alpha')
assert a['from_account']=='Checking' and a['to_account']=='Audit Shop'
assert a['amount_cents']==4275 and a['category']=='Audit Category'
assert a['tags']==['First tag','Second tag'] and a['notes']=='New record notes'
assert a['date'].endswith('13:47:26')
assert set(a)=={'flow_id','date','from_account','to_account','amount_cents','status','name','notes','category','tags'}
cleared();assert not app.ledger.selection();clean_popups('Create full flow clears editor, selection, and popup')
# Expanded and inline Notes share one draft; closing never drops typed text.
row(app.ledger,a['flow_id']);click(app.notes_button)
assert app.notes_dialog is not None and app.expanded_notes_text is not None
expanded=app.expanded_notes_text
click(expanded);chord('Control_L','a');type_text('First note line\nSecond note line\nThird note line')
notes='First note line\nSecond note line\nThird note line'
assert app.notes_text.get('1.0','end-1c')==notes
assert repo.get_flow(a['flow_id'])['notes']=='New record notes'
key('Escape');pump(.2)
assert app.notes_dialog is None and app.notes_text.get('1.0','end-1c')==notes
click(app.notes_text);chord('Control_L','End');type_text('\nFourth note line')
notes+='\nFourth note line'
assert app.notes_text.get('1.0','end-1c')==notes
# Up and Down keep the insertion cursor on screen even in the compact text field.
for direction in ('Up','Up','Up','Down','Down','Down'):
    key(direction)
    assert app.notes_text.bbox('insert') is not None,(direction,app.notes_text.index('insert'),app.notes_text.yview())
click(app.notes_button)
assert app.expanded_notes_text.get('1.0','end-1c')==notes
click(app.expanded_notes_text);chord('Control_L','End');type_text(' - expanded')
notes+=' - expanded'
assert app.notes_text.get('1.0','end-1c')==notes
click_text('Close',app.notes_dialog)
assert app.notes_dialog is None and app.notes_text.get('1.0','end-1c')==notes
ok('Expanded Notes and inline Notes share edits; Escape and Close retain the draft')
click(app.save_button);cleared()
assert repo.get_flow(a['flow_id'])['notes']==notes
row(app.ledger,a['flow_id']);assert app.notes_text.get('1.0','end-1c')==notes
ok('Multiline Notes stay visible during arrow navigation and survive Save/reload')
click(app.notes_button);click(app.expanded_notes_text);chord('Control_L','End');type_text(' again')
notes+=' again'
click(app.save_button)
assert app.notes_dialog is None and repo.get_flow(a['flow_id'])['notes']==notes
cleared()
ok('Main Save commits expanded Notes directly and closes the old editor')
# Date-only, explicit midnight, and seconds survive actual editing and reloading.
row(app.ledger,a['flow_id'])
assert app.form_vars['time'].get()=='13:47:26'
enter('date','2026-10-03 17:18:19');click(app.save_button)
assert repo.get_flow(a['flow_id'])['date']=='2026-10-03 17:18:19'
row(app.ledger,a['flow_id']);assert app.form_vars['time'].get()=='17:18:19'
click(app.time_button);d=modal();time_entry=next(w for w in widgets(d) if w.winfo_class()=='TEntry')
replace(time_entry,'00:00:00');click_text('Apply',d);click(app.save_button)
assert repo.get_flow(a['flow_id'])['date'].endswith(' 00:00:00')
row(app.ledger,a['flow_id']);assert app.form_vars['time'].get()=='00:00:00'
click(app.time_button);d=modal();click_text('Clear',d);click_text('Apply',d);click(app.save_button)
assert len(repo.get_flow(a['flow_id'])['date'])==10
row(app.ledger,a['flow_id']);assert app.form_vars['time'].get()==''
ok('Second precision, explicit midnight, and date-only values survive Save/reload')
# Existing A -> edit every optional field -> Save -> existing B must reflect only B.
f=flow_named('October electricity');row(app.ledger,f['flow_id'])
assert app.editing_id==f['flow_id']
assert app.form_vars['status'].get()=='Pending' and app.form_vars['time'].get()=='09:00:00'
clean_popups('Single click loads existing flow without popup')
enter('from_account','Savings');enter('to_account','Audit Utility');enter('amount','99.25')
enter('category','Edited Category');enter('tags','Edited tag');replace(app.notes_text,'Edited record notes')
click(app.time_button);d=modal();click_text('Clear',d);click_text('Apply',d)
click(app.form_widgets['status']);key('Up');key('Return')
assert app.form_vars['status'].get()=='Complete'
click(app.save_button)
f2=repo.get_flow(f['flow_id'])
assert f2['from_account']=='Savings' and f2['to_account']=='Audit Utility'
assert f2['amount_cents']==9925 and f2['category']=='Edited Category'
assert f2['tags']==['Edited tag'] and f2['notes']=='Edited record notes' and len(f2['date'])==10
assert f2['status']=='Complete' and f2['flow_id']==f['flow_id']
cleared();clean_popups('Edit endpoints and metadata; settle Pending to Complete without changing ID')
b=flow_named('Boat rope and hardware');row(app.ledger,b['flow_id'])
assert app.editing_id==b['flow_id']
assert app.form_vars['from_account'].get()=='Cash' and app.form_vars['category'].get()=='Boat'
assert app.form_vars['status'].get()=='Complete' and app.form_vars['tags'].get()=='Weekend project'
assert app.form_vars['time'].get()=='10:30:00' and app.notes_text.get('1.0','end-1c')=='Illustrative demo record.'
clean_popups('Click next flow replaces every field without carryover')
# Saving with Ctrl+S while a suggestion is open must not reopen it on key release.
enter('amount','40.00');click(entry('from_account'));assert popups()
chord('Control_L','s');pump(.3)
assert repo.get_flow(b['flow_id'])['amount_cents']==4000
cleared();clean_popups('Ctrl+S from open dropdown does not reopen on key release')
# Same row can be clicked again after save/reset (no stale tree selection).
row(app.ledger,b['flow_id']);assert app.editing_id==b['flow_id'];ok('Same row reloads after save')
# Page transition dismisses entry suggestions.
click(entry('category'));assert popups();click(app.nav_buttons['accounts'])
assert app.current_view=='accounts';clean_popups('Navigation dismisses flow suggestions')
# Accounts: click row, edit, save, New, click same row again.
row(app.accounts_tree,'Cash');assert app.account_vars['name'].get()=='Cash'
account_opening=next(w for w in widgets(app.account_editor) if w.winfo_class()=='TEntry' and str(w.cget('textvariable'))==str(app.account_vars['opening']))
replace(account_opening,'181.25');click_text('Save Account')
assert repo.get_account('Cash')['opening_cents']==18125
click_text('New Account');assert app.account_vars['name'].get()==''
row(app.accounts_tree,'Cash');assert app.account_vars['name'].get()=='Cash'
ok('Account edit/save/new/reselect works')
click(app.nav_buttons['flows'])
# Modal time editor must dismiss a flow popup and must not restore it on return.
click(entry('category'));assert popups();click(app.time_button);d=modal()
assert not popups();key('Escape');pump(.2);clean_popups('Modal open/close leaves no suggestion popup')
assert not errors,errors
bad=[m for m in maps if m['override'] and m['geometry'].endswith('+0+0')]
assert not bad,bad
ok('Every observed custom popup first mapped at its final position')
# Native message boxes are answered through OS clicks, not mocked return values.
def answer_dialog(label):
    def send():
        def walk_tcl(path='.'):
            yield path
            for child in app.tk.splitlist(app.tk.call('winfo','children',path)):
                yield from walk_tcl(child)
        grabs=app.tk.splitlist(app.tk.call('grab','current'));grab=str(grabs[0]) if grabs else ''
        if not grab:
            app.after(80,send)
            return
        for path in walk_tcl(grab):
            try:
                caption=str(app.tk.call(path,'cget','-text'))
                if caption.casefold()!=label.casefold() or not int(app.tk.call('winfo','viewable',path)):continue
                px=int(app.tk.call('winfo','rootx',path))+int(app.tk.call('winfo','width',path))//2
                py=int(app.tk.call('winfo','rooty',path))+int(app.tk.call('winfo','height',path))//2
                xt.XTestFakeMotionEvent(display,0,px,py,0)
                xt.XTestFakeButtonEvent(display,1,True,0);xt.XTestFakeButtonEvent(display,1,False,0)
                x.XSync(display,False)
                return
            except tk.TclError:pass
        raise AssertionError('Could not find dialog button '+label)
    app.after(180,send)
row(app.ledger,b['flow_id']);enter('amount','41.01')
answer_dialog('Cancel');row(app.ledger,f['flow_id'])
assert app.editing_id==b['flow_id'] and app.form_vars['amount'].get()=='41.01'
assert app.ledger.selection()==(b['flow_id'],)
ok('Cancel row switch preserves draft and selection')
answer_dialog('No');row(app.ledger,f['flow_id'])
assert app.editing_id==f['flow_id'] and repo.get_flow(b['flow_id'])['amount_cents']==4000
ok('Discard row switch loads next record without saving draft')
enter('amount','100.00');answer_dialog('Yes');row(app.ledger,b['flow_id'])
assert repo.get_flow(f['flow_id'])['amount_cents']==10000 and app.editing_id==b['flow_id']
ok('Save row switch commits old record then loads chosen next record')
row(app.ledger,a['flow_id']);answer_dialog('Yes');click_text('Void')
assert repo.get_flow(a['flow_id'])['status']=='Void';cleared()
click(app.save_button);assert repo.get_flow(a['flow_id'])['status']=='Void'
clean_popups('Void clears stale editor; empty Save cannot resurrect record')
# Tooltip first mapping is positioned too; hover over an existing visible row.
box=app.ledger.bbox(b['flow_id'],'#1')
move(app.ledger.winfo_rootx()+box[0]+20,app.ledger.winfo_rooty()+box[1]+box[3]/2);pump(.6)
assert app.ledger_tooltip.popup is not None
bad=[m for m in maps if m['override'] and m['geometry'].endswith('+0+0')]
assert not bad,bad
ok('Native hover tooltip opens at its final position')
# Invalid Save keeps the draft, learns nothing, and can be corrected normally.
click_text('New Flow');enter('from_account','Checking');enter('to_account','Audit Utility')
enter('amount','invalid');enter('name','Corrected Audit Flow');enter('category','Only After Save')
count=len(repo.list_flows());click(app.save_button)
assert len(repo.list_flows())==count and app.form_vars['amount'].get()=='invalid'
assert 'Only After Save' not in repo.list_categories()
clean_popups('Invalid Save preserves draft without vocabulary side effects')
enter('amount','12.34');click(app.save_button)
c=flow_named('Corrected Audit Flow');assert c['amount_cents']==1234
cleared();ok('Corrected invalid entry saves normally')
row(app.ledger,c['flow_id']);enter('amount','55.00');click(app.cancel_button)
cleared();assert repo.get_flow(c['flow_id'])['amount_cents']==1234
ok('Cancel discards changed draft and clears every field')
# Filtering must not silently replace a dirty editor with a different record.
row(app.ledger,b['flow_id']);enter('amount','41.50')
search=next(w for w in widgets() if w.winfo_class()=='TEntry' and str(w.cget('textvariable'))==str(app.search_var))
replace(search,'electric')
assert app.editing_id==b['flow_id'] and app.form_vars['amount'].get()=='41.50'
click(app.save_button);assert repo.get_flow(b['flow_id'])['amount_cents']==4150
cleared();replace(search,'');assert b['flow_id'] in app.ledger_rows
ok('Filter refresh preserves unsaved draft; Save updates correct hidden ID')
# Actually click graph controls and the plot, verify selected account/result.
click(app.nav_buttons['charts']);click_text('None')
idx=next(i for i in range(app.chart_accounts.size()) if app.chart_accounts.get(i)=='Cash')
box=app.chart_accounts.bbox(idx);assert box
click_xy(app.chart_accounts.winfo_rootx()+20,app.chart_accounts.winfo_rooty()+box[1]+box[3]/2)
click_text('Update Graph');pump(.4)
assert [a['name'] for a in app.chart_inspection_context[0]]==['Cash']
chart=app.canvas.get_tk_widget()
click_xy(chart.winfo_rootx()+chart.winfo_width()*.55,chart.winfo_rooty()+chart.winfo_height()*.5)
assert 'Cash:' in app.chart_inspect.get(),app.chart_inspect.get()
ok('Graph account selection, Update Graph, and exact inspection work')
