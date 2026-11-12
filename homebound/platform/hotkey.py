"""The global overlay shortcut: a key that works while EVE has the focus (docs/plans/done/plan-overlay-shortcut.md).

``QShortcut`` only fires while Homebound has the focus and Qt has no global hotkey API, so each platform has
a backend: ``RegisterHotKey`` on Windows, the KDE global shortcut service (``org.kde.kglobalaccel``) on Linux.
Both report a failed registration (key taken, no such service) as text instead of raising, so the app
keeps running without the shortcut. Wayland doesn't let an app grab keys itself; other desktops than KDE get
"not available" for now.
"""
import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QMetaType, QObject, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence

from ..validation import require_text

DEFAULT_SHORTCUT = "Ctrl+Shift+O"
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x4842                  # "HB"
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
BUS_NAME, BUS_PATH, BUS_INTERFACE = "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel"
COMPONENT_INTERFACE = "org.kde.kglobalaccel.Component"
ACTION_ID = ["homebound", "cycle-overlay", "Homebound", "Cycle overlay mode"]
# Qt modifier -> Win32 modifier
_WINDOWS_MODIFIERS = ((Qt.KeyboardModifier.ControlModifier, MOD_CONTROL), (Qt.KeyboardModifier.ShiftModifier, MOD_SHIFT),
                      (Qt.KeyboardModifier.AltModifier, MOD_ALT), (Qt.KeyboardModifier.MetaModifier, MOD_WIN))


def parse_shortcut(text: str) -> tuple[Qt.KeyboardModifier, int, int] | None:
    """Split a shortcut text such as ``Ctrl+Shift+O`` into modifiers and key.

    A usable shortcut is one key (a letter, a digit or F1 to F12) with Ctrl, Alt or Meta, plus Shift if wanted.
    Shift alone would take a plain capital letter away from every other program.

    Args:
        text: The shortcut as the user typed it.

    Returns:
        ``(modifiers, Qt key, combined key code)``, or None if the text is empty, several keys or unusable.
    """
    require_text(text, "text", allow_empty=True)
    sequence = QKeySequence(text.strip(), QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        return None
    combined = sequence[0]  # type: ignore[index]  # PySide6 stub lacks QKeySequence.__getitem__; it returns a QKeyCombination
    modifiers = combined.keyboardModifiers() & ~Qt.KeyboardModifier.KeypadModifier
    key = combined.key()
    letter_or_digit = Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9
    function = Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12
    needed = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
    if not (letter_or_digit or function) or not modifiers & needed:
        return None
    return modifiers, int(key), combined.toCombined()


def normalize_shortcut(text: str) -> str:
    """Return a shortcut text in its portable form (``ctrl+shift+o`` becomes ``Ctrl+Shift+O``), or ``""`` if unusable."""
    parsed = parse_shortcut(text)
    if parsed is None:
        return ""
    return QKeySequence(parsed[2]).toString(QKeySequence.SequenceFormat.PortableText)


def windows_modifiers(modifiers: Qt.KeyboardModifier) -> int:
    """Return the Win32 ``fsModifiers`` for Qt modifiers (without ``MOD_NOREPEAT``)."""
    return sum(flag for qt_flag, flag in _WINDOWS_MODIFIERS if modifiers & qt_flag)


def windows_virtual_key(key: int) -> int:
    """Return the Win32 virtual-key code of a letter, digit or F-key (these are the ASCII code and 0x70 + n)."""
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
        return 0x70 + key - Qt.Key.Key_F1
    return key


class _MessageFilter(QAbstractNativeEventFilter):
    """Passes ``WM_HOTKEY`` thread messages of our hotkey on."""

    def __init__(self, pressed: Callable[[], None]) -> None:
        super().__init__()
        self.pressed = pressed

    def handle(self, message: int, w_param: int) -> bool:
        """Call ``pressed`` for our hotkey; return whether the message was one."""
        if message == WM_HOTKEY and w_param == HOTKEY_ID:
            self.pressed()
            return True
        return False

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt name)
        """Read the MSG behind the pointer Qt gives for ``windows_generic_MSG`` events."""
        if event_type == b"windows_generic_MSG" and message is not None:
            msg = wintypes.MSG.from_address(int(message))
            if self.handle(msg.message, msg.wParam):
                return True, 0
        return False, 0


class WindowsBackend:
    """``RegisterHotKey`` on the application's message queue."""

    def __init__(self, pressed: Callable[[], None], user32=None) -> None:
        """Remember the callback; ``user32`` is a stand-in in tests."""
        self.user32 = user32 if user32 is not None else getattr(getattr(ctypes, "windll", None), "user32", None)
        self.filter = _MessageFilter(pressed)
        self.registered = False

    def register(self, modifiers: Qt.KeyboardModifier, key: int, _combined: int) -> str:
        """Register the key.

        Returns:
            ``""`` on success, else why not.
        """
        if self.user32 is None:
            return "not available here"
        self.unregister()
        if not self.user32.RegisterHotKey(None, HOTKEY_ID, windows_modifiers(modifiers) | MOD_NOREPEAT,
                                          windows_virtual_key(key)):
            code = getattr(ctypes, "GetLastError", lambda: 0)()
            return "the key is already used by another program" if code == ERROR_HOTKEY_ALREADY_REGISTERED \
                else "Windows refused the key"
        if not self.registered:
            QCoreApplication.instance().installNativeEventFilter(self.filter)
        self.registered = True
        return ""

    def unregister(self) -> None:
        """Release the key."""
        if self.registered and self.user32 is not None:
            self.user32.UnregisterHotKey(None, HOTKEY_ID)
            QCoreApplication.instance().removeNativeEventFilter(self.filter)
        self.registered = False


class KdeBackend(QObject):
    """The KDE global shortcut service over D-Bus (works on Wayland and X11 under Plasma)."""

    def __init__(self, pressed: Callable[[], None], bus=None) -> None:
        """Remember the callback; ``bus`` is a stand-in in tests (default: the session bus)."""
        super().__init__()
        from PySide6.QtDBus import QDBusConnection
        self.pressed = pressed
        self.bus = bus if bus is not None else QDBusConnection.sessionBus()
        self.registered = False

    def _call(self, method: str, *arguments):
        """Call a method of the service; return the reply, or None if the service isn't there or refused."""
        from PySide6.QtDBus import QDBusMessage
        message = QDBusMessage.createMethodCall(BUS_NAME, BUS_PATH, BUS_INTERFACE, method)
        message.setArguments(list(arguments))
        reply = self.bus.call(message)
        return reply if reply.type() == QDBusMessage.MessageType.ReplyMessage else None

    @staticmethod
    def _keys(combined: int):
        """Return the key list argument (``ai``) for a combined key code."""
        from PySide6.QtDBus import QDBusArgument
        argument = QDBusArgument()
        argument.beginArray(QMetaType.Type.Int.value)
        argument.appendVariant(int(combined))
        argument.endArray()
        return argument

    def register(self, _modifiers: Qt.KeyboardModifier, _key: int, combined: int) -> str:
        """Register the key as the action ``cycle-overlay`` of the component ``homebound``.

        The service keeps the binding when another action holds the key already (it then ends up with
        no key), so the key is read back to see whether it stuck.

        Returns:
            ``""`` on success, else why not.
        """
        if not self.bus.isConnected() or self._call("doRegister", ACTION_ID) is None:
            return "this desktop has no KDE global shortcut service"
        component = self._call("getComponent", "homebound")
        if component is None or not component.arguments():
            return "the shortcut service refused the component"
        if self._call("setForeignShortcut", ACTION_ID, self._keys(combined)) is None:
            return "the shortcut service refused the key"
        stored = self._call("shortcut", ACTION_ID)
        if stored is not None and stored.arguments() and combined not in list(stored.arguments()[0] or []):
            return "the key is already used by another shortcut"
        if not self.registered:
            self.bus.connect(BUS_NAME, component.arguments()[0].path(), COMPONENT_INTERFACE,
                             "globalShortcutPressed", self, "1key_pressed(QString,QString,qlonglong)")
        self.registered = True
        return ""

    @Slot(str, str, "qlonglong")
    def key_pressed(self, _component: str, action: str, _timestamp: int) -> None:
        """The service reports a key press of one of our actions."""
        if action == ACTION_ID[1]:
            self.pressed()

    def unregister(self) -> None:
        """Release the key (the action stays known to the service, without a key)."""
        if self.registered:
            self._call("unregister", ACTION_ID[0], ACTION_ID[1])
        self.registered = False


class GlobalHotkey(QObject):
    """One global key: ``pressed`` fires when it is hit anywhere, ``text`` is the key once registered."""

    pressed = Signal()

    def __init__(self, backend_factory: Callable[[Callable[[], None]], object] | None = None) -> None:
        """Pick the backend for this platform (or the factory a test passes in)."""
        super().__init__()
        factory = backend_factory or _platform_backend
        self._backend = factory(self.pressed.emit)
        self.text = ""

    def set_shortcut(self, text: str) -> str:
        """Register ``text`` (replacing the previous key); an empty text only releases the key.

        Args:
            text: A shortcut such as ``Ctrl+Shift+O``.

        Returns:
            ``""`` when the key is registered (or ``text`` is empty), else the reason it isn't.
        """
        require_text(text, "text", allow_empty=True)
        if not text.strip():
            self.release()
            return ""
        parsed = parse_shortcut(text)
        if parsed is None:
            self.release()
            return "needs Ctrl, Alt or Meta plus one letter, digit or F-key"
        if self._backend is None:
            self.text = ""
            return "not available on this system"
        try:
            reason = self._backend.register(*parsed)
        except Exception as error:   # a backend must never take the app down  # noqa: BLE001
            reason = f"registering failed ({error})"
        self.text = "" if reason else normalize_shortcut(text)
        return reason

    def release(self) -> None:
        """Give the key back."""
        if self._backend is not None:
            self._backend.unregister()
        self.text = ""


def _platform_backend(pressed: Callable[[], None]):
    """Return the backend of this platform, or None where there is none."""
    if sys.platform == "win32":
        return WindowsBackend(pressed)
    if sys.platform.startswith("linux"):
        return KdeBackend(pressed)
    return None
