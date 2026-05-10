"""
Spreadsheet-style keyboard range selection for ttk.Treeview widgets.

State model
-----------
_focused_iid : str | None  — item where the keyboard cursor currently is
_anchor_iid  : str | None  — item where SHIFT range selection started
_shift_held  : bool         — True while Shift is physically down (used only
                               to set the anchor on the first shift+arrow)

Rules
-----
1. Arrow without Shift  → move focus, select only that row, reset anchor.
2. First Shift+Arrow    → anchor = current focused row, then extend.
3. Shift+Arrow          → move focus, selected = range(anchor, focused).
4. Shrinking is correct: range is always recomputed from anchor→focus.
5. Both directions work (anchor can be above or below focused).
6. After Shift is released, the next plain Arrow returns to single-select.
"""

from __future__ import annotations

from tkinter import ttk


class _KeyboardRangeSelector:
    """Manages spreadsheet-style keyboard range selection for one Treeview."""

    # Tkinter event.state bit for Shift
    _SHIFT_MASK = 0x0001

    def __init__(self, tree: ttk.Treeview) -> None:
        self._tree = tree
        self._focused_iid: str | None = None
        self._anchor_iid:  str | None = None

        for seq in ("<Up>", "<Down>", "<Shift-Up>", "<Shift-Down>"):
            tree.bind(seq, self._on_arrow, add="+")

    # ------------------------------------------------------------------ #
    # Public helpers                                                       #
    # ------------------------------------------------------------------ #

    def sync_focus_from_selection(self) -> None:
        """Call this after programmatically changing the selection so that
        the internal focused-item pointer stays consistent."""
        sel = self._tree.selection()
        if sel:
            self._focused_iid = sel[0]
            self._anchor_iid  = sel[0]

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _resolve_focused(self) -> str | None:
        """Return the current focused item, falling back to selection or first child."""
        children = self._tree.get_children()
        if not children:
            return None

        if self._focused_iid and self._focused_iid in children:
            return self._focused_iid

        # Initialise from Treeview's own focus indicator or selection
        native_focus = self._tree.focus()
        if native_focus and native_focus in children:
            return native_focus

        sel = self._tree.selection()
        if sel and sel[0] in children:
            return sel[0]

        return children[0]

    def _on_arrow(self, event) -> str:
        tree = self._tree
        children = list(tree.get_children())
        if not children:
            return "break"

        is_shift = bool(event.state & self._SHIFT_MASK)
        direction = -1 if event.keysym in ("Up",) else 1

        focused = self._resolve_focused()
        if focused is None:
            return "break"

        try:
            idx = children.index(focused)
        except ValueError:
            idx = 0

        next_idx = max(0, min(len(children) - 1, idx + direction))
        next_iid = children[next_idx]

        if not is_shift:
            # Rule 1 — plain navigation: single-select, reset anchor
            self._focused_iid = next_iid
            self._anchor_iid  = next_iid
            tree.selection_set(next_iid)
            tree.focus(next_iid)
            tree.see(next_iid)
        else:
            # Rule 2 — set anchor on the first shift+arrow if not set yet
            if self._anchor_iid is None or self._anchor_iid not in children:
                self._anchor_iid = focused

            # Rules 3-5 — move focus and recompute the whole range
            self._focused_iid = next_iid
            tree.focus(next_iid)

            anchor_idx  = children.index(self._anchor_iid)
            focused_idx = children.index(self._focused_iid)

            start = min(anchor_idx, focused_idx)
            end   = max(anchor_idx, focused_idx)
            selected_range = children[start : end + 1]

            tree.selection_set(*selected_range)
            tree.see(next_iid)

        return "break"


def attach_keyboard_range_selection(tree: ttk.Treeview) -> _KeyboardRangeSelector:
    """
    Attach spreadsheet-style keyboard range selection to *tree* and return
    the selector instance (useful for calling ``sync_focus_from_selection``
    after programmatic selection changes).
    """
    return _KeyboardRangeSelector(tree)
