# MicroPython USB utility functions
# MIT license; Copyright (c) 2023 Angus Gratton
#
# Some constants and stateless utility functions for working with USB descriptors and requests.
from micropython import const
import ustruct

# Shared constants
#
# It's a tough decision of when to make a constant "shared" like this. "Private" constants have no resource use, but these will take up flash space for the name. Suggest deciding on basis of:
#
# - Is this constant used in a lot of places, including potentially by users
#   of this package?
#
# Otherwise, it's not the greatest sin to be copy-pasting "private" constants
# in a couple of places. I guess. :/

EP_IN_FLAG = const(1 << 7)

# Control transfer stages
STAGE_IDLE = const(0)
STAGE_SETUP = const(1)
STAGE_DATA = const(2)
STAGE_ACK = const(3)

# Request types
REQ_TYPE_STANDARD = const(0x0)
REQ_TYPE_CLASS = const(0x1)
REQ_TYPE_VENDOR = const(0x2)
REQ_TYPE_RESERVED = const(0x3)

# TinyUSB xfer_result_t enum
RESULT_SUCCESS = const(0)
RESULT_FAILED = const(1)
RESULT_STALLED = const(2)
RESULT_TIMEOUT = const(3)
RESULT_INVALID = const(4)


# Non-shared constants, used in this function only
_STD_DESC_ENDPOINT_LEN = const(7)
_STD_DESC_ENDPOINT_TYPE = const(0x5)


def endpoint_descriptor(bEndpointAddress, bmAttributes, wMaxPacketSize, bInterval=1):
    # Utility function to generate a standard Endpoint descriptor bytes object, with
    # the properties specified in the parameter list.
    #
    # See USB 2.0 specification section 9.6.6 Endpoint p269
    #
    # As well as a numeric value, bmAttributes can be a string value to represent
    # common endpoint types: "control", "bulk", "interrupt".
    bmAttributes = {"control": 0, "bulk": 2, "interrupt": 3}.get(bmAttributes, bmAttributes)
    return ustruct.pack(
        "<BBBBHB",
        _STD_DESC_ENDPOINT_LEN,
        _STD_DESC_ENDPOINT_TYPE,
        bEndpointAddress,
        bmAttributes,
        wMaxPacketSize,
        bInterval,
    )


def split_bmRequestType(bmRequestType):
    # Utility function to split control transfer field bmRequestType into a tuple of 3 fields:
    #
    # Recipient
    # Type
    # Data transfer direction
    #
    # See USB 2.0 specification section 9.3 USB Device Requests and 9.3.1 bmRequestType, p248.
    return (
        bmRequestType & 0x1F,
        (bmRequestType >> 5) & 0x03,
        (bmRequestType >> 7) & 0x01,
    )
