"""Cloud sync UI: the Cloud menu and its dialogs (plan §10; mockups still missing, design-system §11).

Everything here talks to :class:`homebound.ui.sync_manager.SyncManager`; server errors are shown in
the dialog or as a toast, and local editing never depends on any of it.
"""
import webbrowser
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from ..sync.api import ApiError
from ..sync.login import LoopbackLogin
from . import menus
from .dialogs import BaseDialog, PromptDialog, danger_button
from .sync_manager import SyncManager

ROLE_LABELS = {"owner": "Owner", "manager": "Manager", "editor": "Editor", "viewer": "Viewer"}


def isk(amount: float | int | None) -> str:
    """Format ISK the way EVE players read it: 125M, 1.5B."""
    amount = float(amount or 0)
    if amount >= 1e9:
        return f"{amount / 1e9:.2f}B ISK".replace(".00B", "B")
    if amount >= 1e6:
        return f"{amount / 1e6:.0f}M ISK"
    return f"{amount:,.0f} ISK"


def short_date(text: str | None) -> str:
    """Return ``2026-11-08`` from an ISO time (empty for none)."""
    return (text or "")[:10]


def help_label(text: str) -> QLabel:
    """A wrapped help line."""
    label = QLabel(text)
    label.setProperty("role", "help")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def error_label() -> QLabel:
    """A hidden line for server errors."""
    label = QLabel()
    label.setProperty("role", "error")
    label.setWordWrap(True)
    label.hide()
    return label


def show_error(label: QLabel, error: Exception) -> None:
    """Show an error in a dialog's error line."""
    label.setText(getattr(error, "detail", str(error)))
    label.show()


class _LoginBridge(QObject):
    """Moves the code from the loopback thread to the Qt thread."""

    code = Signal(str)


class SignInDialog(BaseDialog):
    """EVE SSO through the browser; the code comes back on a loopback port, or is pasted (plan §6)."""

    def __init__(self, parent: QWidget | None, sync: SyncManager, link: str | None = None) -> None:
        """Open the browser right away."""
        title = "Add a character" if link else "Sign in to cloud sync"
        super().__init__(parent, title, "Log in with EVE Online in your browser. Homebound asks for no scopes: "
                                        "the login only proves which character you are.")
        self.sync = sync
        self.bridge = _LoginBridge()
        self.bridge.code.connect(self._finish)
        self.listener = LoopbackLogin(self.bridge.code.emit)
        self.url = sync.begin_login(self.listener.port, self.listener.state, link)
        self.body.addWidget(help_label("Your browser should open now. If it doesn't, open this link:"))
        link_field = QLineEdit(self.url)
        link_field.setReadOnly(True)
        self.body.addWidget(link_field)
        self.caption("If the browser shows a code, paste it here")
        self.code_field = QLineEdit()
        self.code_field.setPlaceholderText("Code from the browser")
        self.body.addWidget(self.code_field)
        self.error = error_label()
        self.body.addWidget(self.error)
        primary = self.add_buttons(primary_text="Use code")
        primary.clicked.disconnect()
        primary.clicked.connect(lambda: self._finish(self.code_field.text().strip()))
        webbrowser.open(self.url)

    def _finish(self, code: str) -> None:
        """Trade the code for a session."""
        if not code:
            return
        try:
            self.sync.finish_login(code, self.listener.state)
        except ApiError as error:
            show_error(self.error, error)
            return
        self.accept()

    def done(self, result: int) -> None:
        """Stop listening when the dialog closes."""
        self.listener.stop()
        super().done(result)


class MakeCloudMapDialog(BaseDialog):
    """"Make cloud map": upload the open map, billed to you or your corp (plan §15, §16)."""

    def __init__(self, parent: QWidget | None, sync: SyncManager, account: dict) -> None:
        """Show the price and how the map would be paid."""
        name = sync.state.settings.active_map
        super().__init__(parent, f"Make “{name}” a cloud map",
                         "The map is uploaded and members you invite edit it live with you. "
                         "It keeps working offline and merges when you are back.")
        self.sync, self.account = sync, account
        self.caption("Billed to")
        self.payer = QComboBox()
        self.payer.addItem("Me (my balance)", None)
        for character in account.get("characters", []):
            self.payer.addItem(f"My corp, as CEO {character['name']}", character["char_id"])
        self.body.addWidget(self.payer)
        try:
            quote = sync.api.quote(new_map_seats=1)
            price = f"Your monthly total goes from {isk(quote['before'])} to {isk(quote['after'])}."
        except ApiError:
            price = ""
        trial = " This is your first map: it starts the free 3-day trial." if account.get("trial_available") else ""
        self.body.addWidget(help_label(f"Balance {isk(account.get('balance'))}. {price}{trial}"))
        self.error = error_label()
        self.body.addWidget(self.error)
        primary = self.add_buttons(primary_text="Make cloud map")
        primary.clicked.disconnect()
        primary.clicked.connect(self._make)
        self.result_info: dict = {}

    def _make(self) -> None:
        """Upload."""
        char_id = self.payer.currentData()
        try:
            self.result_info = self.sync.make_cloud_map(corp=char_id is not None, char_id=char_id)
        except ApiError as error:
            show_error(self.error, error)
            return
        self.accept()


class InviteDialog(BaseDialog):
    """"Share map": make an invite code with a role, an expiry and a use count (plan §5)."""

    def __init__(self, parent: QWidget | None, sync: SyncManager, map_id: str, my_role: str) -> None:
        """Pick the invite's options."""
        super().__init__(parent, "Invite to this map", "Send the code to the people you invite. "
                                                       "They join with Cloud → Join with invite code.")
        self.sync, self.map_id = sync, map_id
        self.caption("Role")
        self.role = QComboBox()
        for role in (("manager", "editor", "viewer") if my_role == "owner" else ("editor", "viewer")):
            self.role.addItem(ROLE_LABELS[role], role)
        self.role.setCurrentIndex(self.role.findData("editor"))
        self.body.addWidget(self.role)
        row = QHBoxLayout()
        self.hours = QSpinBox(minimum=1, maximum=720, value=72, suffix=" h")
        self.uses = QSpinBox(minimum=1, maximum=100, value=1, suffix=" use(s)")
        row.addWidget(QLabel("Valid for"))
        row.addWidget(self.hours)
        row.addWidget(self.uses)
        self.body.addLayout(row)
        self.body.addWidget(help_label("Every member is a seat, viewers too. Extra seats change the map's price."))
        self.code = QLineEdit()
        self.code.setReadOnly(True)
        self.code.hide()
        self.body.addWidget(self.code)
        self.error = error_label()
        self.body.addWidget(self.error)
        self.primary = self.add_buttons(cancel_text="Close", primary_text="Make code")
        self.primary.clicked.disconnect()
        self.primary.clicked.connect(self._make)

    def _make(self) -> None:
        """Ask the server for a code and copy it."""
        try:
            invite = self.sync.api.invite(self.map_id, self.role.currentData(), self.hours.value(), self.uses.value())
        except ApiError as error:
            show_error(self.error, error)
            return
        self.code.setText(invite["code"])
        self.code.show()
        QGuiApplication.clipboard().setText(invite["code"])
        self.primary.setText("Copied ✓")
        self.primary.setEnabled(False)


class MembersDialog(BaseDialog):
    """Members and roles of a cloud map (owner and managers change them; anyone can leave)."""

    def __init__(self, parent: QWidget | None, sync: SyncManager, map_id: str) -> None:
        """List the members from the server."""
        super().__init__(parent, "Members")
        self.sync, self.map_id = sync, map_id
        self.error = error_label()
        self.body.addWidget(self.error)
        self.member_box = QWidget()
        self.member_layout = QVBoxLayout(self.member_box)
        self.member_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.member_box)
        self.extra = QWidget()
        self.extra_layout = QVBoxLayout(self.extra)
        self.extra_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.extra)
        refresh = self.add_buttons(danger=danger_button("Leave map", self._leave), cancel_text="Close",
                                   primary_text="Refresh")
        refresh.clicked.disconnect()
        refresh.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Reload the member list."""
        while self.member_layout.count():
            widget = self.member_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        while self.extra_layout.count():
            widget = self.extra_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        try:
            details = self.sync.api.map(self.map_id)
        except ApiError as error:
            show_error(self.error, error)
            return
        self.error.hide()
        mine = details["role"]
        me = self.sync.engine.account_id
        for member in details["members"]:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            names = ", ".join(member["names"]) or f"Account {member['account_id']}"
            layout.addWidget(QLabel(names + (" (you)" if member["account_id"] == me else "")), 1)
            can_change = (member["role"] != "owner" and member["account_id"] != me and
                          (mine == "owner" or (mine == "manager" and member["role"] in ("editor", "viewer"))))
            if can_change:
                combo = QComboBox()
                for role in (("manager", "editor", "viewer") if mine == "owner" else ("editor", "viewer")):
                    combo.addItem(ROLE_LABELS[role], role)
                combo.setCurrentIndex(combo.findData(member["role"]))
                combo.activated.connect(lambda _index, combo=combo, account=member["account_id"]:
                                        self._set_role(account, combo.currentData()))
                layout.addWidget(combo)
                remove = QToolButton()
                remove.setText("✕")
                remove.setToolTip("Remove from the map")
                remove.clicked.connect(lambda _checked=False, account=member["account_id"]: self._remove(account))
                layout.addWidget(remove)
            else:
                layout.addWidget(QLabel(ROLE_LABELS[member["role"]] + (" · corp" if member.get("via_corp") else "")))
            self.member_layout.addWidget(row)
        status = details["status"]
        self.extra_layout.addWidget(help_label(
            f"{len(details['members'])} seat(s) · {isk(status.get('price_month'))} a month for this map · "
            f"paid until {short_date(status.get('paid_until')) or '–'}"))
        if details["invites"]:
            self.extra_layout.addWidget(help_label("Open invites: " + ", ".join(
                f"{invite['code']} ({ROLE_LABELS[invite['role']]}, {invite['uses_left']} left)" for invite in details["invites"])))
        if details["map"].get("seat_limit") is not None or status.get("payer_kind") == "corp":
            self.extra_layout.addWidget(help_label(
                "Corp map: corp members may join as viewer " + ("(on, " if details["map"]["corp_join"] else "(off, ")
                + f"seat limit {details['map'].get('seat_limit') or '–'})."))

    def _set_role(self, account_id: int, role: str) -> None:
        """Change a role."""
        try:
            self.sync.api.set_role(self.map_id, account_id, role)
        except ApiError as error:
            show_error(self.error, error)
        self.refresh()

    def _remove(self, account_id: int) -> None:
        """Remove a member."""
        try:
            self.sync.api.remove_member(self.map_id, account_id)
        except ApiError as error:
            show_error(self.error, error)
        self.refresh()

    def _leave(self) -> None:
        """Leave the map; the local copy stays."""
        try:
            self.sync.leave(self.map_id)
        except ApiError as error:
            show_error(self.error, error)
            return
        self.accept()


class AccountDialog(BaseDialog):
    """Linked characters, devices, balance and how to pay, delete account (plan §6, §7)."""

    def __init__(self, parent: QWidget | None, sync: SyncManager) -> None:
        """Load the account from the server."""
        super().__init__(parent, "Cloud account")
        self.setMaximumWidth(560)
        self.sync = sync
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.content)
        self.error = error_label()
        self.body.addWidget(self.error)
        delete = danger_button("Delete account …", self._delete)
        self.add_buttons(danger=delete, cancel_text="Sign out", primary_text="Close")
        cancel = self.foot.itemAt(self.foot.count() - 2).widget()
        cancel.clicked.disconnect()
        cancel.clicked.connect(self._sign_out)
        self.refresh()

    def refresh(self) -> None:
        """Reload."""
        layout = self.content_layout
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        try:
            account = self.sync.api.account()
            sessions = self.sync.api.sessions()
        except ApiError as error:
            show_error(self.error, error)
            return
        layout.addWidget(QLabel("<b>Characters</b>"))
        for character in account["characters"]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(QLabel(character["name"]), 1)
            if len(account["characters"]) > 1:
                unlink = QToolButton()
                unlink.setText("Unlink")
                unlink.clicked.connect(lambda _checked=False, char_id=character["char_id"]: self._unlink(char_id))
                row_layout.addWidget(unlink)
            layout.addWidget(row)
        add = QPushButton("Add a character …")
        add.clicked.connect(self._add_alt)
        layout.addWidget(add)
        layout.addWidget(QLabel("<b>Balance</b>"))
        receiver = account.get("receiver_char_id")
        layout.addWidget(help_label(
            f"{isk(account['balance'])} · {isk(account['price_month'])} a month · paid until "
            f"{short_date(account['paid_until']) or '–'} · state: {account['state']}"))
        layout.addWidget(help_label(
            f"To pay, give ISK in EVE to the Homebound receiver character{f' (ID {receiver})' if receiver else ''} "
            f"from one of your characters. From another character, put your code “{account['payer_code']}” in the "
            "reason. Payments show up within about an hour."))
        layout.addWidget(QLabel("<b>Devices</b>"))
        for session in sessions["sessions"]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            current = session["id"] == sessions["current"]
            row_layout.addWidget(QLabel(f"{session['device'] or 'Device'}{' (this one)' if current else ''} · "
                                        f"last used {short_date(session['last_used_at'])}"), 1)
            if not current:
                out = QToolButton()
                out.setText("Sign out")
                out.clicked.connect(lambda _checked=False, session_id=session["id"]: self._revoke(session_id))
                row_layout.addWidget(out)
            layout.addWidget(row)
        self.error.hide()

    def _add_alt(self) -> None:
        """Link another character with its own SSO login."""
        try:
            link = self.sync.api.link_character()["link_code"]
        except ApiError as error:
            show_error(self.error, error)
            return
        SignInDialog(self, self.sync, link=link).exec()
        self.refresh()

    def _unlink(self, char_id: int) -> None:
        try:
            self.sync.api.unlink_character(char_id)
        except ApiError as error:
            show_error(self.error, error)
        # unlinking revokes the account's sessions: sign in again
        self.sync.sign_out()
        self.accept()

    def _revoke(self, session_id: str) -> None:
        try:
            self.sync.api.revoke(session_id)
        except ApiError as error:
            show_error(self.error, error)
        self.refresh()

    def _sign_out(self) -> None:
        self.sync.sign_out()
        self.accept()

    def _delete(self) -> None:
        """Delete the account after typing DELETE."""
        prompt = PromptDialog(self, "Delete cloud account",
                              "You leave every cloud map (maps you own go to the longest-standing manager or editor) "
                              "and your ISK balance is lost. Your local maps stay.",
                              "Type DELETE to confirm", validate=lambda text: (text == "DELETE", "Type DELETE"),
                              primary=lambda text: "Delete account")
        if prompt.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.sync.api.delete_account()
        except ApiError as error:
            show_error(self.error, error)
            return
        self.sync.sign_out()
        self.accept()


class CloudMenu(QObject):
    """The toolbar's Cloud button and its menu."""

    def __init__(self, window, sync: SyncManager, toast: Callable[[str, str], object]) -> None:
        """Make the button; the menu is filled each time it opens."""
        super().__init__(window)
        self.window, self.sync, self.toast = window, sync, toast
        self.button = QToolButton()
        self.button.setText("☁ Cloud")
        self.button.setToolTip("Cloud sync: share maps with your group")
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        self.menu = menus.menu(self.button)
        self.menu.aboutToShow.connect(self.fill)
        self.button.setMenu(self.menu)
        sync.sessionProblem.connect(self._session_problem)

    def fill(self) -> QMenu:
        """Build the menu for the current state."""
        menu, sync = self.menu, self.sync
        menu.clear()
        if not sync.signed_in:
            menus.item(menu, "Sign in with EVE Online …", self.sign_in)
            menus.item(menu, "Cloud maps need a signed-in account", enabled=False)
            return menu
        map_id = sync.active_map_id
        info = sync.active_info()
        menus.section(menu, "This map")
        if map_id is None:
            menus.item(menu, "Make cloud map …", self.make_cloud_map)
        else:
            role = info.role if info else None
            menus.item(menu, "Invite …", lambda: self.invite(map_id, role), enabled=role in ("owner", "manager"),
                       why="Only the owner and managers can invite.")
            menus.item(menu, "Members …", lambda: MembersDialog(self.window, sync, map_id).exec())
            menus.item(menu, "Reset to shared layout", sync.reset_layout)
            characters = sync.engine.characters
            if characters:
                menus.section(menu, "Share my location")
                shared = set(sync.shared_characters(map_id))
                for character in characters:
                    menus.item(menu, character["name"],
                               lambda char_id=character["char_id"], on=character["char_id"] not in shared:
                               sync.set_sharing(map_id, char_id, on), checked=character["char_id"] in shared)
        menu.addSeparator()
        menus.item(menu, "Join with invite code …", self.join)
        menus.item(menu, "Account and billing …", lambda: AccountDialog(self.window, sync).exec())
        return menu

    def sign_in(self) -> bool:
        """Run the sign-in dialog."""
        return SignInDialog(self.window, self.sync).exec() == QDialog.DialogCode.Accepted

    def make_cloud_map(self) -> None:
        """Ask and upload."""
        try:
            account = self.sync.api.account()
        except ApiError as error:
            self.toast("err", error.detail)
            return
        dialog = MakeCloudMapDialog(self.window, self.sync, account)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            how = dialog.result_info.get("billing")
            self.toast("ok", "Cloud map made" + (": free 3-day trial started" if how == "trial" else ""))

    def invite(self, map_id: str, role: str | None) -> None:
        """Make an invite code."""
        InviteDialog(self.window, self.sync, map_id, role or "editor").exec()

    def join(self) -> None:
        """Join a map with a code."""
        prompt = PromptDialog(self.window, "Join a cloud map", "The map is added to your map menu. "
                              "A local map with the same home stays as it is.", "Invite code",
                              validate=lambda text: (len(text.strip()) >= 6, "Paste the code you were sent"),
                              primary=lambda text: "Join", placeholder="INV-XXXXXX")
        if prompt.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            name = self.sync.join_with_code(prompt.text())
        except ApiError as error:
            self.toast("err", error.detail)
            return
        self.toast("ok", f"Joined “{name}”. Pick it in the map menu.")

    def _session_problem(self, code: str) -> None:
        """Explain a session problem and offer what fits."""
        if code == "upgrade_required":
            self.toast("warn", "Please update Homebound to keep syncing. Local editing keeps working.")
        elif code == "relogin_due":
            prompt = BaseDialog(self.window, "Sign in again", "Your cloud session needs a fresh EVE login. "
                                                              "You can put this off once, for 36 hours.")
            prompt.add_buttons(cancel_text="Do it later", primary_text="Sign in")
            if prompt.exec() == QDialog.DialogCode.Accepted:
                self.sign_in()
            else:
                try:
                    until = self.sync.defer_login()
                    self.toast("info", f"Sign-in put off until {until[:16].replace('T', ' ')} UTC")
                except ApiError as error:
                    self.toast("warn", error.detail)
        else:
            self.toast("warn", "Signed out of cloud sync. Your maps keep working locally.")
