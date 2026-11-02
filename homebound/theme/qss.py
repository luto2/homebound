"""App stylesheet and palette built from tokens (design-system/qt-implementation.md §2)."""
import re
from importlib.resources import files

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QWidget

from ..validation import require_type
from .fonts import load_fonts
from .tokens import HEX_COLORS, PIXELS, color, font, qss_color

QSS = """
QWidget            { background: {bg-1}; color: {ink}; font-family: "Noto Sans"; font-size: 13px; }
QMainWindow::separator { background: {line-1}; width: 1px; height: 1px; }
QDockWidget > QWidget, QDialog { background: {bg-2}; }
QDialog .QWidget, QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton { background: transparent; }
QDockWidget::title { background: {bg-3}; padding: {sp-4}px {sp-8}px; }
QFrame[role="divider"] { background: {line-1}; max-height: 1px; }
QFrame[role="panel"]   { background: {bg-2}; }
QToolBar { background: {bg-1}; border-bottom: 1px solid {line-1}; spacing: {sp-8}px; padding: 0 {sp-8}px; min-height: {size-toolbar}px; }
QStatusBar { background: {bg-1}; border-top: 1px solid {line-1}; color: {ink-muted}; font-size: 12px; }
QStatusBar QLabel { background: transparent; }

QPushButton, QToolButton {
    background: {bg-3}; color: {ink}; border: 1px solid {line-2}; border-radius: {radius-2}px;
    min-height: {size-control-inner}px; padding: 0 {sp-12}px; }
QPushButton:hover, QToolButton:hover   { background: {bg-4}; }
QPushButton:pressed, QToolButton:pressed { background: {bg-2}; }
QPushButton:focus                      { border: 2px solid {accent}; }
QPushButton:disabled, QToolButton:disabled { color: {ink-disabled}; border-color: {line-1}; background: {bg-2}; }
QToolButton:checked, QPushButton:checked { background: {accent-dim}; border: 1px solid {accent}; color: {ink}; }
QPushButton[variant="primary"]         { background: {accent}; border-color: {accent}; color: {ink-inverse}; font-weight: 600; }
QPushButton[variant="primary"]:hover   { background: {accent-hover}; border-color: {accent-hover}; }
QPushButton[variant="danger"]          { background: {danger-dim}; border-color: {danger}; color: {danger}; font-weight: 600; }
QPushButton[variant="ghost"], QToolButton[variant="ghost"] { background: transparent; border-color: transparent; color: {ink-muted}; }
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover { background: {bg-4}; color: {ink}; }
QToolButton[variant="map"] { background: {accent-dim}; border-color: {accent}; font-weight: 600; }
QToolButton[variant="map"]:hover { background: {accent-dim}; border-color: {accent-hover}; }
QPushButton[variant="primary"]:disabled, QPushButton[variant="danger"]:disabled { background: {bg-2}; border-color: {line-1}; color: {ink-disabled}; font-weight: 400; }
QPushButton[size="sm"] { min-height: 18px; padding: 0 {sp-6}px; font-size: 11px; }

QLineEdit, QComboBox, QPlainTextEdit, QSpinBox {
    background: {bg-3}; border: 1px solid {line-2}; border-radius: {radius-2}px; padding: 0 {sp-8}px;
    selection-background-color: {accent}; selection-color: {ink-inverse}; min-height: {size-control-inner}px; }
QPlainTextEdit { padding: {sp-4}px {sp-8}px; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus { border: 2px solid {accent}; }
QLineEdit:disabled, QComboBox:disabled { background: {bg-2}; border-color: {line-1}; color: {ink-muted}; }
QLineEdit[state="valid"]   { border-color: {success}; }
QLineEdit[state="invalid"] { border-color: {danger}; background: {danger-dim}; }
QLineEdit[mono="true"]     { font-family: "Hack", "Noto Sans Mono", "DejaVu Sans Mono"; font-size: 12px; }
QComboBox::drop-down { border: 0; width: 18px; }
QComboBox QAbstractItemView { background: {bg-2}; border: 1px solid {line-2}; selection-background-color: {accent}; selection-color: {ink-inverse}; }
QLabel { background: transparent; }
QLabel[role="help"]       { color: {ink-faint}; font-size: 12px; }
QLabel[role="help-ok"]    { color: {success}; font-size: 12px; }
QLabel[role="help-err"]   { color: {danger};  font-size: 12px; }
QLabel[role="help-warn"]  { color: {warning}; font-size: 12px; }
QLabel[role="caption"]    { color: {ink-muted}; font-size: 11px; font-weight: 600; }
QLabel[role="title"]      { font-size: 15px; font-weight: 600; }
QLabel[role="muted"]      { color: {ink-muted}; }
QLabel[role="mono"]       { font-family: "Hack", "Noto Sans Mono", "DejaVu Sans Mono"; font-size: 12px; color: {ink-muted}; }
QLabel[role="best-guess"] { color: {accent}; font-size: 11px; font-weight: 600; }

QCheckBox, QRadioButton { background: transparent; spacing: {sp-6}px; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; border: 1px solid {line-2}; background: {bg-3}; }
QCheckBox::indicator { border-radius: {radius-1}px; }
QRadioButton::indicator { border-radius: 7px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: {accent}; border-color: {accent}; }
QCheckBox::indicator:checked    { image: url({icon-check}); }
QRadioButton::indicator:checked { image: url({icon-radio}); }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: {accent}; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { background: {bg-2}; border-color: {line-1}; }

QFrame[card="true"] { background: {bg-3}; border: 1px solid {line-1}; border-radius: {radius-3}px; }
QFrame[card="true"][selected="true"] { background: {accent-dim}; border: 1px solid {accent}; }
QFrame[banner="info"]    { background: {bg-3}; border: 1px solid {line-2}; border-radius: {radius-2}px; }
QFrame[banner="warning"] { background: {warning-dim}; border: 1px solid {warning}; border-radius: {radius-2}px; }
QFrame[banner="success"] { background: {success-dim}; border: 1px solid {success}; border-radius: {radius-2}px; }
QFrame[banner="danger"]  { background: {danger-dim};  border: 1px solid {danger};  border-radius: {radius-2}px; }

QTableView, QListView, QTreeView { background: {bg-2}; gridline-color: {line-1}; border: 0; font-size: 12px;
             selection-background-color: {accent-dim}; selection-color: {ink}; outline: 0; }
QTableView::item { padding: 0 {sp-8}px; border-bottom: 1px solid {line-1}; }
QTableView::item:hover, QListView::item:hover { background: {bg-4}; }
QListView::item { min-height: {size-row}px; padding: 0 {sp-8}px; }
QListView::item:selected { background: {accent-dim}; color: {ink}; }
QHeaderView { background: {bg-3}; }
QHeaderView::section { background: {bg-3}; color: {ink-muted}; border: 0; border-bottom: 1px solid {line-1};
                       padding: 0 {sp-8}px; font-size: 11px; font-weight: 600; height: {size-row}px; }

QScrollBar:vertical { background: {bg-1}; width: 10px; margin: 0; }
QScrollBar:horizontal { background: {bg-1}; height: 10px; margin: 0; }
QScrollBar::handle { background: {bg-4}; border-radius: 4px; min-height: 20px; min-width: 20px; }
QScrollBar::handle:hover { background: {line-2}; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QProgressBar { background: {bg-4}; border: 0; border-radius: 3px; }
QProgressBar::chunk { background: {accent}; border-radius: 3px; }
QSlider::groove:horizontal { height: 4px; background: {bg-4}; border-radius: 2px; }
QSlider::sub-page:horizontal { background: {accent}; border-radius: 2px; }
QSlider::handle:horizontal { background: {ink}; width: 12px; margin: -5px 0; border-radius: 6px; }

QMenu { background: {bg-2}; border: 1px solid {line-2}; border-radius: {radius-4}px; padding: {sp-4}px 0; }
QMenu::item { padding: 0 {sp-12}px 0 {sp-8}px; min-height: 26px; background: transparent; }
QMenu::item:selected { background: {accent}; color: {ink-inverse}; }
QMenu::item:disabled { color: {ink-disabled}; }
QMenu::indicator { width: 14px; height: 14px; left: {sp-6}px; }
QMenu::indicator:non-exclusive:checked { image: url({icon-menu-check}); }
QMenu::indicator:exclusive:checked { image: url({icon-menu-dot}); }
QMenu::indicator:selected:non-exclusive:checked { image: url({icon-check}); }
QMenu::indicator:selected:exclusive:checked { image: url({icon-radio}); }
QMenu::icon { left: {sp-6}px; }
QMenu::separator { height: 1px; background: {line-1}; margin: {sp-4}px 0; }
QToolTip { background: {bg-3}; color: {ink}; border: 1px solid {line-2}; padding: {sp-4}px {sp-6}px; }
"""

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.-]+)\}")
ICONS = {"icon-check": "check.svg", "icon-radio": "radio-dot.svg", "icon-menu-check": "menu-check.svg",
         "icon-menu-dot": "menu-dot.svg"}


def build_qss() -> str:
    """Return the app style sheet with every ``{token}`` placeholder filled in.

    Raises:
        KeyError: If the template names a token that doesn't exist.
    """
    values = {name: qss_color(name) for name in HEX_COLORS} | {name: f"{value:g}" for name, value in PIXELS.items()}
    values["size-control-inner"] = f"{PIXELS['size-control'] - 2:g}"
    icons = files("homebound.theme").joinpath("icons")
    values |= {key: str(icons.joinpath(file_name)) for key, file_name in ICONS.items()}
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], QSS)


def build_palette() -> QPalette:
    """Return the Fusion palette built from the colour tokens."""
    palette = QPalette()
    role = QPalette.ColorRole
    for color_role, token in ((role.Window, "bg-1"), (role.WindowText, "ink"), (role.Base, "bg-3"),
                              (role.AlternateBase, "bg-2"), (role.Text, "ink"), (role.Button, "bg-3"),
                              (role.ButtonText, "ink"), (role.Highlight, "accent"),
                              (role.HighlightedText, "ink-inverse"), (role.PlaceholderText, "ink-faint"),
                              (role.ToolTipBase, "bg-3"), (role.ToolTipText, "ink")):
        palette.setColor(color_role, color(token))
    palette.setColor(QPalette.ColorGroup.Disabled, role.Text, color("ink-disabled"))
    palette.setColor(QPalette.ColorGroup.Disabled, role.ButtonText, color("ink-disabled"))
    return palette


def apply_theme(application: QApplication) -> QApplication:
    """Load the bundled fonts, then set the Fusion style, the token palette, the body font and the style sheet.

    Returns:
        The application.
    """
    require_type(application, QApplication, "application")
    load_fonts()
    application.setStyle("Fusion")
    application.setPalette(build_palette())
    application.setFont(font("ui-body"))
    application.setStyleSheet(build_qss())
    return application


def repolish(widget: QWidget) -> QWidget:
    """Re-apply the style sheet to a widget after changing a dynamic property used by QSS.

    Returns:
        The widget.
    """
    require_type(widget, QWidget, "widget")
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget
