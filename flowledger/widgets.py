"""Small reusable Tk widgets for keyboard-first flow entry."""
from __future__ import annotations

import csv
import io
import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable


class FilterCombo(ttk.Frame):
    """Editable suggestions: focus opens, typing filters, Enter accepts text.

    The popup never grabs the keyboard. Arrow navigation deliberately selects a
    suggestion; typing otherwise preserves the user's new value. Vocabulary is
    supplied by the repository only after a successful save.

    ``on_remove(value)`` persists removal from suggestions and returns True on
    success. Removing a suggestion never accepts or changes the current draft.
    """

    def __init__(self, master, variable=None, values: Iterable[str] = (),
                 on_accept: Callable[[], None] | None = None,
                 on_remove: Callable[[str], bool] | None = None, **kwargs):
        super().__init__(master, **kwargs)
        self.variable = variable or tk.StringVar(self)
        self.values = list(values)
        self.on_accept = on_accept
        self.on_remove = on_remove
        self.multi_value = False
        self.popup = None
        self.listbox = None
        self.remove_canvas = None
        self._remove_redraw_id = None
        self._remove_pressed = None
        self._show_all = False
        self.matches = []
        self.nav_index = None
        self._focus_check_id = None
        self._paste_id = None
        self._dismiss_focus_id = None
        self._suppress_focus_open = False
        self._dismissed = False
        self.entry = ttk.Entry(self, textvariable=self.variable)
        self.entry.pack(side="left", fill="x", expand=True)
        self.arrow = ttk.Button(self, text="▾", width=2, command=self._toggle_popup,
                                style="Small.TButton", takefocus=False)
        self.arrow.pack(side="right")
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<ButtonPress-1>", self._entry_click)
        self.entry.bind("<FocusOut>", self._defer_focus_check)
        self.entry.bind("<KeyRelease>", self._key_release)
        self.entry.bind("<Down>", lambda event: self._move(1))
        self.entry.bind("<Up>", lambda event: self._move(-1))
        self.entry.bind("<Return>", self._accept)
        self.entry.bind("<Escape>", self._escape)
        self.entry.bind("<Tab>", self._close_for_tab)
        self.entry.bind("<ISO_Left_Tab>", self._close_for_tab)
        self.entry.bind("<<Paste>>", self._paste, add="+")
        self.entry.bind("<<PasteSelection>>", self._paste, add="+")
        self.bind("<Destroy>", self._destroyed)
        self.bind("<Unmap>", lambda event: self.close_popup())
        top = self.winfo_toplevel()
        if not getattr(top, "_flow_filter_click_binding", None):
            top._flow_filter_click_binding = top.bind(
                "<ButtonPress-1>",
                lambda event: FilterCombo._dismiss_outside(event, top), add="+")

    @staticmethod
    def _dismiss_outside(event, top):
        current = getattr(top, "_flow_filter_combo", None)
        if current is None:
            return
        if event.widget in (current, current.entry, current.arrow):
            return
        popup = current.popup
        if popup is not None and (event.widget is popup or
                                  str(event.widget).startswith(str(popup) + ".")):
            return
        current.close_popup()

    def get(self):
        return self.variable.get()

    def set(self, value):
        self.variable.set(value)

    def set_values(self, values):
        self.values = list(dict.fromkeys(values))
        if self.popup and self.popup.winfo_exists():
            self._populate(preserve_scroll=True)

    def focus_set(self, *, open_popup=True):
        """Focus the input, optionally without opening suggestions.

        Form loading and saving should pass ``open_popup=False``. The one-shot
        suppression is consumed by FocusIn; a later mouse click or typed value
        still opens suggestions normally.
        """
        if open_popup:
            self._dismissed = False
        if str(self.tk.call("focus")) != str(self.entry):
            self._suppress_focus_open = not open_popup
            self.entry.focus_set()
        elif open_popup:
            self.open_popup(show_all=True)

    def _focus_in(self, event=None):
        suppress = self._suppress_focus_open
        self._suppress_focus_open = False
        if str(self.tk.call("focus")) != str(self.entry) or self._dismissed:
            return
        if self.popup is not None and self.popup.winfo_exists():
            return
        if not self.multi_value:
            self.entry.selection_range(0, "end")
        if not suppress:
            self.open_popup(show_all=True)

    def _entry_click(self, event=None):
        # Clicking an already focused input must reopen a dismissed dropdown.
        self._suppress_focus_open = False
        self.open_popup(show_all=True)

    def _toggle_popup(self):
        was_open = self.popup is not None
        self.focus_set(open_popup=False)
        if was_open:
            self.close_popup()
        else:
            self.open_popup(show_all=True)

    def _close_for_tab(self, event=None):
        self.close_popup()
        # Do not return "break": Entry's normal Tab traversal must still run.

    def _escape(self, event=None):
        if self.popup is not None:
            return self.close_popup()
        # Let the application handle Escape when there is no dropdown.

    def _paste(self, event=None):
        if self._paste_id is not None:
            self.after_cancel(self._paste_id)
        self._paste_id = self.after_idle(self._after_paste)

    def _after_paste(self):
        self._paste_id = None
        if str(self.tk.call("focus")) == str(self.entry):
            self.open_popup()

    def open_popup(self, show_all=False):
        if self.entry.instate(("disabled",)) or self.entry.instate(("readonly",)):
            self.close_popup()
            return
        if not self.winfo_viewable():
            return
        self._dismissed = False
        top = self.winfo_toplevel()
        calendar = getattr(top, "_flow_calendar", None)
        if calendar is not None:
            calendar.close_popup()
        check_filter = getattr(top, "_flow_check_filter", None)
        if check_filter is not None:
            check_filter.close_popup()
        previous = getattr(top, "_flow_filter_combo", None)
        if previous is not None and previous is not self:
            previous.close_popup()
        top._flow_filter_combo = self
        if self.popup is None or not self.popup.winfo_exists():
            self.popup = tk.Toplevel(self)
            if hasattr(top, "_bind_history_popup"):
                top._bind_history_popup(self.popup)
            # Never map an unpositioned popup. In particular, do not call
            # update/update_idletasks here: they can map it at (0, 0) and
            # dispatch unrelated callbacks while this widget is half built.
            self.popup.withdraw()
            self.popup.overrideredirect(True)
            self.popup.transient(self.winfo_toplevel())
            self.popup.bind("<FocusIn>", self._popup_focus_in)
            self.popup.configure(bg="#ccd9d6", padx=1, pady=1)
            body = tk.Frame(self.popup, bg="white")
            body.pack(fill="both", expand=True)
            self.listbox = tk.Listbox(body, bg="white", fg="#17352f",
                selectbackground="#d8eee7", selectforeground="#17352f",
                relief="flat", borderwidth=0, highlightthickness=0,
                activestyle="none", exportselection=False, takefocus=False, font=("DejaVu Sans", 10))
            self.listbox.pack(side="left", fill="both", expand=True)
            self.listbox.bind("<ButtonPress-1>", lambda event: "break")
            self.listbox.bind("<ButtonRelease-1>", self._click)
            if self.on_remove is not None:
                self.remove_canvas = tk.Canvas(body, width=28, height=1,
                    bg="white", borderwidth=0, highlightthickness=0, takefocus=False)
                self.remove_canvas.pack(side="right", fill="y")
                self.remove_canvas.bind("<ButtonPress-1>", self._remove_press)
                self.remove_canvas.bind("<ButtonRelease-1>", self._remove_release)
                self.remove_canvas.bind("<Motion>", self._remove_motion)
                self.remove_canvas.bind("<Leave>", lambda event: self.remove_canvas.configure(cursor=""))
                self.remove_canvas.bind("<Button-4>", lambda event: self._scroll_remove(-1))
                self.remove_canvas.bind("<Button-5>", lambda event: self._scroll_remove(1))
                self.remove_canvas.bind("<MouseWheel>", self._remove_wheel)
                self.remove_canvas.bind("<Configure>", self._queue_remove_redraw)
                self.listbox.bind("<Configure>", self._queue_remove_redraw)
                self.listbox.configure(yscrollcommand=self._queue_remove_redraw)
            hint = ("↑ ↓ choose · Enter accepts · × removes" if self.on_remove is not None
                    else "↑ ↓ choose   ·   Enter accepts   ·   Esc closes")
            self._hint = tk.Label(self.popup, text=hint,
                     bg="#f1f6f3", fg="#668079", font=("DejaVu Sans", 8),
                     anchor="w", padx=8, pady=4)
            self._hint.pack(fill="x")
        self.nav_index = None
        self._populate(show_all=show_all)
        self.popup.deiconify()

    def _popup_focus_in(self, event=None):
        # Suggestions are a mouse target, while typing belongs to the input.
        # Some window managers give an override-redirect popup pointer focus.
        # Redirect it without selecting text or rebuilding the open list.
        focused = str(self.tk.call("focus"))
        if self.popup is not None and (focused == str(self.popup) or
                                       focused.startswith(str(self.popup) + ".")):
            self.focus_set(open_popup=False)

    def _populate(self, show_all=None, *, preserve_scroll=False):
        if not self.popup or not self.popup.winfo_exists():
            return
        if show_all is not None:
            self._show_all = show_all
        top_index = self.listbox.nearest(0) if preserve_scroll and self.matches else 0
        query = "" if self._show_all else self._query().casefold()
        self.matches = [str(v) for v in self.values if query in str(v).casefold()]
        self.listbox.delete(0, "end")
        for value in self.matches[:80]:
            self.listbox.insert("end", value)
        self.matches = self.matches[:80]
        if not self.matches:
            self.listbox.insert("end", "New value — press Enter to use")
            self.listbox.itemconfig(0, fg="#668079")
        self.nav_index = None
        self.listbox.configure(height=max(1, min(6, len(self.matches))))
        if preserve_scroll:
            self.listbox.yview(top_index)
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(screen_width, max(self.winfo_width(), 300))
        # Widget geometry requests are synchronous; the packer is not. Use
        # the children's requests so placement never needs a nested event loop.
        height = min(screen_height, self.listbox.winfo_reqheight() + self._hint.winfo_reqheight() + 2)
        x = max(0, min(self.winfo_rootx(), screen_width - width))
        y = self.winfo_rooty() + self.winfo_height()
        if y + height > screen_height:
            y = self.winfo_rooty() - height
        y = max(0, min(y, screen_height - height))
        self.popup.geometry(f"{width}x{height}+{x}+{y}")
        self._queue_remove_redraw()

    def _queue_remove_redraw(self, *args):
        # The listbox reports scrolling before all geometry has settled. Draw
        # from its actual visible row bounds once; never remap the popup.
        if self.remove_canvas is not None and self._remove_redraw_id is None:
            self._remove_redraw_id = self.after_idle(self._draw_remove_buttons)

    def _draw_remove_buttons(self):
        self._remove_redraw_id = None
        canvas, listbox = self.remove_canvas, self.listbox
        if canvas is None or listbox is None:
            return
        canvas.delete("all")
        if not self.matches:
            return
        first = max(0, listbox.nearest(0))
        last = min(len(self.matches) - 1, listbox.nearest(listbox.winfo_height()))
        for index in range(first, last + 1):
            bounds = listbox.bbox(index)
            if bounds is not None:
                _, y, _, height = bounds
                canvas.create_text(canvas.winfo_width() / 2, y + height / 2,
                    text="×", fill="#668079", font=("DejaVu Sans", 11),
                    tags=("remove", f"remove_{index}"))

    def _remove_index(self, event):
        if self.listbox is None or not self.matches or self.on_remove is None:
            return None
        if not 0 <= event.x < self.remove_canvas.winfo_width():
            return None
        index = self.listbox.nearest(event.y)
        bounds = self.listbox.bbox(index)
        if bounds is not None and 0 <= index < len(self.matches):
            _, y, _, height = bounds
            if y <= event.y < y + height:
                return index
        return None

    def _remove_motion(self, event):
        self.remove_canvas.configure(cursor="hand2" if self._remove_index(event) is not None else "")

    def _remove_press(self, event):
        index = self._remove_index(event)
        self._remove_pressed = self.matches[index] if index is not None else None
        return "break"

    def _remove_release(self, event):
        index = self._remove_index(event)
        value = self.matches[index] if index is not None else None
        pressed, self._remove_pressed = self._remove_pressed, None
        if value is not None and value == pressed:
            # The callback owns persistence and can refresh this combo itself.
            # A rejected removal must leave both vocabulary and draft intact.
            if self.on_remove(value):
                self.set_values(v for v in self.values if v != value)
            self.focus_set(open_popup=False)
        return "break"

    def _scroll_remove(self, delta):
        if self.listbox is not None:
            self.listbox.yview_scroll(delta, "units")
        return "break"

    def _remove_wheel(self, event):
        # Windows uses multiples of 120; macOS can send small deltas.
        if event.delta:
            self._scroll_remove(-int(event.delta / 120) if abs(event.delta) >= 120
                                else (-1 if event.delta > 0 else 1))
        return "break"

    def _query(self):
        if not self.multi_value:
            return self.get().strip()
        try:
            parts = next(csv.reader([self.get()], skipinitialspace=True))
            return parts[-1].strip() if parts else ""
        except csv.Error:
            return self.get().rsplit(",", 1)[-1].strip()

    def _replace_value(self, value):
        if self.entry.instate(("disabled",)) or self.entry.instate(("readonly",)):
            return
        if not self.multi_value:
            self.variable.set(value)
            return
        try:
            parts = next(csv.reader([self.get()], skipinitialspace=True))
        except csv.Error:
            parts = []
        values = [part.strip() for part in parts[:-1] if part.strip()] + [value]
        stream = io.StringIO()
        csv.writer(stream, lineterminator="").writerow(values)
        self.variable.set(stream.getvalue())

    def _key_release(self, event):
        # A command such as Ctrl+S can move focus back here while its key is
        # still held. Its release is not text entry and must not open a popup.
        if str(self.tk.call("focus")) != str(self.entry) or event.state & (0x0004 | 0x0008 | 0x0040):
            return
        ignored = {"Up", "Down", "Left", "Right", "Home", "End", "Prior", "Next",
                   "Return", "KP_Enter", "Escape", "Tab", "ISO_Left_Tab", "Insert",
                   "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                   "Meta_L", "Meta_R", "Super_L", "Super_R", "Caps_Lock", "Num_Lock"}
        if event.keysym in ignored or (event.keysym.startswith("F") and event.keysym[1:].isdigit()):
            return
        self.nav_index = None
        self.open_popup()

    def _move(self, delta):
        if not self.popup:
            self.open_popup()
        if self.matches and self.listbox is not None:
            self.nav_index = (0 if delta > 0 else len(self.matches)-1) if self.nav_index is None else max(0, min(len(self.matches)-1, self.nav_index+delta))
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.nav_index)
            self.listbox.see(self.nav_index)
        return "break"

    def _click(self, event):
        if self.matches:
            index = self.listbox.nearest(event.y)
            self._replace_value(self.matches[index])
        self.close_popup()
        self.focus_set(open_popup=False)
        if self.on_accept:
            self.on_accept()
        return "break"

    def _accept(self, event=None):
        if self.nav_index is not None and self.nav_index < len(self.matches):
            self._replace_value(self.matches[self.nav_index])
        self.close_popup()
        if self.on_accept:
            self.on_accept()
        return "break"

    def _defer_focus_check(self, event=None):
        if self._focus_check_id is not None:
            self.after_cancel(self._focus_check_id)
        self._focus_check_id = self.after_idle(self._check_focus)

    def _check_focus(self, allow_pointer_grace=True):
        self._focus_check_id = None
        try:
            # Native ttk popdowns exist only in Tcl, outside tkinter's widget
            # registry. Compare their paths without resolving widget objects.
            focused = str(self.tk.call("focus"))
            if not focused and self.popup is not None:
                # X can report no focus between leaving the input and entering
                # the popup. The pointer already owns the suggestion window;
                # do not destroy it before its FocusIn notification arrives.
                pointed = str(self.tk.call("winfo", "containing", *self.winfo_pointerxy()))
                if allow_pointer_grace and (pointed == str(self.popup) or
                        pointed.startswith(str(self.popup) + ".")):
                    # If focus remains outside this app, close after a brief
                    # transfer grace period even with the pointer still here.
                    self._focus_check_id = self.after(60, lambda: self._check_focus(False))
                    return
            # Override-redirect windows can receive pointer focus themselves
            # (not just their listbox). Destroying one in that state returns
            # focus to the entry and can cause an endless destroy/reopen loop.
            in_popup = self.popup is not None and (
                focused == str(self.popup) or focused.startswith(str(self.popup) + "."))
            if focused != str(self.entry) and not in_popup:
                self.close_popup()
                if focused:
                    self._dismissed = False
        except tk.TclError:
            pass

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self.close_popup()

    def _cancel_callbacks(self):
        for after_id in (self._focus_check_id, self._paste_id, self._dismiss_focus_id,
                         self._remove_redraw_id):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
        self._focus_check_id = self._paste_id = self._dismiss_focus_id = None
        self._remove_redraw_id = None

    def _finish_dismissal(self):
        self._dismiss_focus_id = None
        self._suppress_focus_open = False

    def close_popup(self):
        # A view transition can dismiss the active combo, then dismiss every
        # combo again. Preserve cleanup of the first dismissal's focus guard.
        needs_focus_cleanup = self._suppress_focus_open
        self._cancel_callbacks()
        try:
            top = self.winfo_toplevel()
            if getattr(top, "_flow_filter_combo", None) is self:
                top._flow_filter_combo = None
        except tk.TclError:
            pass
        had_popup = self.popup is not None
        if had_popup:
            # Stay dismissed until focus actually leaves, or the user clicks,
            # types, or requests suggestions. X focus-return notifications may
            # arrive after idle callbacks, so elapsed time is not a boundary.
            self._dismissed = True
            # Destroying a window beneath the pointer can deliver FocusIn to
            # the entry, even though this is dismissal rather than new entry.
            self._suppress_focus_open = True
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
        self.popup = None
        self.listbox = None
        self.remove_canvas = None
        self._remove_pressed = None
        self.matches = []
        self.nav_index = None
        if (had_popup or needs_focus_cleanup) and self.winfo_exists():
            self._dismiss_focus_id = self.after_idle(self._finish_dismissal)
        return "break"


class TreeTooltip:
    """Show row details on hover without widening a Treeview column.

    Keep the returned object if callers need to call ``hide()`` on a view change.
    ``text_for_item(item_id)`` returns the text, or an empty value for no tooltip.
    The callback runs when the tooltip opens, so edited rows use current data.
    """

    def __init__(self, tree: ttk.Treeview,
                 text_for_item: Callable[[str], str | None], delay: int = 450):
        self.tree = tree
        self.text_for_item = text_for_item
        self.delay = delay
        self.popup = None
        self._after_id = None
        self._item = None
        self._position = (0, 0)
        tree.bind("<Motion>", self._motion, add="+")
        for sequence in ("<Leave>", "<ButtonPress>", "<MouseWheel>",
                         "<Button-4>", "<Button-5>", "<Unmap>"):
            tree.bind(sequence, self.hide, add="+")
        tree.bind("<Destroy>", self._destroyed, add="+")

    def _motion(self, event):
        item = self.tree.identify_row(event.y)
        self._position = (event.x_root + 14, event.y_root + 20)
        if item == self._item:
            return
        self.hide()
        if item:
            self._item = item
            self._after_id = self.tree.after(self.delay, self._show)

    def _show(self):
        self._after_id = None
        if not self._item or not self.tree.winfo_viewable() or not self.tree.exists(self._item):
            return
        text = self.text_for_item(self._item)
        if not text:
            return
        self.popup = tk.Toplevel(self.tree)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.transient(self.tree.winfo_toplevel())
        label = tk.Label(self.popup, text=text, justify="left", anchor="w",
                 bg="#213b35", fg="#ffffff", padx=10, pady=7,
                 font=("DejaVu Sans", 9), wraplength=500)
        label.pack()
        width, height = label.winfo_reqwidth(), label.winfo_reqheight()
        x = max(0, min(self._position[0], self.tree.winfo_screenwidth() - width))
        y = max(0, min(self._position[1], self.tree.winfo_screenheight() - height))
        self.popup.geometry(f"{width}x{height}+{x}+{y}")
        self.popup.deiconify()

    def hide(self, event=None):
        if self._after_id is not None:
            try:
                self.tree.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None
        self._item = None

    def _destroyed(self, event):
        if event.widget is self.tree:
            self.hide()


class ScrollFrame(ttk.Frame):
    """Vertically scroll a form while keeping its width responsive."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#ffffff")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame")
        self.window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self._wheel_bound = False
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, event):
        if self._wheel_bound:
            return
        self._wheel_bound = True
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")
        self.canvas.bind_all("<Button-4>", self._wheel, add="+")
        self.canvas.bind_all("<Button-5>", self._wheel, add="+")

    def _unbind_wheel(self, event):
        # Bind to the toplevel rather than removing other widgets' global handlers.
        pass

    def _wheel(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
            if widget and str(widget).startswith(str(self)):
                delta = -1 if getattr(event, "num", 0) == 4 or getattr(event, "delta", 0) > 0 else 1
                self.canvas.yview_scroll(delta, "units")
                return "break"
        except tk.TclError:
            pass
