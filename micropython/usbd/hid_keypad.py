# MicroPython USB keypad module
# MIT license; Copyright (c) 2023 Dave Wickham, Angus Gratton

from .hid import HIDInterface
from micropython import const

_INTERFACE_PROTOCOL_KEYBOARD = const(0x01)

# See HID Usages and Descriptions 1.4, section 10 Keyboard/Keypad Page (0x07)
#
# This keypad example has a contiguous series of keys (KEYPAD_KEY_IDS) starting
# from the NumLock/Clear keypad key (0x53), but you can send any Key IDs from
# the table in the HID Usages specification.
_KEYPAD_KEY_OFFS = const(0x53)

_KEYPAD_KEY_IDS = [
    "<NumLock>",
    "/",
    "*",
    "-",
    "+",
    "<Enter>",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "0",
    ".",
]


def _key_to_id(key):
    # This is a little slower than making a dict for lookup, but uses
    # less memory and O(n) can be fast enough when n is small.
    return _KEYPAD_KEY_IDS.index(key) + _KEYPAD_KEY_OFFS


# fmt: off
_KEYPAD_REPORT_DESC = bytes(
    [
        0x05, 0x01,  # Usage Page (Generic Desktop)
            0x09, 0x07,  # Usage (Keypad)
            0xA1, 0x01,  # Collection (Application)
                0x05, 0x07,  # Usage Page (Keypad)
                    0x19, 0x00,  # Usage Minimum (0)
                    0x29, 0xFF,  # Usage Maximum (ff)
                    0x15, 0x00,  # Logical Minimum (0)
                    0x25, 0xFF,  # Logical Maximum (ff)
                    0x95, 0x01,  # Report Count (1),
                    0x75, 0x08,  # Report Size (8),
                    0x81, 0x00,  # Input (Data, Array, Absolute)
                0x05, 0x08,  # Usage page (LEDs)
                    0x19, 0x01,  # Usage Minimum (1)
                    0x29, 0x01,  # Usage Maximum (1),
                    0x95, 0x01,  # Report Count (1),
                    0x75, 0x01,  # Report Size (1),
                    0x91, 0x02,  # Output (Data, Variable, Absolute)
                    0x95, 0x01,  # Report Count (1),
                    0x75, 0x07,  # Report Size (7),
                    0x91, 0x01,  # Output (Constant) - padding bits
            0xC0,  # End Collection
    ]
)
# fmt: on


class KeypadInterface(HIDInterface):
    # Very basic synchronous USB keypad HID interface

    def __init__(self):
        self.numlock = False
        self.set_report_initialised = False
        super().__init__(
            _KEYPAD_REPORT_DESC,
            set_report_buf=bytearray(1),
            protocol=_INTERFACE_PROTOCOL_KEYBOARD,
            interface_str="MicroPython Keypad",
        )

    def handle_set_report(self, report_data, _report_id, _report_type):
        report = report_data[0]
        b = bool(report & 1)
        if b != self.numlock:
            print("Numlock: ", b)
            self.numlock = b

    def send_key(self, key=None):
        if key is None:
            self.send_report(b"\x00")
        else:
            self.send_report(_key_to_id(key).to_bytes(1, "big"))
