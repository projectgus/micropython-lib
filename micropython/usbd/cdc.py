# MicroPython USB CDC module
# MIT license; Copyright (c) 2022 Martin Fischer
from .device import (
    USBInterface,
    get_usbdevice
)
from .utils import (
    endpoint_descriptor,
    split_bmRequestType,
    STAGE_SETUP,
    STAGE_DATA,
    STAGE_ACK,
    REQ_TYPE_STANDARD,
    REQ_TYPE_CLASS,
    EP_IN_FLAG
)
from micropython import const
import ustruct
import time

_DEV_CLASS_MISC = const(0xef)
_CS_DESC_TYPE = const(0x24)   # CS Interface type communication descriptor
_ITF_ASSOCIATION_DESC_TYPE = const(0xb)  # Interface Association descriptor

# CDC control interface definitions
_INTERFACE_CLASS_CDC = const(2)
_INTERFACE_SUBCLASS_CDC = const(2)  # Abstract Control Mode
_PROTOCOL_NONE = const(0)   # no protocol

# CDC descriptor subtype
# see also CDC120.pdf, table 13
_CDC_FUNC_DESC_HEADER = const(0)
_CDC_FUNC_DESC_CALL_MANAGEMENT = const(1)
_CDC_FUNC_DESC_ABSTRACT_CONTROL = const(2)
_CDC_FUNC_DESC_UNION = const(6)

# CDC class requests, table 13, PSTN subclass
_SET_LINE_CODING_REQ = const(0x20)
_GET_LINE_CODING_REQ = const(0x21)
_SET_CONTROL_LINE_STATE = const(0x22)
_SEND_BREAK_REQ = const(0x23)

_LINE_CODING_STOP_BIT_1 = const(0)
_LINE_CODING_STOP_BIT_1_5 = const(1)
_LINE_CODING_STOP_BIT_2 = const(2)


_LINE_CODING_PARITY_NONE = const(0)
_LINE_CODING_PARITY_ODD = const(1)
_LINE_CODING_PARITY_EVEN = const(2)
_LINE_CODING_PARITY_MARK = const(3)
_LINE_CODING_PARITY_SPACE = const(4)

parity_bits_repr = ['N', 'O', 'E', 'M', 'S']
stop_bits_repr = ['1', '1.5', '2']

# Other definitions
_CDC_VERSION = const(0x0120)  # release number in binary-coded decimal


# CDC data interface definitions
_CDC_ITF_DATA_CLASS = const(0xa)
_CDC_ITF_DATA_SUBCLASS = const(0)
_CDC_ITF_DATA_PROT = const(0)   # no protocol


def setup_CDC_device():
    # CDC is a composite device, consisting of multiple interfaces
    # (CDC control and CDC data)
    # therefore we have to make sure that the association descriptor
    # is set and that it associates both interfaces to the logical cdc class
    usb_device = get_usbdevice()
    usb_device.device_class = _DEV_CLASS_MISC
    usb_device.device_subclass = 2
    usb_device.device_protocol = 1   # Itf association descriptor


class CDCControlInterface(USBInterface):
    # Implements the CDC Control Interface

    def __init__(self, _):
        super().__init__(_INTERFACE_CLASS_CDC, _INTERFACE_SUBCLASS_CDC, _PROTOCOL_NONE)
        self.rts = None
        self.dtr = None
        self.baudrate = None
        self.stop_bits = 0
        self.parity = 0
        self.data_bits = None
        self.line_coding_state = bytearray(7)

    def get_itf_descriptor(self, num_eps, itf_idx, str_idx):
        # CDC needs a Interface Association Descriptor (IAD)
        # two interfaces in total
        desc = ustruct.pack("<BBBBBBBB",
                            8,
                            _ITF_ASSOCIATION_DESC_TYPE,
                            itf_idx,
                            2,
                            _INTERFACE_CLASS_CDC,
                            _INTERFACE_SUBCLASS_CDC,
                            _PROTOCOL_NONE,
                            0)

        itf, strs = super().get_itf_descriptor(num_eps, itf_idx, str_idx)
        desc += itf
        # Append the CDC class-specific interface descriptor
        # see CDC120-track, p20
        desc += ustruct.pack("<BBBH",
                             5,  # bFunctionLength
                             _CS_DESC_TYPE,  # bDescriptorType
                             _CDC_FUNC_DESC_HEADER,  # bDescriptorSubtype
                             _CDC_VERSION)  # cdc version

        # CDC-PSTN table3 "Call Management"
        # set to No
        desc += ustruct.pack("<BBBBB",
                             5,  # bFunctionLength
                             _CS_DESC_TYPE,  # bDescriptorType
                             _CDC_FUNC_DESC_CALL_MANAGEMENT,  # bDescriptorSubtype
                             0,  # bmCapabilities - XXX no call managment so far
                             1)  # bDataInterface - interface 1

        # CDC-PSTN table4 "Abstract Control"
        # set to support line_coding and send_break
        desc += ustruct.pack("<BBBB", 
                             4,  # bFunctionLength
                             _CS_DESC_TYPE,  # bDescriptorType
                             _CDC_FUNC_DESC_ABSTRACT_CONTROL,  # bDescriptorSubtype
                             0x6)  # bmCapabilities D1, D2 
        # CDC-PSTN "Union"
        # set control interface / data interface number
        desc += ustruct.pack("<BBBH",
                             5,  # bFunctionLength
                             _CS_DESC_TYPE,  # bDescriptorType
                             _CDC_FUNC_DESC_UNION,  # bDescriptorSubtype
                             itf_idx,  # bControlInterface
                             itf_idx+1)  # bSubordinateInterface0 (data class itf number)
        return desc, strs

    def get_endpoint_descriptors(self, ep_addr, str_idx):
        self.ep_in = endpoint_descriptor((ep_addr + 1) | EP_IN_FLAG, "interrupt", 8, 16)
        return (self.ep_in, [], ((ep_addr+1) | EP_IN_FLAG,))

    def handle_interface_control_xfer(self, stage, request):
        # Handle standard and class-specific interface control transfers 
        bmRequestType, bRequest, wValue, _, wLength = request
        recipient, req_type, req_dir = split_bmRequestType(bmRequestType)
        if stage == STAGE_SETUP:
            if req_type == REQ_TYPE_CLASS:
                if bRequest == _SET_LINE_CODING_REQ:
                    # XXX check against wLength
                    return self.line_coding_state
                elif bRequest == _GET_LINE_CODING_REQ:
                    return self.line_coding_state
                elif bRequest == _SET_CONTROL_LINE_STATE:
                    # DTR = BIT0, RTS = BIT1
                    self.dtr = bool(wValue & 0x1)
                    self.rts = bool(wValue & 0x2)
                    return b""

        if stage == STAGE_DATA:
            if req_type == REQ_TYPE_CLASS:
                if bRequest == _SET_LINE_CODING_REQ:
                    # Byte 0-3   Byte 4      Byte 5       Byte 6
                    # dwDTERate  bCharFormat bParityType  bDataBits
                    self.baudrate, self.stop_bits, self.parity, self.data_bits = ustruct.unpack(
                        '<LBBB', self.line_coding_state)
        return True

    def get_control_line(self):
        return self.dtr, self.rts

    def __repr__(self):
        return f"{self.baudrate}/{self.data_bits}/{parity_bits_repr[self.parity]}/{stop_bits_repr[self.stop_bits]} rts={self.rts} dtr={self.dtr} "


class CDCDataInterface(USBInterface):
    # Implements the CDC Data Interface

    def __init__(self, interface_str, timeout=1):
        super().__init__(_CDC_ITF_DATA_CLASS, _CDC_ITF_DATA_SUBCLASS,
                         _CDC_ITF_DATA_PROT)
        self.rx_buf = bytearray(64)
        self.mv_buf = memoryview(self.rx_buf)
        self.timeout = timeout

    def get_endpoint_descriptors(self, ep_addr, str_idx):
        self.ep_in = (ep_addr + 1) | EP_IN_FLAG
        self.ep_out = (ep_addr + 2)
        # one IN / OUT Endpoint
        e_out = endpoint_descriptor(self.ep_out, "bulk", 64, 0)
        e_in = endpoint_descriptor(self.ep_in, "bulk", 64, 0)
        return (e_out + e_in, [], (self.ep_out, self.ep_in))

    def write(self, data):
        super().submit_xfer(self.ep_in, data)

    def _poll_rx_endpoint(self, cb):
        super().submit_xfer(self.ep_out, self.rx_buf, cb)

    def read(self, nbytes=0):
        # XXX PoC.. When returning, it should probably
        # copy it to a ringbuffer instead of leaving it here
        self.rx_nbytes = 0
        self.rx_nbytes_requested = nbytes
        self.total_rx = bytearray()
        self._poll_rx_endpoint(self._cb_rx)
        now = time.time()
        while ((time.time() - now) < self.timeout):
            if self.rx_nbytes >= nbytes:
                break
            time.sleep_ms(10)   # XXX blocking.. could be async'd
        return self.total_rx

    def _cb_rx(self, ep, res, num_bytes):
        self.total_rx.extend(self.mv_buf[:num_bytes])
        self.rx_nbytes += num_bytes
        if self.rx_nbytes < self.rx_nbytes_requested:
            # try to get more from endpoint
            self._poll_rx_endpoint(self._cb_rx)
