"""Application-wide theming utilities for the RC Car UI."""

PRIMARY = "#00d2ff"
SECONDARY = "#36e0b8"
SURFACE = "#0c1117"
SURFACE_MID = "#111927"
TEXT_PRIMARY = "#e8ecf3"
TEXT_MUTED = "#9ba7b4"
ACCENT_WARM = "#ffc857"

GLOBAL_QSS = f"""
* {{
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    color: {TEXT_PRIMARY};
}}

QMainWindow {{
    background-color: {SURFACE};
}}

QWidget#content-card, QFrame#content-card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 0.06),
        stop:1 rgba(255, 255, 255, 0.02));
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 18px;
}}

QWidget#content-card[role="main"], QFrame#content-card[role="main"] {{
    border: none;
}}

#header-title {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.6px;
}}

#status-chip {{
    padding: 0 6px;
    border-radius: 7px;
    font-weight: 600;
    font-size: 9px;
    line-height: 9px;
    height: 18px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    color: {TEXT_PRIMARY};
    background: rgba(255, 255, 255, 0.06);
}}

#battery-chip {{
    padding: 0 6px;
    border-radius: 7px;
    font-weight: 600;
    font-size: 9px;
    line-height: 9px;
    height: 18px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: {TEXT_MUTED};
    background: rgba(255, 255, 255, 0.04);
}}

#status-chip[state="discovering"] {{
    border-color: rgba(255, 200, 87, 0.45);
    color: {ACCENT_WARM};
    background: rgba(255, 200, 87, 0.14);
}}

#status-chip[state="connected"] {{
    border-color: rgba(0, 210, 255, 0.55);
    color: {PRIMARY};
    background: rgba(0, 210, 255, 0.16);
}}

#status-chip[state="idle"] {{
    color: {TEXT_MUTED};
}}

#card-title {{
    font-size: 18px;
    font-weight: 650;
    letter-spacing: 0.4px;
}}

QLabel#muted {{
    color: {TEXT_MUTED};
}}

QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 0.09),
        stop:1 rgba(255, 255, 255, 0.04));
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    padding: 10px 14px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: rgba(0, 210, 255, 0.18);
    border-color: rgba(0, 210, 255, 0.5);
}}

QPushButton:pressed {{
    background-color: rgba(0, 210, 255, 0.26);
}}

QLineEdit, QTextEdit {{
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    padding: 10px 12px;
    color: {TEXT_PRIMARY};
    selection-background-color: {PRIMARY};
}}

QProgressBar {{
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 10px;
    text-align: center;
    color: {TEXT_PRIMARY};
}}

QProgressBar::chunk {{
    background-color: {PRIMARY};
    border-radius: 8px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 6px 0 6px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 0.18);
    border-radius: 6px;
    min-height: 18px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(0, 210, 255, 0.5);
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0 6px 0 6px;
}}
QScrollBar::handle:horizontal {{
    background: rgba(255, 255, 255, 0.18);
    border-radius: 6px;
    min-width: 18px;
}}
QScrollBar::handle:horizontal:hover {{
    background: rgba(0, 210, 255, 0.5);
}}

QToolTip {{
    background-color: #0b111c;
    color: {TEXT_PRIMARY};
    border: 1px solid rgba(255, 255, 255, 0.18);
    padding: 6px 8px;
    border-radius: 6px;
}}
"""

def apply_app_theme(app) -> None:
    """Apply the global stylesheet to the QApplication."""
    app.setStyleSheet(GLOBAL_QSS)


def make_card(widget) -> None:
    """Tag a widget so the shared card styling applies via QSS."""
    widget.setObjectName("content-card")
