"""界面配色与样式。

取向是公文与档案：米白纸面、深红标题线、克制的分隔线。
不用渐变按钮和圆角卡片——那是消费类 App 的语言，不是这个题材的语言。
"""

PAPER = "#f2efe8"
PANEL = "#fbfaf7"
INK = "#26282b"
MUTED = "#6b7078"
LINE = "#d9d3c6"
ACCENT = "#8c2f33"
ACCENT_DIM = "#a8595c"
HEADER = "#31353b"
WARN = "#a8601f"
OK = "#3f6b45"

QSS = f"""
QWidget {{
    background: {PAPER};
    color: {INK};
    font-family: "Microsoft YaHei", "SimSun";
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {PAPER}; }}

QTabWidget::pane {{
    border: 1px solid {LINE};
    background: {PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 8px 22px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
    font-weight: bold;
}}
QTabBar::tab:hover:!selected {{ color: {INK}; }}

#statusBar {{
    background: {HEADER};
    color: #eceae5;
    padding: 0px;
    border: none;
}}
#statusBar QLabel {{ background: transparent; }}
#statusDate {{ background: transparent; color: #d8b06a; font-size: 16px; font-weight: bold; }}
#statusMain {{ background: transparent; color: #f2f0ec; font-size: 16px; font-weight: bold; }}
#statusSub  {{ background: transparent; color: #a8adb5; font-size: 12px; }}

#narrative {{
    background: #fdfcf9;
    border: 1px solid {LINE};
    padding: 18px 22px;
    font-size: 14px;
    line-height: 190%;
}}

QGroupBox {{
    border: 1px solid {LINE};
    background: {PANEL};
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {ACCENT};
    font-weight: bold;
}}

QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    padding: 7px 14px;
    color: {INK};
}}
QPushButton:hover {{ border-color: {ACCENT_DIM}; color: {ACCENT}; }}
QPushButton:pressed {{ background: #ece7dc; }}
QPushButton:disabled {{ color: #b2aea6; border-color: #e6e1d6; }}
QPushButton#primary {{
    background: {ACCENT};
    color: #fdfcf9;
    border: 1px solid {ACCENT};
    font-weight: bold;
}}
QPushButton#primary:hover {{ background: {ACCENT_DIM}; }}

QComboBox, QLineEdit {{
    background: #fdfcf9;
    border: 1px solid {LINE};
    padding: 6px 8px;
}}
QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT_DIM}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: #fdfcf9;
    border: 1px solid {LINE};
    selection-background-color: {ACCENT};
    selection-color: #fdfcf9;
}}

QTableWidget, QTreeWidget, QListWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    gridline-color: #eae5da;
    selection-background-color: #ece3d6;
    selection-color: {INK};
    outline: none;
}}
QHeaderView::section {{
    background: #ebe6db;
    color: {MUTED};
    padding: 7px 8px;
    border: none;
    border-bottom: 1px solid {LINE};
    font-weight: bold;
}}
QTableWidget::item, QTreeWidget::item {{ padding: 5px 6px; }}
QListWidget::item {{ padding: 2px; border-bottom: 1px solid #efeade; }}

QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #cdc6b8; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QSplitter::handle {{ background: {LINE}; width: 1px; }}
QLabel#sectionTitle {{
    color: {ACCENT};
    font-size: 15px;
    font-weight: bold;
    padding: 4px 0 2px 0;
}}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#fieldKey {{ color: {MUTED}; }}
QLabel#fieldVal {{ color: {INK}; font-weight: bold; font-size: 14px; }}
"""
