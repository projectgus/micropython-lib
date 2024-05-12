# MicroPython USB keypad module
# MIT license; Copyright (c) 2023 Dave Wickham

from .hid import HIDInterface
from .keycodes import KEYPAD_KEYS_TO_KEYCODES
from .utils import STAGE_SETUP, split_bmRequestType
from micropython import const
import micropython

_INTERFACE_PROTOCOL_KEYBOARD = const(0x01)
_REQ_CONTROL_SET_REPORT = const(0x09)
_REQ_CONTROL_SET_IDLE = const(0x0A)

# fmt: off
_KEYPAD_REPORT_DESC = bytes(
    [
        0x05, 0x01,  # Usage Page (Generic Desktop)
            0x09, 0x07,  # Usage (Keypad)
            0xA1, 0x01,  # Collection (Application)
                0x05, 0x07,  # Usage Page (Keypad)
                0x19, 0x00,  # Usage Minimum (00),
                0x29, 0xFF,  # Usage Maximum (ff),
                0x15, 0x00,  # Logical Minimum (0),
                0x25, 0xFF,  # Logical Maximum (ff),
                0x95, 0x01,  # Report Count (1),
                0x75, 0x08,  # Report Size (8),
                0x81, 0x00,  # Input (Data, Array, Absolute)
                0x05, 0x08,  # Usage page (LEDs)
                0x19, 0x01,  # Usage minimum (1)
                0x29, 0x05,  # Usage Maximum (5),
                0x95, 0x05,  # Report Count (5),
                0x75, 0x01,  # Report Size (1),
                0x91, 0x02,  # Output (Data, Variable, Absolute)
                0x95, 0x01,  # Report Count (1),
                0x75, 0x03,  # Report Size (3),
                0x91, 0x01,  # Output (Constant)
            0xC0,  # End Collection
    ]
)
# fmt: on


class KeypadInterface(HIDInterface):
    # Very basic synchronous USB keypad HID interface

    def __init__(self):
        self.numlock = None
        self.capslock = None
        self.scrolllock = None
        self.compose = None
        self.kana = None
        self.set_report_initialised = False
        super().__init__(
            _KEYPAD_REPORT_DESC,
            protocol=_INTERFACE_PROTOCOL_KEYBOARD,
            interface_str="MicroPython Keypad!",
            use_out_ep=True,
        )

    def handle_interface_control_xfer(self, stage, request):
        if request[1] == _REQ_CONTROL_SET_IDLE and not self.set_report_initialised:
            # Hacky initialisation goes here
            self.set_report()
            self.set_report_initialised = True

        if stage == STAGE_SETUP:
            return super().handle_interface_control_xfer(stage, request)

        bmRequestType, bRequest, wValue, _, _ = request
        recipient, req_type, _ = split_bmRequestType(bmRequestType)

        return True

    def set_report(self, args=None):
        self.out_buffer = bytearray(1)
        self.submit_xfer(self._out_ep, self.out_buffer, self.set_report_cb)
        return True

    def set_report_cb(self, ep_addr, result, xferred_bytes):
        buf_result = int(self.out_buffer[0])
        self.numlock = buf_result & 1
        self.capslock = (buf_result >> 1) & 1
        self.scrolllock = (buf_result >> 2) & 1
        self.compose = (buf_result >> 3) & 1
        self.kana = (buf_result >> 4) & 1

        micropython.schedule(self.set_report, None)

    def send_report(self, key=None):
        if key is None:
            super().send_report(bytes(1))
        else:
            super().send_report(KEYPAD_KEYS_TO_KEYCODES[key].to_bytes(1, "big"))
