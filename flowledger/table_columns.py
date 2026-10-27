"""Treeview header dragging and display-position to field-name mapping."""


def displayed_columns(tree):
    """Resolve Tk's #all/default and numeric display-column identifiers."""
    columns = tuple(tree["columns"])
    shown = tuple(tree["displaycolumns"])
    if shown == ("#all",):
        return columns
    return tuple(columns[int(key)] if str(key).isdigit() else str(key)
                 for key in shown)


def field_at(tree, x):
    position = tree.identify_column(x)
    if not position or position == "#0":
        return None
    columns = displayed_columns(tree)
    index = int(position[1:]) - 1
    return columns[index] if 0 <= index < len(columns) else None


def sort_rows(rows, key, reverse=False):
    """Sort by all keys; reverse only the chosen column, as in the ledger."""
    rows.sort(key=key)
    if reverse:
        rows.sort(key=lambda row: key(row)[0], reverse=True)


def enable_column_sort(tree, row_key):
    """Give a read-only table the ledger's ascending/reverse heading clicks."""
    column, reverse = None, False

    def sort(key):
        nonlocal column, reverse
        reverse = not reverse if column == key else False
        column = key
        rows = list(tree.get_children())
        sort_rows(rows, lambda row: row_key(row, key), reverse)
        for index, row in enumerate(rows):
            tree.move(row, "", index)
        if tree.focus():
            tree._multi_selection._see_row(tree.focus())

    for key in tree["columns"]:
        tree.heading(key, command=lambda k=key: sort(k))


class ColumnDrag:
    """Move headers after a short drag; keep ordinary clicks and resizing.

    Motion previews the order. Release records exactly one undo action. Cell
    values stay in their original schema order; only displaycolumns changes.
    """

    def __init__(self, tree, *, prepare, changed, click=None, resized=None):
        self.tree, self.prepare, self.changed, self.click = tree, prepare, changed, click
        self.source = None
        self.dragging = False
        self.resized = resized
        self.resize_before = None
        tree.bind("<ButtonPress-1>", self.press, add="+")
        tree.bind("<Double-1>", self.press, add="+")
        tree.bind("<Triple-1>", self.press, add="+")
        tree.bind("<B1-Motion>", self.move, add="+")
        tree.bind("<ButtonRelease-1>", self.release, add="+")

    def press(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "separator":
            self.prepare()
            self.resize_before = tuple((c, self.tree.column(c, "width")) for c in self.tree["columns"])
            return  # Let Tk resize the separator normally.
        if region != "heading":
            return
        self.prepare()
        self.source = field_at(self.tree, event.x)
        self.start_x = event.x
        self.before = displayed_columns(self.tree)
        self.dragging = False
        return "break"

    def move(self, event):
        if self.source is None:
            return
        if not self.dragging and abs(event.x - self.start_x) < 8:
            return "break"
        self.dragging = True
        self.tree.configure(cursor="fleur")
        target = field_at(self.tree, event.x)
        order = list(displayed_columns(self.tree))
        if target is not None and target != self.source:
            old, new = order.index(self.source), order.index(target)
            order.insert(new, order.pop(old))
            self.tree.configure(displaycolumns=order)
        return "break"

    def release(self, event):
        if self.resize_before is not None:
            before, self.resize_before = self.resize_before, None
            if self.resized is not None:
                self.resized(before)
            return  # Tk still owns the separator's release.
        if self.source is None:
            return
        source, dragging = self.source, self.dragging
        self.source = None
        self.tree.configure(cursor="")
        if dragging:
            if displayed_columns(self.tree) != self.before:
                self.changed(self.before)
        elif self.click is not None:
            self.click(source)
        return "break"
