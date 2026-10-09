"""Session history for editable views, without adding fields to records."""
from contextlib import contextmanager


class ViewHistory:
    """Keep before/after snapshots beside the repository's undo actions.

    Widgets notify after a change, so each registered view keeps its last value.
    Restoring a snapshot suppresses widget callbacks. Consecutive keystrokes in
    one focused field form one action; another action or an undo breaks a group.
    """

    def __init__(self, app):
        self.app = app
        self.views = {}
        self.before = {}
        self.depth = 0
        self.gesture = 0

    @contextmanager
    def suspended(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def register(self, key, get, put):
        self.views[key] = (get, put)
        self.remember(key)

    def remember(self, key):
        if key in self.views:
            self.before[key] = self.views[key][0]()

    def begin(self, event=None):
        self.gesture += 1

    def watch_entry(self, widget):
        widget.bind("<FocusIn>", self.begin, add="+")

    def record(self, key, label, *, merge=None, before=None):
        if self.depth or self.app._loading or self.app._committing:
            return False
        after = self.views[key][0]()
        old = self.before[key] if before is None else before
        self.before[key] = after
        if old == after:
            return False

        def restore(value):
            with self.suspended():
                self.views[key][1](value)
                self.remember(key)

        changed = self.app.history.perform_external(
            label, lambda: True, lambda: restore(old), lambda: restore(after),
            merge_key=(key, merge, self.gesture) if merge is not None else None)
        self.app._draft_redo = None
        self.app._update_history_buttons()
        return changed
