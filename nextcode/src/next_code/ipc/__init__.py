"""IPC module — dual-process communication between Python Core and Ink Frontend."""

from .transport import IPCTransport
from .protocol import MessageType, Message
from .bridge import IPCBridge
from .ink_launcher import InkLauncher
from .fallback import check_ink_available, InkMode

__all__ = [
    "IPCTransport",
    "MessageType",
    "Message",
    "IPCBridge",
    "InkLauncher",
    "check_ink_available",
    "InkMode",
]