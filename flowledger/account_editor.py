"""Inline views of the Accounts form's starting balance and date draft."""
from tkinter import ttk

from .ledger_editor import LedgerEditor


class AccountEditor(LedgerEditor):
    """Use the ledger editor lifecycle without making calculated cells editable."""

    view_name = "accounts"
    default_field = "balance"
    editable_columns = ("balance", "anchor")

    def __init__(self, app, *, font, background, foreground):
        super().__init__(app, None, tree=app.accounts_tree, font=font,
                         background=background, foreground=foreground)

    @property
    def draft_id(self):
        return self.app.account_editing

    def _select_draft(self, row):
        if self.tree.selection() != (row,):
            self.tree.selection_set(row)
        self.app._select_account()

    @property
    def record_kind(self):
        return "account"

    def variable_key(self, field):
        return "opening" if field == "balance" else "date"

    def _make_widget(self, field):
        key = "opening" if field == "balance" else "date"
        widget = ttk.Entry(self.tree, textvariable=self.app.account_vars[key],
                           width=1, style="Ledger.TEntry")
        self.input = widget
        return widget

    def _finish_field(self, field):
        if field == "anchor":
            self.app._normalize_date_field("account")
        return self.app._finish_existing("account")
