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
styles.add(ParagraphStyle('FlowBody', fontName=FONT, fontSize=9.2, leading=13.8, textColor=INK, spaceAfter=6))
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
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, colors.HexColor('#F3F7F8')]),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, TEAL),
        ('LINEBELOW', (0, 1), (-1, -1), 0.35, GRID),
    ]))
    return t

PAGES = []

def page(title, subtitle, blocks):
    PAGES.append((title, subtitle, blocks))

page('Objects and editing', '01 / Flow - 11 October 2026', [
 ('p', 'A flow subtracts a quantity from one named account and adds it to another, in one unit. Accounts can hold several units; each account/unit pair has its own starting balance and time.'),
 ('code', '{\n  "name": "Warehouse", "units": "kg",\n  "opening_amount": "20",\n  "opening_at": "2026-10-01 00:00:00"\n}'),
 ('code', '{\n  "flow_id": "22222222-2222-4222-8222-222222222222",\n  "date": "2026-10-03 09:15:06", "name": "Materials",\n  "from_account": "Warehouse", "to_account": "Workshop",\n  "amount": "0.125", "units": "kg", "status": "Complete",\n  "category": "Materials", "tags": ["Project"],\n  "notes": "Exact fractional quantity"\n}'),
 ('table', ['Representation', 'Meaning'], [
   ['Account / units', 'Account names identify holders. Unit labels are required and case-sensitive: mW and MW differ. No implicit conversion. Starting balance is signed decimal text, or null when unknown.'],
   ['Amount / flow ID', 'Exact decimal text, without a cents limit. .125 stores as 0.125; 12.00 stores as 12. UUID preserves edits/imports; Paste inserts a new UUID immediately.'],
   ['Date', 'One Date/Time entry, optional 24-hour time to seconds. Leading zeros optional. Missing month/day = 1; missing hour/minute/second = 0. Date-only means midnight.'],
   ['Category / tags', 'One category and a list of tag names. Shared tags can connect related transfers. Ten agreed flow fields plus ID.'],
 ], [113,411]),
 ('sub', 'The GUI edits these objects'),
 ('p', 'Default order: Date, Name, From, To, Amount, Units, Status, Category, Tags, Notes. Defaults: today, USD, Complete and Uncategorized. Units uses the learned dropdown with filtering, Enter and X. Accounts has Name, Units, Starting Balance and Starting Date/Time.'),
 ('p', 'Existing records save completed fields on Enter, Tab or leaving them. Ctrl+S finishes existing edits; new drafts wait for Add. Notes: Ctrl+Enter or leaving/closing saves. Invalid edits stay unsaved with a silent error. New clears; ledger Ctrl+C/V inserts a full copy under a new ID. Delete removes a selected row without confirmation; undo restores its record. Delete inside text edits text.'),
 ('p', 'Row/page changes and blank ledger clicks discard unadded drafts without prompts; undo restores them. Drag titles to move columns; cell editing and Tab follow their visible order. Account Name/Units identify a row; starting balance/date remain editable. Starting Balance enters with the caret at the end.'),
 ('p', 'Filters: From, To, Status, Categories, Tags and Units. Checkboxes select multiple values; hover lists every selection. Filter selections and choice search support session undo/redo. Unit matches keep exact case.'),
 ('small', 'Actual objects and SQL rows: examples/record_objects.json and examples/review.sql. Source walkthrough: docs/model_review.md. Controls and update/export commands: README.md.'),
])

page('Storage and calculations', '02 / Schema 6 - seven tables', [
 ('p', 'Account/category/tag keys use name.strip().casefold(), retaining display spelling. Unit labels keep exact case. Amounts are canonical decimal TEXT; Python Decimal arithmetic uses sufficient precision for every operand.'),
 ('table', ['Table', 'Every stored column'], [
   ['accounts', 'account_key, name'],
   ['account_balances', 'account_key, units, opening_amount, opening_at'],
   ['flows', 'flow_id, date, from_key, to_key, amount, units, status, name, notes, category_key'],
   ['categories', 'category_key, name'],
   ['tags', 'tag_key, name'],
   ['flow_tags', 'flow_id, tag_key, position'],
   ['audit', 'audit_id, changed_at, entity, entity_key, action, before_json, after_json'],
 ], [122,402]),
 ('p', 'account_balances is keyed by (account_key, units); each flow references matching endpoint/unit pairs. Lists show pairs used by saved flows, including Canceled. Unreferenced anchors remain stored for reuse/undo. audit retains before/after history. SQL: flowledger/schema.sql.'),
 ('sub', 'Save, calculate and inspect'),
 ('p', 'GUI draft -> Repository.save_flow() -> validate -> write flow, endpoint/unit anchors, choices, tags and audit atomically. Failed writes roll back. Ctrl+Z/Ctrl+Y reverse records/deletion, drafts, filters, searches, graph controls and all plot/column changes in one session order.'),
 ('p', 'Within its unit, a flow contributes +amount to To and -amount to From. Confirmed includes Complete; Including Pending adds Pending; Including Planned adds Planned. Canceled contributes zero. Each pair has a confirmed anchor immediately before events at its own timestamp; unknown remains unknown.'),
 ('p', 'Graph account/unit pairs separately. Combine sums each unit and names its members. Dates fit selected anchors and eligible flows; manual dates persist until Fit Dates. Controls and saved edits redraw. Each unit pads plotted values 10% without forcing zero. Wheel and toolbar zoom both ways. Hover shows exact values; drawing uses floating-point coordinates. Transfers cancel if both endpoints are selected. Readonly decimal display is configured in flowledger/units.py; stored quantities remain exact.'),
 ('p', 'Experimental animates ledger-filtered flows. First/last jump; previous/next step by timestamp; play/pause speed runs 0 to 5 (0 pauses). Hover pulses for flow details, accounts for saved anchors, balances from all flows and shown-flow In/Out totals. Canceled is excluded; node placement lasts this session. No data or schema changes.'),
 ('sub', 'Migration and scope'),
 ('p', 'Schema 5 -> 6 changes Void to Canceled in one transaction, without a backup file. Failure rolls back schema and data. IDs, quantities, anchors, metadata and audit remain exact. Older schema 1-4 conversions retain their one-time backup/recovery. Databases and backups stay out of Git and project snapshots.'),
 ('small', 'Import/export: ten fields plus ID; units required. Git rollback changes code, not records; schema 5 code cannot open schema 6. Scope: local timestamps and per-unit anchors on one computer; no automatic conversions or forecast collections.'),
])

def page_footer(c, doc):
    c.saveState()
    c.setStrokeColor(GRID); c.setLineWidth(.6)
    c.line(44,43,568,43)
    c.setFillColor(MUTED); c.setFont(FONT,7.2)
    c.drawString(44,29,'FLOW / SOFTWARE SPECIFICATION')
    c.drawRightString(568,29,f'{doc.page} / {len(PAGES)}')
    c.restoreState()

def build():
    story=[]
    md=['# Flow software specification','', '11 October 2026 | Schema 6','']
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
                        title='Flow - Software Specification',author='Flow project',
                        leftMargin=44,rightMargin=44,topMargin=43,bottomMargin=57)
    doc.addPageTemplates(PageTemplate(id='standard',frames=[Frame(44,57,524,692,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=page_footer))
    doc.build(story)
    (ROOT/'specification.md').write_text('\n'.join(md),encoding='utf-8')
    print(ROOT/'Flow_Specification.pdf')

if __name__=='__main__':
    build()
