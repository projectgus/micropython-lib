# MicroPython USB hid module
# MIT license; Copyright (c) 2023 Angus Gratton
from .device import (
    USBInterface,
)
from .utils import (
    endpoint_descriptor,
    split_bmRequestType,
    EP_IN_FLAG,
    STAGE_SETUP,
    STAGE_DATA,
    REQ_TYPE_STANDARD,
    REQ_TYPE_CLASS,
)
from micropython import const
import struct

_DESC_HID_TYPE = const(0x21)
_DESC_REPORT_TYPE = const(0x22)
_DESC_PHYSICAL_TYPE = const(0x23)

_INTERFACE_CLASS = const(0x03)
_INTERFACE_SUBCLASS_NONE = const(0x00)
_INTERFACE_SUBCLASS_BOOT = const(0x01)

_INTERFACE_PROTOCOL_NONE = const(0x00)
_INTERFACE_PROTOCOL_KEYBOARD = const(0x01)
_INTERFACE_PROTOCOL_MOUSE = const(0x02)

# bRequest values for HID control requests
_REQ_CONTROL_GET_REPORT = const(0x01)
_REQ_CONTROL_GET_IDLE = const(0x02)
_REQ_CONTROL_GET_PROTOCOL = const(0x03)
_REQ_CONTROL_GET_DESCRIPTOR = const(0x06)
_REQ_CONTROL_SET_REPORT = const(0x09)
_REQ_CONTROL_SET_IDLE = const(0x0A)
_REQ_CONTROL_SET_PROTOCOL = const(0x0B)


class HIDInterface(USBInterface):
    # Abstract base class to implement a USB device HID interface in Python.

    def __init__(
        self,
        report_descriptor,
        extra_descriptors=[],
        set_report_buf=None,
        protocol=_INTERFACE_PROTOCOL_NONE,
        interface_str=None,
        use_out_ep=False,
    ):
        # Construct a new HID interface.
        #
        # - report_descriptor is the only mandatory argument, which is the binary
        # data consisting of the HID Report Descriptor. See Device Class
        # Definition for Human Interface Devices (HID) v1.11 section 6.2.2 Report
        # Descriptor, p23.
        #
        # - extra_descriptors is an optional argument holding additional HID
        #   descriptors, to append after the mandatory report descriptor. Most
        #   HID devices do not use these.
        #
        # - set_report_buf is an optional writable buffer object (i.e.
        #   bytearray), where SET_REPORT requests from the host can be
        #   written. Only necessary if the report_descriptor contains Output
        #   entries. If set, the size must be at least the size of the largest
        #   Output entry.
        #
        # - protocol can be set to a specific value as per HID v1.11 section 4.3 Protocols, p9.
        #
        # - interface_str is an optional string descriptor to associate with the HID USB interface.
        #
        # - use_out_ep needs to be set to True if you're using the OUT endpoint, e.g. to get
        #   keyboard LEDs
        super().__init__(_INTERFACE_CLASS, _INTERFACE_SUBCLASS_NONE, protocol, interface_str)
        self.extra_descriptors = extra_descriptors
        self.report_descriptor = report_descriptor
        self._set_report_buf = set_report_buf
        self._int_ep = None  # set during enumeration
        self._out_ep = None
        self.use_out_ep = use_out_ep

    def get_report(self):
        return False

    def handle_set_report(self, report_data, report_id, report_type):
        # Override this function in order to handle SET REPORT requests from the host,
        # where it sends data to the HID device.
        #
        # This function will only be called if the Report descriptor contains at least one Output entry,
        # and the set_report_buf argument is provided to the constructor.
        #
        # Return True to complete the control transfer normally, False to abort it.
        return True

    def send_report(self, report_data):
        # Helper function to send a HID report in the typical USB interrupt
        # endpoint associated with a HID interface.  return
        self.submit_xfer(self._int_ep, report_data)

    def get_endpoint_descriptors(self, ep_addr, str_idx):
        # Return the typical single USB interrupt endpoint descriptor associated
        # with a HID interface.
        #
        # As per HID v1.11 section 7.1 Standard Requests, return the contents of
        # the standard HID descriptor before the associated endpoint descriptor.
        desc = self.get_hid_descriptor()
        self._int_ep = ep_addr | EP_IN_FLAG
        ep_addrs = [self._int_ep]

        desc += endpoint_descriptor(self._int_ep, "interrupt", 8, 8)

        if self.use_out_ep:
            self._out_ep = (ep_addr + 1) & ~EP_IN_FLAG
            desc += endpoint_descriptor(self._out_ep, "interrupt", 8, 8)
            ep_addrs.append(self._out_ep)

        self.idle_rate = 0
        self.protocol = 0
        return (desc, [], ep_addrs)

    def get_hid_descriptor(self):
        # Generate a full USB HID descriptor from the object's report descriptor
        # and optional additional descriptors.
        #
        # See HID Specification Version 1.1, Section 6.2.1 HID Descriptor p22
        result = struct.pack(
            "<BBHBBBH",
            9 + 3 * len(self.extra_descriptors),  # bLength
            _DESC_HID_TYPE,  # bDescriptorType
            0x111,  # bcdHID
            0,  # bCountryCode
            len(self.extra_descriptors) + 1,  # bNumDescriptors
            0x22,  # bDescriptorType, Report
            len(self.report_descriptor),  # wDescriptorLength, Report
        )
        # Fill in any additional descriptor type/length pairs
        #
        # TODO: unclear if this functionality is ever used, may be easier to not
        # support in base class
        if self.extra_descriptors:
            result += b"".join(
                struct.pack("<BH", dt, len(dd)) for (dt, dd) in self.extra_descriptors
            )

        return result

    def handle_interface_control_xfer(self, stage, request):
        # Handle standard and class-specific interface control transfers for HID devices.
        bmRequestType, bRequest, wValue, _, wLength = request

        recipient, req_type, _ = split_bmRequestType(bmRequestType)

        if stage == STAGE_SETUP:
            if req_type == REQ_TYPE_STANDARD:
                # HID Spec p48: 7.1 Standard Requests
                if bRequest == _REQ_CONTROL_GET_DESCRIPTOR:
                    desc_type = wValue >> 8
                    if desc_type == _DESC_HID_TYPE:
                        return self.get_hid_descriptor()
                    if desc_type == _DESC_REPORT_TYPE:
                        return self.report_descriptor
            elif req_type == REQ_TYPE_CLASS:
                # HID Spec p50: 7.2 Class-Specific Requests
                if bRequest == _REQ_CONTROL_GET_REPORT:
                    print("GET_REPORT?")
                    return False  # Unsupported for now
                if bRequest == _REQ_CONTROL_GET_IDLE:
                    return bytes([self.idle_rate])
                if bRequest == _REQ_CONTROL_GET_PROTOCOL:
                    return bytes([self.protocol])
                if bRequest == _REQ_CONTROL_SET_IDLE:
                    self.idle_rate = wValue >> 8
                    return b""
                if bRequest == _REQ_CONTROL_SET_PROTOCOL:
                    self.protocol = wValue
                    return b""
                if bRequest == _REQ_CONTROL_SET_REPORT:
                    # Return the _set_report_buf to be filled with the
                    # report data
                    if not self._set_report_buf:
                        return False
                    elif wLength >= len(self._set_report_buf):
                        # Saves an allocation if the size is exactly right (or will be a short read)
                        return self._set_report_buf
                    else:
                        # Otherwise, need to wrap the buffer in a memoryview of the correct length
                        #
                        # TODO: check this is correct, maybe TinyUSB won't mind if we ask for more
                        # bytes than the host has offered us.
                        return memoryview(self._set_report_buf)[:wLength]
            return False  # Unsupported

        if stage == STAGE_DATA:
            if req_type == REQ_TYPE_CLASS:
                if bRequest == _REQ_CONTROL_SET_REPORT and self._set_report_buf:
                    report_id = wValue & 0xFF
                    report_type = wValue >> 8
                    report_data = self._set_report_buf
                    if wLength < len(report_data):
                        # as above, need to truncate the buffer if we read less
                        # bytes than what was provided
                        report_data = memoryview(self._set_report_buf)[:wLength]
                    self.handle_set_report(report_data, report_id, report_type)

        return True  # allow DATA/ACK stages to complete normally


# Basic 3-button mouse HID Report Descriptor.
# This is cribbed from Appendix E.10 of the HID v1.11 document.
_MOUSE_REPORT_DESC = bytes(
    [
        0x05,
        0x01,  # Usage Page (Generic Desktop)
        0x09,
        0x02,  # Usage (Mouse)
        0xA1,
        0x01,  # Collection (Application)
        0x09,
        0x01,  # Usage (Pointer)
        0xA1,
        0x00,  # Collection (Physical)
        0x05,
        0x09,  # Usage Page (Buttons)
        0x19,
        0x01,  # Usage Minimum (01),
        0x29,
        0x03,  # Usage Maximun (03),
        0x15,
        0x00,  # Logical Minimum (0),
        0x25,
        0x01,  # Logical Maximum (1),
        0x95,
        0x03,  # Report Count (3),
        0x75,
        0x01,  # Report Size (1),
        0x81,
        0x02,  # Input (Data, Variable, Absolute), ;3 button bits
        0x95,
        0x01,  # Report Count (1),
        0x75,
        0x05,  # Report Size (5),
        0x81,
        0x01,  # Input (Constant), ;5 bit padding
        0x05,
        0x01,  # Usage Page (Generic Desktop),
        0x09,
        0x30,  # Usage (X),
        0x09,
        0x31,  # Usage (Y),
        0x15,
        0x81,  # Logical Minimum (-127),
        0x25,
        0x7F,  # Logical Maximum (127),
        0x75,
        0x08,  # Report Size (8),
        0x95,
        0x02,  # Report Count (2),
        0x81,
        0x06,  # Input (Data, Variable, Relative), ;2 position bytes (X & Y)
        0xC0,  # End Collection,
        0xC0,  # End Collection
    ]
)


class MouseInterface(HIDInterface):
    # Very basic synchronous USB mouse HID interface
    # TODO: This should be in a different package or an example

    def __init__(self):
        super().__init__(
            _MOUSE_REPORT_DESC,
            protocol=_INTERFACE_PROTOCOL_MOUSE,
            interface_str="MP Mouse!",
        )
        self._l = False  # Left button
        self._m = False  # Middle button
        self._r = False  # Right button

    def send_report(self, dx=0, dy=0):
        b = 0
        if self._l:
            b |= 1 << 0
        if self._r:
            b |= 1 << 1
        if self._m:
            b |= 1 << 2
        # Note: This allocates the bytes object 'report' each time a report is
        # sent.
        #
        # However, at the moment the base class doesn't keep track of each
        # transfer after it's submitted. So reusing a bytearray() creates a risk
        # of a race condition if a new report transfer is submitted using the
        # same buffer, before the previous one has completed.
        report = struct.pack("Bbb", b, dx, dy)

        super().send_report(report)

    def click_left(self, down=True):
        self._l = down
        self.send_report()

    def click_middle(self, down=True):
        self._m = down
        self.send_report()

    def click_right(self, down=True):
        self._r = down
        self.send_report()

    def move_by(self, dx, dy):
        # dx, dy are -127, 127 in range
        self.send_report(dx, dy)
