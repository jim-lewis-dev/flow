#!/usr/bin/env python3
"""Build the Flow specification and its readable Markdown source.

Run from anywhere: python3 docs/generate_spec.py
Requires ReportLab only. The shipped application does not require ReportLab.
"""
from pathlib import Path
from html import escape
import re

from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table,
    TableStyle, PageBreak, Flowable, KeepTogether,
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).resolve().parent
INK = colors.HexColor('#17313C')
TEAL = colors.HexColor('#007F7B')
MUTED = colors.HexColor('#526873')
PALE = colors.HexColor('#EAF5F3')
GRID = colors.HexColor('#D4DFE3')
AMBER = colors.HexColor('#A45B14')
WHITE = colors.white
FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')
if (FONT_DIR / 'DejaVuSans.ttf').exists():
    pdfmetrics.registerFont(TTFont('FlowSans', str(FONT_DIR / 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('FlowSans-Bold', str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
    pdfmetrics.registerFontFamily('FlowSans', normal='FlowSans', bold='FlowSans-Bold')
    FONT = 'FlowSans'
else:
    FONT = 'Helvetica'

styles = getSampleStyleSheet()
styles.add(ParagraphStyle('FlowTitle', fontName=FONT+'-Bold', fontSize=25, leading=30, textColor=INK, spaceAfter=12))
styles.add(ParagraphStyle('FlowSub', fontName=FONT+'-Bold', fontSize=12, leading=16, textColor=TEAL, spaceBefore=12, spaceAfter=6))
styles.add(ParagraphStyle('FlowBody', fontName=FONT, fontSize=9.2, leading=13.8, textColor=INK, spaceAfter=7))
styles.add(ParagraphStyle('FlowSmall', fontName=FONT, fontSize=8, leading=11.5, textColor=MUTED, spaceAfter=5))
styles.add(ParagraphStyle('FlowCell', fontName=FONT, fontSize=8.1, leading=11.7, textColor=INK))
styles.add(ParagraphStyle('FlowHead', fontName=FONT+'-Bold', fontSize=8.1, leading=11.7, textColor=WHITE))
styles.add(ParagraphStyle('FlowCode', fontName='Courier', fontSize=7.6, leading=10.5, textColor=INK, backColor=PALE, borderPadding=8, spaceAfter=8))

def rich(s):
    s = escape(str(s))
    s = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'`(.*?)`', r'<font face="Courier">\1</font>', s)
    return s.replace('\n', '<br/>')

def p(s, style='FlowBody'):
    return Paragraph(rich(s), styles[style])

def tbl(head, rows, widths):
    data = [[p(x, 'FlowHead') for x in head]]
    data.extend([[p(x, 'FlowCell') for x in row] for row in rows])
    t = Table(data, colWidths=widths, hAlign='LEFT', repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TEAL),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, colors.HexColor('#F3F7F8')]),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, TEAL),
        ('LINEBELOW', (0, 1), (-1, -1), 0.35, GRID),
    ]))
    return t

PAGES = []

def page(title, subtitle, blocks):
    PAGES.append((title, subtitle, blocks))

page('Objects and editing', '01 / Flow 0.5 - 9 October 2026', [
 ('p', 'A flow subtracts from one named account and adds to another. The application uses Python dictionaries and one SQLite repository. These are the actual objects returned by Repository.get_account() and get_flow().'),
 ('code', '{\n  "name": "AmEx",\n  "opening_cents": 0,\n  "opening_at": "2026-10-01 00:00:00"\n}'),
 ('code', '{\n  "flow_id": "11111111-1111-4111-8111-111111111111",\n  "date": "2026-10-02 12:30:00",\n  "from_account": "AmEx", "to_account": "Joe\'s Diner",\n  "amount_cents": 1200, "status": "Complete",\n  "name": "Friday lunch", "notes": "Burger and fries",\n  "category": "Dining", "tags": ["Road trip"]\n}'),
 ('table', ['Representation', 'Meaning'], [
   ['Account name', 'Its identity. Starting balance is integer cents, negative for debt, or null when unknown. New endpoint accounts start unknown.'],
   ['Amount / flow ID', 'Input .50 means 50 cents; stored 1200 means $12.00. Exact integer arithmetic. UUID preserves identity during edits/imports. Repeat creates a new UUID.'],
   ['Date', 'One Date/Time entry, in 24-hour format. Leading zeros optional. Missing month/day = 1; missing hour/minute/second = 0. Example: 2026-10-9 7:5 becomes 2026-10-09 07:05:00.'],
   ['Category / tags', 'One category and a list of tag names. Shared tags can connect split purchases. Nine flow fields plus ID; no scenario field.'],
 ], [113,411]),
 ('sub', 'The GUI edits these objects'),
 ('p', 'Date/Time, From, To, Amount, Status, Name, Notes, Category, Tags. Defaults: today, Complete and Uncategorized. Accounts has Name, Starting Balance and Starting Date/Time. No separate Time control.'),
 ('p', 'Existing records save on Enter, Tab or leaving a field. Ctrl+S finishes existing edits and leaves new drafts untouched. Notes: Enter adds a line; Ctrl+Enter or leaving/closing saves. Invalid edits stay unsaved with a silent error; Escape restores the saved value.'),
 ('p', 'New Flow clears; Add Flow inserts; Repeat copies every value. Empty ledger space deselects and clears, saving valid edits and discarding invalid/unadded drafts. Row/page changes discard new drafts; undo restores them. Drag table titles to move columns; cell editing and Tab follow their visible order.'),
 ('small', 'Repository-generated objects and rows: examples/record_objects.json and examples/review.sql. Launching and controls: README.md. Source walkthrough: docs/model_review.md. Forecast collections and simulation controls are deferred.'),
])

page('Storage and calculations', '02 / Schema 4 - six tables', [
 ('p', 'Named keys are name.strip().casefold(): AmEx becomes amex. They provide case-insensitive matching and foreign-key references, not separate generated identities. Display spelling stays in name.'),
 ('table', ['Table', 'Every stored column'], [
   ['accounts', 'account_key, name, opening_cents, opening_at'],
   ['flows', 'flow_id, date, from_key, to_key, amount_cents, status, name, notes, category_key'],
   ['categories', 'category_key, name'],
   ['tags', 'tag_key, name'],
   ['flow_tags', 'flow_id, tag_key, position'],
   ['audit', 'audit_id, changed_at, entity, entity_key, action, before_json, after_json'],
 ], [122,402]),
 ('p', 'Category/tag tables retain choices; flow_tags preserves assignments and order. audit stores before/after values. Account lists show only names used in saved From/To fields, including Void flows. Unreferenced starting balances remain stored for reuse/undo. Schema: flowledger/schema.sql.'),
 ('sub', 'Save and calculate'),
 ('p', 'GUI draft -> Repository.save_flow() -> validate -> write flow, choices, tags and audit atomically. Failed saves roll back. Ctrl+Z / Ctrl+Y undo records, drafts, filters, searches, dates, sorting, graph controls and column changes in action order. history.py captures changed rows; view_history.py keeps UI snapshots in memory. Record undo appends audit; closing/restoring clears history. No new stored fields.'),
 ('p', 'An account receives +amount when To and -amount when From. Confirmed includes Complete; Including Pending adds Pending; Including Planned also adds Planned. Void contributes zero. Settle Pending by editing that record; a refund is a new reverse flow.'),
 ('p', 'Each account has one confirmed starting balance immediately before flows at its timestamp. Earlier Complete flows are already reflected; eligible earlier Pending/Planned flows enter estimates once. Unknown balances remain unknown. Dates are points in local time, compared at second precision; date-only means midnight.'),
 ('p', 'Graphs compare or sum explicitly selected accounts. A transfer cancels within a sum containing both endpoints. Selecting every endpoint conserves the total; this is not automatically net worth. Category totals count each matching non-Void flow once, including transfers.'),
 ('sub', 'Upgrade and scope'),
 ('p', 'Schema 1-3 upgrades first save a full .pre-v4-timestamp.bak. Old Baseline flows stay active with their IDs; accounts and history remain. Old hypothetical collections remain in the backup and .bak.forecasts.json recovery file, outside the active ledger. Time flags become date precision, then disappear.'),
 ('small', 'Import/export: nine fields plus ID. Old scenario/time-flag imports are rejected; upgrade the database and export again. Use Backup for a complete snapshot. Scope: USD cents, one starting balance per account, local timestamps, one computer. Executed checks: docs/test_results.txt and docs/interaction_check.txt.'),
])

def page_footer(c, doc):
    c.saveState()
    c.setStrokeColor(GRID); c.setLineWidth(.6)
    c.line(44,43,568,43)
    c.setFillColor(MUTED); c.setFont(FONT,7.2)
    c.drawString(44,29,'FLOW / SOFTWARE SPECIFICATION / v0.5')
    c.drawRightString(568,29,f'{doc.page} / {len(PAGES)}')
    c.restoreState()

def build():
    story=[]
    md=['# Flow software specification','', 'Version 0.5 | 9 October 2026','']
    for n,(title,subtitle,blocks) in enumerate(PAGES):
        if n: story.append(PageBreak())
        story.append(p(subtitle.upper(),'FlowSmall'))
        story.append(p(title,'FlowTitle'))
        md += ['## '+title,'',subtitle,'']
        for block in blocks:
            kind=block[0]
            if kind in ('p','small','sub'):
                st={'p':'FlowBody','small':'FlowSmall','sub':'FlowSub'}[kind]
                story.append(p(block[1],st))
                md += [('### ' if kind=='sub' else '')+block[1],'']
            elif kind=='code':
                story.append(p(block[1],'FlowCode'))
                md += ['```text',block[1],'```','']
            elif kind=='table':
                _,heads,rows,widths=block
                story.append(tbl(heads,rows,widths)); story.append(Spacer(1,9))
                md += ['| '+' | '.join(heads)+' |','| '+' | '.join(['---']*len(heads))+' |']
                md += ['| '+' | '.join(row)+' |' for row in rows]
                md += ['']
    doc=BaseDocTemplate(str(ROOT/'Flow_Specification.pdf'), pagesize=(612,792),
                        title='Flow - Software Specification v0.5',author='Flow project',
                        leftMargin=44,rightMargin=44,topMargin=43,bottomMargin=57)
    doc.addPageTemplates(PageTemplate(id='standard',frames=[Frame(44,57,524,692,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=page_footer))
    doc.build(story)
    (ROOT/'specification.md').write_text('\n'.join(md),encoding='utf-8')
    print(ROOT/'Flow_Specification.pdf')

if __name__=='__main__':
    build()
