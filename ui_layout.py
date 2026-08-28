"""Screen-aware geometry and scrollable pages for small screens / high DPI."""
from __future__ import annotations

import os
from tkinter import Canvas, PhotoImage, font
from tkinter import ttk


def choose_font(families, display=False):
    available = {name.casefold(): name for name in families}
    preferred = ("SF Pro Display", "SF UI Display") if display else ("SF Pro Text", "SF UI Text")
    for name in (*preferred, "SF Pro", "San Francisco", "Segoe UI", "Arial"):
        if name.casefold() in available:
            return available[name.casefold()]
    return "TkDefaultFont"


def configure_fonts(root):
    families = font.families(root)
    text_family, display_family = choose_font(families), choose_font(families, True)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        font.nametofont(name, root=root).configure(family=text_family)
    root.option_add("*Font", (text_family, 10))
    style = ttk.Style(root)
    style.configure(".", font=(text_family, 10))
    style.configure("Treeview", font=(text_family, 10), rowheight=max(24, font.Font(root=root, family=text_family, size=10).metrics("linespace") + 8))
    style.configure("Treeview.Heading", font=(text_family, 10, "bold"))
    return text_family, display_family


def desktop_work_area(root) -> tuple[int, int, int, int]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        monitor = user32.MonitorFromWindow(root.winfo_id(), 2)
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.work.left, info.work.top, info.work.right, info.work.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def window_dimensions(area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = area
    # Leave room for the native title bar, resize borders and desktop margins.
    width = min(820, max(1, right - left - 40))
    height = min(720, max(1, bottom - top - 80))
    return width, height, left + (right - left - width) // 2, top + 20


def fit_window(root) -> None:
    width, height, x, y = window_dimensions(desktop_work_area(root))
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.minsize(min(560, width), min(420, height))


class ScrollablePage(ttk.Frame):
    """Keep every control reachable, including when Windows scales the fonts."""

    def __init__(self, parent):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = Canvas(self, highlightthickness=0, borderwidth=0, takefocus=False)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set)
        self.body = ttk.Frame(self.canvas, padding=12)
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.pending = False
        self.body.bind("<Configure>", self._schedule_layout)
        self.canvas.bind("<Configure>", self._schedule_layout)
        # The toplevel bind tag receives events from the page's children as well.
        self.winfo_toplevel().bind("<MouseWheel>", self._mousewheel, add="+")
        self.winfo_toplevel().bind("<FocusIn>", self._reveal_focus, add="+")

    def _contains(self, widget) -> bool:
        return str(widget).startswith(str(self.body) + ".") or widget == self.body

    def _schedule_layout(self, _event=None):
        if not self.pending:
            self.pending = True
            self.after_idle(self._layout)

    def _layout(self):
        self.pending = False
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        needed_width, needed_height = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        self.canvas.itemconfigure(self.window, width=max(width, needed_width), height=max(height, needed_height))
        self.canvas.configure(scrollregion=(0, 0, max(width, needed_width), max(height, needed_height)))
        if needed_height > height:
            self.vertical.grid(row=0, column=1, sticky="ns")
        else:
            self.vertical.grid_remove()
            self.canvas.yview_moveto(0)
        if needed_width > width:
            self.horizontal.grid(row=1, column=0, sticky="ew")
        else:
            self.horizontal.grid_remove()
            self.canvas.xview_moveto(0)

    def _mousewheel(self, event):
        if not self._contains(event.widget) or not self.vertical.winfo_ismapped():
            return
        # Let editable text widgets / dropdowns keep their own wheel behavior.
        if event.widget.winfo_class() in {"Text", "TCombobox"}:
            return
        self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _reveal_focus(self, event):
        if not self._contains(event.widget) or not self.winfo_ismapped():
            return
        widget = event.widget
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        height = self.body.winfo_height()
        top = self.canvas.canvasy(0)
        if y < top:
            self.canvas.yview_moveto(max(0, y - 8) / height)
        elif y + widget.winfo_height() > top + self.canvas.winfo_height():
            self.canvas.yview_moveto((y + widget.winfo_height() + 8 - self.canvas.winfo_height()) / height)
        x = widget.winfo_rootx() - self.body.winfo_rootx()
        width = self.body.winfo_width()
        left = self.canvas.canvasx(0)
        if x < left:
            self.canvas.xview_moveto(max(0, x - 8) / width)
        elif x + widget.winfo_width() > left + self.canvas.winfo_width():
            self.canvas.xview_moveto((x + widget.winfo_width() + 8 - self.canvas.winfo_width()) / width)


def wrapping_label(parent, **kwargs):
    label = ttk.Label(parent, width=1, wraplength=480, justify="left", **kwargs)
    label.pack(fill="x", pady=(0, 8))
    label.bind("<Configure>", lambda event: label.configure(wraplength=max(1, event.width)))
    return label


def build_brand(parent, logo_path, family="Segoe UI"):
    brand = ttk.Frame(parent)
    # The PNG is the first frame of the site's animation: crop the symbol only.
    brand.source_image = PhotoImage(file=str(logo_path))
    brand.logo_image = PhotoImage(width=36, height=40)
    brand.logo_image.tk.call(
        brand.logo_image, "copy", brand.source_image,
        "-from", 58, 48, 305, 327, "-to", 0, 0, "-subsample", 7, 7,
    )
    ttk.Label(brand, image=brand.logo_image).pack(side="left")
    ttk.Label(brand, text="C² SISTEMAS", font=(family, 10, "bold"),
              foreground="#0b1730").pack(side="left", padx=(6, 0))
    return brand
