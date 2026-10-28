"""Screen-aware geometry and scrollable pages for small screens / high DPI."""
from __future__ import annotations

import os
from tkinter import Canvas, PhotoImage, Toplevel, font
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
    configure_modern_styles(root, text_family, display_family)
    return text_family, display_family


# Deemix-Inspired Dark Obsidian Theme Palette (Extracted directly from Deemix GUI bundle.js)
DEEMIX_DARK_BG = "#0e0e13"          # hsl(240, 10%, 8%) - Fundo principal da aplicação
DEEMIX_SIDEBAR_BG = "#161622"       # hsl(240, 15%, 12%) - Painel lateral e cabeçalhos
DEEMIX_CARD_BG = "#1b1b26"          # hsl(240, 14%, 13%) - Cartões e painéis elevados
DEEMIX_INPUT_BG = "#12121a"         # Campos de entrada e caixas de texto
DEEMIX_CARD_BORDER = "#2a2a3c"      # Bordas sutis dos elementos
DEEMIX_CARD_HOVER = "#262638"       # hsl(240, 10%, 22%) - Destaque ao passar o mouse
DEEMIX_TEXT_PRIMARY = "#f3f4f6"     # hsl(0, 0%, 93%) - Texto principal de alto contraste
DEEMIX_TEXT_SECONDARY = "#9ca3af"   # Textos secundários, subtítulos e metadados
DEEMIX_TEXT_MUTED = "#64748b"       # Dicas e placeholders
DEEMIX_BLUE = "#0a84ff"             # hsl(210, 100%, 52%) - Azul elétrico assinatura Deemix
DEEMIX_BLUE_HOVER = "#0070e0"
DEEMIX_BLUE_ACTIVE = "#005bb5"
DEEMIX_GOLD = "#ffd024"             # hsl(46, 100%, 57%) - Destaques VIP / explícito / avisos
DEEMIX_GREEN = "#10b981"            # Indicadores de sucesso / status ativo
DEEMIX_SELECTION = "#1d3557"        # Seleção de linhas em tabelas

# Aliases de compatibilidade retroativa
APPLE_DARK_BG = DEEMIX_DARK_BG
APPLE_SIDEBAR_BG = DEEMIX_SIDEBAR_BG
APPLE_CARD_BG = DEEMIX_CARD_BG
APPLE_CARD_BORDER = DEEMIX_CARD_BORDER
APPLE_CARD_HOVER = DEEMIX_CARD_HOVER
APPLE_TEXT_PRIMARY = DEEMIX_TEXT_PRIMARY
APPLE_TEXT_SECONDARY = DEEMIX_TEXT_SECONDARY
APPLE_TEXT_MUTED = DEEMIX_TEXT_MUTED
APPLE_GREEN = DEEMIX_GREEN
APPLE_ORANGE = DEEMIX_GOLD
APPLE_BLUE = DEEMIX_BLUE
APPLE_SELECTION = DEEMIX_SELECTION
PROGRAM_BLUE = DEEMIX_BLUE
PROGRAM_BLUE_HOVER = DEEMIX_BLUE_HOVER
PROGRAM_BLUE_ACTIVE = DEEMIX_BLUE_ACTIVE
PROGRAM_BLUE_SUBTLE = "#1e293b"
APPLE_RED = PROGRAM_BLUE
APPLE_RED_HOVER = PROGRAM_BLUE_HOVER
APPLE_RED_ACTIVE = PROGRAM_BLUE_ACTIVE
APPLE_RED_SUBTLE = PROGRAM_BLUE_SUBTLE


def configure_modern_styles(root, text_family: str, display_family: str) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    try:
        root.configure(background=DEEMIX_DARK_BG)
        root.option_add("*TCombobox*Listbox*background", DEEMIX_CARD_BG)
        root.option_add("*TCombobox*Listbox*foreground", DEEMIX_TEXT_PRIMARY)
        root.option_add("*TCombobox*Listbox*selectBackground", DEEMIX_BLUE)
        root.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
        root.option_add("*Menu*background", DEEMIX_CARD_BG)
        root.option_add("*Menu*foreground", DEEMIX_TEXT_PRIMARY)
        root.option_add("*Menu*activeBackground", DEEMIX_BLUE)
        root.option_add("*Menu*activeForeground", "#ffffff")
    except Exception:
        pass

    # Base style: eliminate all default clam white/beige bevels (lightcolor/darkcolor)
    style.configure(
        ".",
        background=DEEMIX_DARK_BG,
        foreground=DEEMIX_TEXT_PRIMARY,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
        focuscolor=DEEMIX_DARK_BG,
        troughcolor=DEEMIX_DARK_BG,
        font=(text_family, 10),
    )

    row_height = max(30, font.Font(root=root, family=text_family, size=10).metrics("linespace") + 12)
    style.configure(
        "Treeview",
        background=DEEMIX_INPUT_BG,
        fieldbackground=DEEMIX_INPUT_BG,
        foreground=DEEMIX_TEXT_PRIMARY,
        font=(text_family, 10),
        rowheight=row_height,
        borderwidth=1,
        relief="solid",
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
    )
    style.configure(
        "Treeview.Heading",
        background=DEEMIX_SIDEBAR_BG,
        foreground=DEEMIX_TEXT_SECONDARY,
        font=(text_family, 9, "bold"),
        relief="flat",
        borderwidth=1,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
        padding=[8, 5],
    )
    style.map(
        "Treeview",
        background=[("selected", DEEMIX_SELECTION)],
        foreground=[("selected", "#ffffff")],
    )
    style.map(
        "Treeview.Heading",
        background=[("active", DEEMIX_CARD_HOVER)],
        foreground=[("active", "#ffffff")],
    )

    # Frame & Surface Styles
    style.configure("TFrame", background=DEEMIX_DARK_BG, borderwidth=0, relief="flat")
    style.configure("Sidebar.TFrame", background=DEEMIX_SIDEBAR_BG, borderwidth=0, relief="flat")
    style.configure("Header.TFrame", background=DEEMIX_SIDEBAR_BG, borderwidth=0, relief="flat")
    style.configure(
        "Card.TFrame",
        background=DEEMIX_CARD_BG,
        relief="solid",
        borderwidth=1,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
    )
    style.configure("CardInner.TFrame", background=DEEMIX_CARD_BG, relief="flat", borderwidth=0)
    style.configure(
        "CoverBox.TFrame",
        background=DEEMIX_INPUT_BG,
        relief="solid",
        borderwidth=1,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
    )

    # Label Styles
    style.configure("TLabel", background=DEEMIX_DARK_BG, foreground=DEEMIX_TEXT_PRIMARY)
    style.configure("Card.TLabel", background=DEEMIX_CARD_BG, foreground=DEEMIX_TEXT_PRIMARY)
    style.configure("CardMuted.TLabel", background=DEEMIX_CARD_BG, foreground=DEEMIX_TEXT_SECONDARY)
    style.configure("Cover.TLabel", background=DEEMIX_INPUT_BG, foreground=DEEMIX_TEXT_MUTED)
    style.configure("Muted.TLabel", background=DEEMIX_DARK_BG, foreground=DEEMIX_TEXT_SECONDARY)
    style.configure("Sidebar.TLabel", background=DEEMIX_SIDEBAR_BG, foreground=DEEMIX_TEXT_PRIMARY)
    style.configure("SidebarMuted.TLabel", background=DEEMIX_SIDEBAR_BG, foreground=DEEMIX_TEXT_SECONDARY)
    style.configure(
        "SidebarSection.TLabel",
        background=DEEMIX_SIDEBAR_BG,
        foreground="#64748b",
        font=(text_family, 8, "bold"),
    )
    for lbl_style, lbl_bg in (
        ("TLabel", DEEMIX_DARK_BG),
        ("Muted.TLabel", DEEMIX_DARK_BG),
        ("Card.TLabel", DEEMIX_CARD_BG),
        ("CardMuted.TLabel", DEEMIX_CARD_BG),
        ("Cover.TLabel", DEEMIX_INPUT_BG),
        ("Sidebar.TLabel", DEEMIX_SIDEBAR_BG),
        ("SidebarMuted.TLabel", DEEMIX_SIDEBAR_BG),
    ):
        style.map(
            lbl_style,
            background=[("disabled", lbl_bg), ("active", lbl_bg)],
            foreground=[("disabled", DEEMIX_TEXT_MUTED)],
        )

    # LabelFrame Styles: uniform card header + 1px divider below title
    style.configure(
        "TLabelframe",
        background=DEEMIX_CARD_BG,
        foreground=DEEMIX_TEXT_PRIMARY,
        relief="solid",
        borderwidth=1,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
        labeloutside=True,
        labelmargins=(10, 4, 10, 2),
    )
    style.configure(
        "TLabelframe.Label",
        background=DEEMIX_CARD_BG,
        foreground=DEEMIX_BLUE,
        font=(text_family, 9, "bold"),
        padding=(2, 0),
    )

    # Deemix-style hidden tab bar for main navigation notebook
    style.layout("Deemix.TNotebook.Tab", [])
    style.configure(
        "Deemix.TNotebook",
        background=DEEMIX_DARK_BG,
        tabmargins=[0, 0, 0, 0],
        borderwidth=0,
        lightcolor=DEEMIX_DARK_BG,
        darkcolor=DEEMIX_DARK_BG,
    )

    style.configure(
        "TNotebook",
        background=DEEMIX_DARK_BG,
        tabmargins=[2, 4, 2, 0],
        borderwidth=0,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
    )
    style.configure(
        "TNotebook.Tab",
        background=DEEMIX_SIDEBAR_BG,
        foreground=DEEMIX_TEXT_SECONDARY,
        font=(text_family, 9, "bold"),
        padding=[10, 5],
        borderwidth=1,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", DEEMIX_CARD_BG), ("active", DEEMIX_CARD_HOVER)],
        foreground=[("selected", DEEMIX_BLUE), ("active", DEEMIX_TEXT_PRIMARY)],
    )

    # Entry, Combobox & Spinbox (No white inner borders!)
    style.configure(
        "TEntry",
        fieldbackground=DEEMIX_INPUT_BG,
        background=DEEMIX_INPUT_BG,
        foreground=DEEMIX_TEXT_PRIMARY,
        insertcolor=DEEMIX_TEXT_PRIMARY,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
        padding=[8, 5],
        relief="solid",
        borderwidth=1,
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", "#0c0c12"), ("readonly", DEEMIX_INPUT_BG)],
        foreground=[("disabled", DEEMIX_TEXT_MUTED)],
        bordercolor=[("focus", DEEMIX_BLUE)],
        lightcolor=[("focus", DEEMIX_BLUE)],
        darkcolor=[("focus", DEEMIX_BLUE)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=DEEMIX_INPUT_BG,
        background=DEEMIX_CARD_HOVER,
        foreground=DEEMIX_TEXT_PRIMARY,
        arrowcolor=DEEMIX_TEXT_SECONDARY,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
        padding=[8, 4],
        relief="solid",
        borderwidth=1,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", DEEMIX_INPUT_BG), ("disabled", "#0c0c12")],
        background=[("active", DEEMIX_CARD_BORDER), ("readonly", DEEMIX_CARD_HOVER)],
        foreground=[("readonly", DEEMIX_TEXT_PRIMARY), ("disabled", DEEMIX_TEXT_MUTED)],
        selectbackground=[("readonly", DEEMIX_INPUT_BG)],
        selectforeground=[("readonly", DEEMIX_TEXT_PRIMARY)],
        bordercolor=[("focus", DEEMIX_BLUE)],
        lightcolor=[("focus", DEEMIX_BLUE)],
        darkcolor=[("focus", DEEMIX_BLUE)],
    )

    style.configure(
        "TSpinbox",
        fieldbackground=DEEMIX_INPUT_BG,
        background=DEEMIX_CARD_HOVER,
        foreground=DEEMIX_TEXT_PRIMARY,
        insertcolor=DEEMIX_TEXT_PRIMARY,
        arrowcolor=DEEMIX_TEXT_SECONDARY,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=DEEMIX_CARD_BORDER,
        darkcolor=DEEMIX_CARD_BORDER,
        padding=[6, 3],
        relief="solid",
        borderwidth=1,
    )
    style.map(
        "TSpinbox",
        fieldbackground=[("disabled", "#0c0c12"), ("readonly", DEEMIX_INPUT_BG)],
        foreground=[("disabled", DEEMIX_TEXT_MUTED)],
        bordercolor=[("focus", DEEMIX_BLUE)],
        lightcolor=[("focus", DEEMIX_BLUE)],
        darkcolor=[("focus", DEEMIX_BLUE)],
    )

    # Checkbuttons
    style.configure(
        "TCheckbutton",
        background=DEEMIX_CARD_BG,
        foreground=DEEMIX_TEXT_PRIMARY,
        indicatorbackground=DEEMIX_INPUT_BG,
        indicatorforeground=DEEMIX_BLUE,
        upperbordercolor=DEEMIX_CARD_BORDER,
        lowerbordercolor=DEEMIX_CARD_BORDER,
        focuscolor="",
    )
    style.map(
        "TCheckbutton",
        background=[("active", DEEMIX_CARD_BG)],
        foreground=[("active", DEEMIX_TEXT_PRIMARY), ("disabled", DEEMIX_TEXT_MUTED)],
        indicatorbackground=[("selected", DEEMIX_BLUE), ("active", DEEMIX_CARD_HOVER)],
    )
    style.configure(
        "Dark.TCheckbutton",
        background=DEEMIX_DARK_BG,
        foreground=DEEMIX_TEXT_PRIMARY,
        indicatorbackground=DEEMIX_INPUT_BG,
        indicatorforeground=DEEMIX_BLUE,
        focuscolor="",
    )
    style.map(
        "Dark.TCheckbutton",
        background=[("active", DEEMIX_DARK_BG)],
        foreground=[("active", DEEMIX_TEXT_PRIMARY)],
    )

    # Buttons
    style.configure(
        "TButton",
        background="#232334",
        foreground=DEEMIX_TEXT_PRIMARY,
        bordercolor="#323248",
        lightcolor="#323248",
        darkcolor="#323248",
        font=(text_family, 9),
        padding=(9, 4),
        relief="solid",
        borderwidth=1,
        focuscolor="",
    )
    style.map(
        "TButton",
        background=[("pressed", "#191926"), ("active", "#2e2e44"), ("disabled", "#171722")],
        foreground=[("disabled", "#4b5563")],
        bordercolor=[("active", "#3e3e58"), ("disabled", "#222230")],
        lightcolor=[("active", "#3e3e58"), ("disabled", "#222230")],
        darkcolor=[("active", "#3e3e58"), ("disabled", "#222230")],
    )

    style.configure(
        "Accent.TButton",
        background=PROGRAM_BLUE,
        foreground="#ffffff",
        bordercolor=PROGRAM_BLUE_HOVER,
        lightcolor=PROGRAM_BLUE_HOVER,
        darkcolor=PROGRAM_BLUE_HOVER,
        font=(text_family, 9, "bold"),
        padding=(12, 5),
        relief="solid",
        borderwidth=1,
        focuscolor="",
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", PROGRAM_BLUE_ACTIVE), ("active", PROGRAM_BLUE_HOVER), ("disabled", "#171722")],
        foreground=[("disabled", "#4b5563")],
        bordercolor=[("active", PROGRAM_BLUE), ("disabled", "#222230")],
        lightcolor=[("active", PROGRAM_BLUE), ("disabled", "#222230")],
        darkcolor=[("active", PROGRAM_BLUE), ("disabled", "#222230")],
    )

    style.configure(
        "ApplePlay.TButton",
        background=PROGRAM_BLUE,
        foreground="#ffffff",
        bordercolor=PROGRAM_BLUE_HOVER,
        lightcolor=PROGRAM_BLUE_HOVER,
        darkcolor=PROGRAM_BLUE_HOVER,
        font=(text_family, 10, "bold"),
        padding=(10, 4),
        relief="solid",
        borderwidth=1,
        focuscolor="",
    )
    style.map(
        "ApplePlay.TButton",
        background=[("pressed", PROGRAM_BLUE_ACTIVE), ("active", PROGRAM_BLUE_HOVER), ("disabled", "#171722")],
        foreground=[("disabled", "#4b5563")],
    )

    style.configure(
        "Sidebar.TButton",
        background=DEEMIX_SIDEBAR_BG,
        foreground=DEEMIX_TEXT_SECONDARY,
        font=(text_family, 9),
        anchor="w",
        padding=(12, 6),
        relief="flat",
        borderwidth=0,
        lightcolor=DEEMIX_SIDEBAR_BG,
        darkcolor=DEEMIX_SIDEBAR_BG,
        focuscolor="",
    )
    style.map(
        "Sidebar.TButton",
        background=[("active", "#1f2032"), ("pressed", "#282a40")],
        foreground=[("active", DEEMIX_TEXT_PRIMARY)],
    )

    style.configure(
        "ActiveSidebar.TButton",
        background="#1d243b",
        foreground=DEEMIX_BLUE,
        font=(text_family, 9, "bold"),
        anchor="w",
        padding=(12, 6),
        relief="flat",
        borderwidth=0,
        lightcolor="#1d243b",
        darkcolor="#1d243b",
        focuscolor="",
    )
    style.map(
        "ActiveSidebar.TButton",
        background=[("active", "#232c47"), ("pressed", "#181e31")],
        foreground=[("active", "#60a5fa")],
    )

    style.configure(
        "Chip.TButton",
        background="#242436",
        foreground=DEEMIX_TEXT_PRIMARY,
        font=(text_family, 9),
        padding=(8, 3),
        relief="solid",
        borderwidth=1,
        bordercolor="#33334d",
        lightcolor="#33334d",
        darkcolor="#33334d",
        focuscolor="",
    )
    style.map(
        "Chip.TButton",
        background=[("active", DEEMIX_BLUE), ("pressed", DEEMIX_BLUE_ACTIVE)],
        foreground=[("active", "#ffffff")],
        bordercolor=[("active", DEEMIX_BLUE)],
        lightcolor=[("active", DEEMIX_BLUE)],
        darkcolor=[("active", DEEMIX_BLUE)],
    )

    style.configure(
        "Success.TButton",
        background="#10b981",
        foreground="#ffffff",
        bordercolor="#059669",
        lightcolor="#059669",
        darkcolor="#059669",
        font=(text_family, 9, "bold"),
        padding=(10, 5),
        relief="solid",
        borderwidth=1,
        focuscolor="",
    )
    style.map(
        "Success.TButton",
        background=[("pressed", "#047857"), ("active", "#059669"), ("disabled", "#171722")],
        foreground=[("disabled", "#4b5563")],
    )

    # Progressbar
    style.configure(
        "Horizontal.TProgressbar",
        background=PROGRAM_BLUE,
        troughcolor=DEEMIX_INPUT_BG,
        bordercolor=DEEMIX_CARD_BORDER,
        lightcolor=PROGRAM_BLUE,
        darkcolor=PROGRAM_BLUE,
        thickness=7,
    )

    # Sliders / Scales (Sleek dark sliders instead of beige Windows 95 bars)
    for scale_style, bg_col in (
        ("TScale", DEEMIX_DARK_BG),
        ("Horizontal.TScale", DEEMIX_DARK_BG),
        ("Sidebar.Horizontal.TScale", DEEMIX_SIDEBAR_BG),
        ("Card.Horizontal.TScale", DEEMIX_CARD_BG),
    ):
        style.configure(
            scale_style,
            background=bg_col,
            troughcolor="#252638",
            bordercolor=bg_col,
            lightcolor=DEEMIX_BLUE,
            darkcolor=DEEMIX_BLUE,
            sliderlength=14,
            sliderthickness=8,
            relief="flat",
            borderwidth=0,
        )
        style.map(
            scale_style,
            background=[("active", DEEMIX_BLUE)],
            troughcolor=[("active", "#2e3048")],
        )

    # Minimalist Arrowless Dark Scrollbars
    try:
        style.layout(
            "Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                        "sticky": "ns",
                    },
                )
            ],
        )
        style.layout(
            "Horizontal.TScrollbar",
            [
                (
                    "Horizontal.Scrollbar.trough",
                    {
                        "children": [("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                        "sticky": "ew",
                    },
                )
            ],
        )
    except Exception:
        pass

    for sb_style in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(
            sb_style,
            background="#2c2d42",
            troughcolor=DEEMIX_DARK_BG,
            bordercolor=DEEMIX_DARK_BG,
            lightcolor=DEEMIX_DARK_BG,
            darkcolor=DEEMIX_DARK_BG,
            arrowcolor=DEEMIX_TEXT_SECONDARY,
            relief="flat",
            borderwidth=0,
            width=8,
        )
        style.map(
            sb_style,
            background=[("active", DEEMIX_BLUE), ("pressed", DEEMIX_BLUE_ACTIVE)],
        )

    # Panedwindow & Separator (No white grip squares)
    style.configure("TPanedwindow", background=DEEMIX_DARK_BG)
    style.configure(
        "Sash",
        sashthickness=6,
        sashpad=1,
        background=DEEMIX_DARK_BG,
        bordercolor=DEEMIX_DARK_BG,
        lightcolor=DEEMIX_DARK_BG,
        darkcolor=DEEMIX_DARK_BG,
        handlepad=0,
        handlesize=0,
        relief="flat",
    )
    style.configure("TSeparator", background=DEEMIX_CARD_BORDER)

    return style


def harmonize_widget_surfaces(widget, context: str = "dark") -> None:
    """Recursively align child frame/label/scale styles with their parent card or sidebar surface."""
    try:
        cls = widget.winfo_class()
        st = ""
        try:
            st = str(widget.cget("style") or "")
        except Exception:
            pass

        if cls == "TLabelframe" or st in {"Card.TFrame", "CardInner.TFrame"}:
            context = "card"
        elif st in {"Sidebar.TFrame", "Header.TFrame"}:
            context = "sidebar"
        elif st == "CoverBox.TFrame":
            context = "cover"

        if cls == "TFrame" and not st:
            if context == "card":
                widget.configure(style="CardInner.TFrame")
            elif context == "sidebar":
                widget.configure(style="Sidebar.TFrame")
            elif context == "cover":
                widget.configure(style="CoverBox.TFrame")
        elif cls == "TLabel" and not st:
            if context == "card":
                widget.configure(style="Card.TLabel")
            elif context == "sidebar":
                widget.configure(style="Sidebar.TLabel")
            elif context == "cover":
                widget.configure(style="Cover.TLabel")
        elif cls == "TScale" and not st:
            if context == "card":
                widget.configure(style="Card.Horizontal.TScale")
            elif context == "sidebar":
                widget.configure(style="Sidebar.Horizontal.TScale")
    except Exception:
        pass

    try:
        for child in widget.winfo_children():
            harmonize_widget_surfaces(child, context)
    except Exception:
        pass


def format_explicit_badge(explicit: bool = False) -> str:
    """Return explicit badge icon if true."""
    return "🅴" if explicit else ""


def format_title_with_explicit(title: str, explicit: bool = False) -> str:
    """Format title string, appending the explicit badge if flagged."""
    clean = str(title or "").strip()
    if not explicit or not clean:
        return clean
    if clean.endswith("🅴") or clean.endswith("[E]") or clean.endswith("🔞"):
        return clean
    return f"{clean} 🅴"


def format_quality_badge(quality: str, explicit: bool = False) -> str:
    """Format quality and resolution strings with Apple Music-inspired badges."""
    raw = str(quality or "").strip()
    if not raw or raw in {"—", "-", "A definir"}:
        base = "—"
    else:
        low = raw.lower()
        if "flac" in low or "lossless" in low:
            base = "💎 LOSSLESS (FLAC)"
        elif "320" in low:
            base = "⚡ 320k"
        elif "128" in low:
            base = "📱 128k"
        elif "2160" in low or "4k" in low:
            base = "🌟 4K UHD"
        elif "1440" in low or "2k" in low:
            base = "🎬 2K"
        elif "1080" in low:
            base = "🎬 1080p"
        elif "720" in low:
            base = "📱 720p"
        elif "480" in low:
            base = "⚡ 480p"
        elif "360" in low:
            base = "⚡ 360p"
        elif "m4a" in low or "aac" in low:
            base = "🎵 AAC/M4A"
        elif "mp3" in low:
            base = "🎵 MP3"
        elif "wav" in low:
            base = "🎙️ WAV"
        else:
            base = raw

    if explicit:
        if base == "—":
            return "🅴"
        return f"{base} 🅴"
    return base


def configure_treeview_status_tags(tree) -> None:
    try:
        tree.tag_configure("completed", foreground="#10b981")     # Emerald Green
        tree.tag_configure("downloading", foreground=PROGRAM_BLUE)   # Deemix Electric Blue
        tree.tag_configure("pending", foreground="#818cf8")       # Soft Indigo
        tree.tag_configure("failed", foreground="#ef4444")        # Crimson Red
        tree.tag_configure("cancelled", foreground="#94a3b8")     # Slate Gray
        tree.tag_configure("odd", background="#161620")           # Subtle Zebra Stripe
        tree.tag_configure("even", background=DEEMIX_INPUT_BG)    # Base Dark
    except Exception:
        pass


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


def window_dimensions(
    area: tuple[int, int, int, int],
    max_width: int = 1280,
    max_height: int = 840,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = area
    width = min(max_width, max(1, right - left - 50))
    height = min(max_height, max(1, bottom - top - 80))
    return width, height, left + (right - left - width) // 2, top + 20


def fit_window(root, *, max_width: int = 1280, max_height: int = 840) -> None:
    width, height, x, y = window_dimensions(
        desktop_work_area(root),
        max_width=max_width,
        max_height=max_height,
    )
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.minsize(min(560, width), min(420, height))


class ScrollablePage(ttk.Frame):
    """Keep every control reachable, including when Windows scales the fonts."""

    def __init__(self, parent):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = Canvas(self, highlightthickness=0, borderwidth=0, takefocus=False, bg=DEEMIX_DARK_BG)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set)
        self.body = ttk.Frame(self.canvas, padding=10)
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.pending = False
        self.body.bind("<Configure>", self._schedule_layout)
        self.canvas.bind("<Configure>", self._schedule_layout)
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
        if event.widget.winfo_class() in {"Text", "TCombobox", "Treeview"}:
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
    label.pack(fill="x", pady=(0, 4))
    label.bind("<Configure>", lambda event: label.configure(wraplength=max(1, event.width)))
    return label


class ToolTip:
    """Small accessible hint for compact icon-only controls."""

    def __init__(self, widget, text: str, delay: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.pending = None
        self.window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self.pending = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.pending is not None:
            try:
                self.widget.after_cancel(self.pending)
            except Exception:
                pass
            self.pending = None

    def _show(self):
        self.pending = None
        if self.window is not None or not self.widget.winfo_exists():
            return
        self.window = Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        try:
            self.window.attributes("-topmost", True)
        except Exception:
            pass
        x = self.widget.winfo_rootx() + max(0, self.widget.winfo_width() // 2)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window.geometry(f"+{x}+{y}")
        ttk.Label(
            self.window,
            text=self.text,
            padding=(8, 4),
            relief="solid",
            borderwidth=1,
            background=DEEMIX_CARD_BG,
            foreground=DEEMIX_TEXT_PRIMARY,
        ).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None


def add_tooltip(widget, text: str):
    widget._c2_tooltip = ToolTip(widget, text)
    return widget


def build_brand(parent, logo_path, family="Segoe UI"):
    brand = ttk.Frame(parent, style="Header.TFrame")
    # Crop only the blue C² symbol and use it as the product-name initial.
    brand.source_image = PhotoImage(file=str(logo_path))
    brand.logo_image = PhotoImage(width=34, height=36)
    brand.logo_image.tk.call(
        brand.logo_image, "copy", brand.source_image,
        "-from", 58, 48, 305, 327, "-to", 0, 0, "-subsample", 7, 7,
    )
    ttk.Label(brand, image=brand.logo_image, style="Sidebar.TLabel").pack(side="left")
    ttk.Label(
        brand,
        text="² Downloader",
        font=(family, 14, "bold"),
        foreground=DEEMIX_TEXT_PRIMARY,
        style="Sidebar.TLabel",
    ).pack(side="left", padx=(3, 0))
    return brand
