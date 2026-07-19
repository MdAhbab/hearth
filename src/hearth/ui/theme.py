"""Palette-driven theming: one stylesheet template, two palettes.

The user picks System / Dark / Light in Settings; "system" follows the OS
scheme live via Qt's colorSchemeChanged signal (wired in app.py).
"""

from __future__ import annotations

DARK = {
    "bg": "#1b1917",
    "bgSidebar": "#141210",
    "bgCard": "#221f1c",
    "bgInput": "#242120",
    "bgTool": "#201d1a",
    "bgChip": "#242019",
    "bgHover": "#242019",
    "bgNavActive": "#2e2820",
    "border": "#2e2a25",
    "borderInput": "#3a332b",
    "text": "#eae4da",
    "textSoft": "#d8d0c4",
    "textMuted": "#9a9186",
    "textMeta": "#8d8478",
    "accent": "#f4b860",
    "accentHover": "#ffca75",
    "accentText": "#241c10",
    "accentDisabledBg": "#4a4137",
    "accentDisabledText": "#857a6b",
    "bubbleUser": "#3d3323",
    "bubbleAssistant": "#262220",
    "confirmBg": "#2b2117",
    "confirmInner": "#1b1815",
    "approve": "#3e7d4f",
    "approveHover": "#4c9560",
    "reject": "#8a3b34",
    "rejectHover": "#a4483f",
    "secondaryBg": "#2e2a25",
    "secondaryHover": "#3a342d",
    "error": "#e08d84",
}

LIGHT = {
    "bg": "#faf7f2",
    "bgSidebar": "#f0ebe2",
    "bgCard": "#ffffff",
    "bgInput": "#ffffff",
    "bgTool": "#f3efe8",
    "bgChip": "#f3efe8",
    "bgHover": "#e9e3d8",
    "bgNavActive": "#e5ddce",
    "border": "#e3dcd1",
    "borderInput": "#d5ccbd",
    "text": "#2b2620",
    "textSoft": "#443d33",
    "textMuted": "#8a8072",
    "textMeta": "#988e7f",
    "accent": "#a8681a",
    "accentHover": "#c07a22",
    "accentText": "#ffffff",
    "accentDisabledBg": "#d9d0c1",
    "accentDisabledText": "#a49a89",
    "bubbleUser": "#f2e4c8",
    "bubbleAssistant": "#f0ebe2",
    "confirmBg": "#fdf3e2",
    "confirmInner": "#ffffff",
    "approve": "#2f7a41",
    "approveHover": "#3c9151",
    "reject": "#a33f36",
    "rejectHover": "#bb4c42",
    "secondaryBg": "#efe9de",
    "secondaryHover": "#e4dccd",
    "error": "#b3352a",
}

_TEMPLATE = """
* {{ font-family: 'Helvetica Neue', 'Segoe UI', 'Ubuntu', sans-serif; }}

QMainWindow, QWidget#root {{ background: {bg}; }}

QWidget#sidebar {{ background: {bgSidebar}; border-right: 1px solid {border}; }}
QPushButton[nav="true"] {{
    color: {textMuted}; background: transparent; border: none;
    padding: 10px 14px; text-align: left; border-radius: 8px; font-size: 14px;
}}
QPushButton[nav="true"]:hover {{ background: {bgHover}; color: {text}; }}
QPushButton[nav="true"][active="true"] {{ background: {bgNavActive}; color: {accent}; }}

QLabel#appTitle {{ color: {accent}; font-size: 19px; font-weight: 700; padding: 14px; }}
QLabel#statusPill {{
    color: {textMuted}; font-size: 12px; padding: 4px 10px;
    border: 1px solid {border}; border-radius: 10px; background: {bgTool};
}}

QScrollArea {{ border: none; background: transparent; }}
QWidget#chatCanvas {{ background: {bg}; }}

QFrame[bubble="user"] {{ background: {bubbleUser}; border-radius: 12px; padding: 4px; }}
QFrame[bubble="assistant"] {{ background: {bubbleAssistant}; border-radius: 12px; padding: 4px; }}
QFrame[bubble="tool"] {{
    background: {bgTool}; border: 1px solid {border}; border-radius: 10px;
}}
QLabel[bubbleText="true"] {{ color: {text}; font-size: 14px; }}
QLabel[bubbleMeta="true"] {{ color: {textMeta}; font-size: 11px; }}

QFrame#confirmCard {{
    background: {confirmBg}; border: 1px solid {accent}; border-radius: 12px;
}}
QLabel#confirmTitle {{ color: {accent}; font-weight: 700; font-size: 14px; }}
QPlainTextEdit#confirmPreview, QPlainTextEdit#confirmEditor {{
    background: {confirmInner}; color: {text}; border: 1px solid {borderInput};
    border-radius: 8px; font-family: Menlo, Consolas, monospace; font-size: 12px;
}}

QPushButton#approveBtn {{
    background: {approve}; color: white; border: none; border-radius: 8px;
    padding: 8px 18px; font-weight: 600;
}}
QPushButton#approveBtn:hover {{ background: {approveHover}; }}
QPushButton#rejectBtn {{
    background: {reject}; color: white; border: none; border-radius: 8px;
    padding: 8px 18px; font-weight: 600;
}}
QPushButton#rejectBtn:hover {{ background: {rejectHover}; }}
QPushButton#editBtn, QPushButton[secondary="true"] {{
    background: {secondaryBg}; color: {textSoft}; border: 1px solid {borderInput};
    border-radius: 8px; padding: 8px 14px;
}}
QPushButton#editBtn:hover, QPushButton[secondary="true"]:hover {{ background: {secondaryHover}; }}
QPushButton[voiceRecording="true"], QPushButton[voiceRecording="true"]:hover {{
    background: {reject}; color: white; border: 1px solid {reject};
}}

QPlainTextEdit#chatInput {{
    background: {bgInput}; color: {text}; border: 1px solid {borderInput};
    border-radius: 12px; padding: 10px; font-size: 14px;
}}
QPushButton#sendBtn {{
    background: {accent}; color: {accentText}; border: none; border-radius: 10px;
    padding: 10px 22px; font-weight: 700; font-size: 14px;
}}
QPushButton#sendBtn:hover {{ background: {accentHover}; }}
QPushButton#sendBtn:disabled {{ background: {accentDisabledBg}; color: {accentDisabledText}; }}
QPushButton#sendBtn[stopMode="true"] {{ background: {reject}; color: white; }}
QPushButton#sendBtn[stopMode="true"]:hover {{ background: {rejectHover}; }}

QPushButton[chip="true"] {{
    background: {bgChip}; color: {textSoft}; border: 1px solid {borderInput};
    border-radius: 14px; padding: 6px 12px; font-size: 12px;
}}
QPushButton[chip="true"]:hover {{ border-color: {accent}; color: {accent}; }}

QLabel[h1="true"] {{ color: {text}; font-size: 20px; font-weight: 700; }}
QLabel[h2="true"] {{ color: {textSoft}; font-size: 15px; font-weight: 600; }}
QLabel[muted="true"] {{ color: {textMuted}; font-size: 12px; }}
QLabel[error="true"] {{ color: {error}; font-size: 12px; }}

QFrame[card="true"] {{
    background: {bgCard}; border: 1px solid {border}; border-radius: 12px;
}}

QTableWidget {{
    background: {bgTool}; color: {textSoft}; border: 1px solid {border};
    border-radius: 8px; gridline-color: {border}; font-size: 12px;
}}
QHeaderView::section {{
    background: {bubbleAssistant}; color: {textMuted}; border: none; padding: 6px;
}}

QLineEdit, QSpinBox, QComboBox {{
    background: {bgInput}; color: {text}; border: 1px solid {borderInput};
    border-radius: 8px; padding: 7px;
}}
QComboBox QAbstractItemView {{
    background: {bgCard}; color: {text}; border: 1px solid {border};
    selection-background-color: {bgNavActive};
}}
QListWidget {{
    background: {bgTool}; color: {textSoft}; border: 1px solid {border}; border-radius: 8px;
}}
QCheckBox {{ color: {textSoft}; }}
"""


def stylesheet(mode: str) -> str:
    """Render the stylesheet for 'dark' or 'light'."""
    palette = LIGHT if mode == "light" else DARK
    return _TEMPLATE.format(**palette)


def resolve_mode(preference: str, qt_app) -> str:
    """Map a user preference (system/dark/light) to a concrete mode."""
    if preference in ("dark", "light"):
        return preference
    try:
        from PySide6.QtCore import Qt

        scheme = qt_app.styleHints().colorScheme()
        return "light" if scheme == Qt.ColorScheme.Light else "dark"
    except Exception:  # noqa: BLE001 — any detection failure falls back to dark
        return "dark"


def apply_theme(qt_app, preference: str) -> None:
    mode = resolve_mode(preference, qt_app)
    qt_app.setStyleSheet(stylesheet(mode))
    # Links inside rich-text labels take their color from the palette, not QSS.
    from PySide6.QtGui import QColor, QPalette

    palette = qt_app.palette()
    palette.setColor(
        QPalette.ColorRole.Link, QColor((LIGHT if mode == "light" else DARK)["accent"])
    )
    qt_app.setPalette(palette)


def app_icon():
    """Programmatic app icon: a warm rounded square with an 'H' — gives the
    window, taskbar, and tray an identity without shipping image assets."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(DARK["accent"]))
        radius = size * 0.22
        painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
        painter.setPen(QColor(DARK["accentText"]))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * 0.62))
        painter.setFont(font)
        painter.drawText(QRectF(0, -size * 0.04, size, size), Qt.AlignmentFlag.AlignCenter, "H")
        painter.end()
        icon.addPixmap(pixmap)
    return icon
