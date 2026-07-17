"""Application stylesheet — warm, quiet, hearth-like."""

STYLESHEET = """
* { font-family: 'Helvetica Neue', 'Segoe UI', 'Ubuntu', sans-serif; }

QMainWindow, QWidget#root { background: #1b1917; }

QWidget#sidebar { background: #141210; border-right: 1px solid #2a2724; }
QPushButton[nav="true"] {
    color: #b8b0a6; background: transparent; border: none;
    padding: 10px 14px; text-align: left; border-radius: 8px; font-size: 14px;
}
QPushButton[nav="true"]:hover { background: #242019; color: #eae4da; }
QPushButton[nav="true"][active="true"] { background: #2e2820; color: #f4b860; }

QLabel#appTitle { color: #f4b860; font-size: 19px; font-weight: 700; padding: 14px; }
QLabel#statusPill {
    color: #9a9186; font-size: 12px; padding: 4px 10px;
    border: 1px solid #2a2724; border-radius: 10px; background: #201d1a;
}

QScrollArea { border: none; background: transparent; }
QWidget#chatCanvas { background: #1b1917; }

QFrame[bubble="user"] {
    background: #33405a; border-radius: 12px; padding: 4px;
}
QFrame[bubble="assistant"] {
    background: #262220; border-radius: 12px; padding: 4px;
}
QFrame[bubble="tool"] {
    background: #201d1a; border: 1px solid #2a2724; border-radius: 10px;
}
QLabel[bubbleText="true"] { color: #eae4da; font-size: 14px; }
QLabel[bubbleMeta="true"] { color: #8d8478; font-size: 11px; }

QFrame#confirmCard {
    background: #2b2117; border: 1px solid #f4b860; border-radius: 12px;
}
QLabel#confirmTitle { color: #f4b860; font-weight: 700; font-size: 14px; }
QPlainTextEdit#confirmPreview, QPlainTextEdit#confirmEditor {
    background: #1b1815; color: #eae4da; border: 1px solid #3a332b;
    border-radius: 8px; font-family: Menlo, Consolas, monospace; font-size: 12px;
}

QPushButton#approveBtn {
    background: #3e7d4f; color: white; border: none; border-radius: 8px;
    padding: 8px 18px; font-weight: 600;
}
QPushButton#approveBtn:hover { background: #4c9560; }
QPushButton#rejectBtn {
    background: #8a3b34; color: white; border: none; border-radius: 8px;
    padding: 8px 18px; font-weight: 600;
}
QPushButton#rejectBtn:hover { background: #a4483f; }
QPushButton#editBtn, QPushButton[secondary="true"] {
    background: #2e2a25; color: #d8d0c4; border: 1px solid #3a332b;
    border-radius: 8px; padding: 8px 14px;
}
QPushButton#editBtn:hover, QPushButton[secondary="true"]:hover { background: #3a342d; }

QPlainTextEdit#chatInput {
    background: #242120; color: #eae4da; border: 1px solid #3a332b;
    border-radius: 12px; padding: 10px; font-size: 14px;
}
QPushButton#sendBtn {
    background: #f4b860; color: #241c10; border: none; border-radius: 10px;
    padding: 10px 22px; font-weight: 700; font-size: 14px;
}
QPushButton#sendBtn:hover { background: #ffca75; }
QPushButton#sendBtn:disabled { background: #4a4137; color: #857a6b; }
QPushButton#stopBtn {
    background: #8a3b34; color: white; border: none; border-radius: 10px;
    padding: 10px 18px; font-weight: 600;
}

QPushButton[chip="true"] {
    background: #242019; color: #cfc7ba; border: 1px solid #3a332b;
    border-radius: 14px; padding: 6px 12px; font-size: 12px;
}
QPushButton[chip="true"]:hover { border-color: #f4b860; color: #f4b860; }

QLabel[h1="true"] { color: #eae4da; font-size: 20px; font-weight: 700; }
QLabel[h2="true"] { color: #d8d0c4; font-size: 15px; font-weight: 600; }
QLabel[muted="true"] { color: #9a9186; font-size: 12px; }

QFrame[card="true"] {
    background: #221f1c; border: 1px solid #2e2a25; border-radius: 12px;
}

QTableWidget {
    background: #201d1a; color: #d8d0c4; border: 1px solid #2e2a25;
    border-radius: 8px; gridline-color: #2e2a25; font-size: 12px;
}
QHeaderView::section {
    background: #262220; color: #9a9186; border: none; padding: 6px;
}

QLineEdit, QSpinBox {
    background: #242120; color: #eae4da; border: 1px solid #3a332b;
    border-radius: 8px; padding: 7px;
}
QListWidget {
    background: #201d1a; color: #d8d0c4; border: 1px solid #2e2a25; border-radius: 8px;
}
QCheckBox { color: #d8d0c4; }
"""
