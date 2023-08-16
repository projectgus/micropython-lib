# MicroPython USB device module
# MIT license; Copyright (c) 2022 Angus Gratton
from micropython import const
import machine
import struct

from .utils import split_bmRequestType, EP_IN_FLAG

# USB descriptor types
_STD_DESC_DEVICE_TYPE = const(0x1)
_STD_DESC_CONFIG_TYPE = const(0x2)
_STD_DESC_STRING_TYPE = const(0x3)
_STD_DESC_INTERFACE_TYPE = const(0x4)
_STD_DESC_INTERFACE_ASSOC = const(0xB)

# Standard USB descriptor lengths
_STD_DESC_CONFIG_LEN = const(9)
_STD_DESC_INTERFACE_LEN = const(9)

# Standard control request bmRequest fields, can extract by calling split_bmRequestType()
_REQ_RECIPIENT_DEVICE = const(0x0)
_REQ_RECIPIENT_INTERFACE = const(0x1)
_REQ_RECIPIENT_ENDPOINT = const(0x2)
_REQ_RECIPIENT_OTHER = const(0x3)

# Offsets into the standard configuration descriptor, to fixup
_OFFS_CONFIG_iConfiguration = const(6)


# Singleton _USBDevice instance
_inst = None


def get_usbdevice():
    # Access the singleton instance of the MicroPython _USBDevice object.
    #
    # TODO: It might be better to factor this as a module-level interface?
    global _inst
    if not _inst:
        _inst = _USBDevice()
    return _inst


class _USBDevice:
    # Class that implements the Python parts of the MicroPython USBDevice.
    #
    # This object represents any interfaces on the USB device that are implemented
    # in Python, and also allows disabling the 'static' USB interfaces that are
    # implemented in Python (if include_static property is set to False).
    #
    # Should be accessed via the singleton getter module function get_usbdevice(),
    # not instantiated directly..
    def __init__(self):
        self._eps = {}  # Mapping from endpoint address to interface object
        self._ep_cbs = {}  # Mapping from endpoint address to Optional[xfer callback]
        self._itfs = []  # List of interfaces
        self.include_static = True  # Include static devices when enumerating?

        # Device properties, set non-NULL to override static values
        self.manufacturer_str = None
        self.product_str = None
        self.serial_str = None
        self.id_vendor = None
        self.id_product = None
        self.device_class = None
        self.device_subclass = None
        self.device_protocol = None
        self.bcd_device = None

        # Configuration properties
        self.config_str = None
        self.max_power_ma = 50

        self._strs = self._get_device_strs()

        usbd = self._usbd = machine.USBD()
        usbd.init(
            descriptor_device_cb=self._descriptor_device_cb,
            descriptor_config_cb=self._descriptor_config_cb,
            descriptor_string_cb=self._descriptor_string_cb,
            open_cb=self._open_cb,
            reset_cb=self._reset_cb,
            control_xfer_cb=self._control_xfer_cb,
            xfer_cb=self._xfer_cb,
        )

    def add_interface(self, itf):
        # Add an instance of USBInterface to the USBDevice.
        #
        # The next time USB is reenumerated (by calling .reenumerate() or
        # otherwise), this interface will appear to the host.
        self._itfs.append(itf)

    def remove_interface(self, itf):
        # Remove an instance of USBInterface from the USBDevice.
        #
        # If the USB device is currently enumerated to a host, and in particular
        # if any endpoint transfers are pending, then this may cause it to
        # misbehave as these transfers are not cancelled.
        self._itfs.remove(itf)

    def reenumerate(self):
        # Disconnect the USB device and then reconnect it, causing the host to
        # reenumerate it.
        #
        # Any open USB interfaces (for example USB-CDC serial connection) will be
        # temporarily terminated.
        #
        # This is the only way to change the composition of an existing USB device
        # from the device side without disconnecting/reconnecting the port.
        self._usbd.reenumerate()

    def _descriptor_device_cb(self):
        # Singleton callback from TinyUSB to read the USB device descriptor.
        #
        # This function will build a new device descriptor based on the 'static'
        # USB device values compiled into MicroPython, but many values can be
        # optionally overriden by setting properties of this object.

        FMT = "<BBHBBBBHHHBBBB"
        # static descriptor fields
        f = struct.unpack(FMT, self._usbd.static.desc_device)

        def maybe_set(value, idx):
            # Override a numeric descriptor value or keep static value f[idx] if 'value' is None
            if value is not None:
                return value
            return f[idx]

        def maybe_set_str(s, idx):
            # Override a string index 's' or keep static value f[idx] if 's' is None
            if s:
                return self._get_device_str_index(s)
            return f[idx]

        # Either copy each descriptor field directly from the static device descriptor, or 'maybe'
        # override if a custom value has been set on this object
        return struct.pack(
            FMT,
            f[0],  # bLength
            f[1],  # bDescriptorType
            f[2],  # bcdUSB
            maybe_set(self.device_class, 3),  # bDeviceClass
            maybe_set(self.device_subclass, 4),  # bDeviceSubClass
            maybe_set(self.device_protocol, 5),  # bDeviceProtocol
            f[6],  # bMaxPacketSize0, TODO: allow overriding this value?
            maybe_set(self.id_vendor, 7),  # idVendor
            maybe_set(self.id_product, 8),  # idProduct
            maybe_set(self.bcd_device, 9),  # bcdDevice
            maybe_set_str(self.manufacturer_str, 10),  # iManufacturer
            maybe_set_str(self.product_str, 11),  # iProduct
            maybe_set_str(self.serial_str, 12),  # iSerialNumber
            f[13],
        )  # bNumConfigurations

    def _get_device_strs(self):
        # Get strings that are defined by the device or its configuration, not
        # any interfaces/endpoints
        #
        # These values is used as the initial value of self._strs, which is built
        # up during the enumeration process.
        result = [
            self.manufacturer_str,
            self.product_str,
            self.serial_str,
            self.config_str,
        ]
        return [r for r in result if r]

    def _get_str_index(self, s):
        # Get the USB descriptor index for a string 's' defined in the strings
        # list, or 0 if 's' is None
        #
        # It's assumed that 's' will be a string that the caller already knows has
        # been added to self._strs list during enumeration.
        if s:
            return self._usbd.static.str_max + self._strs.index(s)
        else:
            return 0

    def _get_interface(self, index):
        # Return a reference to the interface object with the given USB index.
        index -= self._usbd.static.itf_max
        assert index >= 0  # index shouldn't be in the static range
        try:
            return self._itfs[index]
        except IndexError:
            return None  # host has old mappings for interfaces

    def _descriptor_config_cb(self):
        # Singleton callback from TinyUSB to read the configuration descriptor.
        #
        # Each time this function is called (in response to a GET DESCRIPTOR -
        # CONFIGURATION request from the host), it rebuilds the full configuration
        # descriptor and also the list of strings stored in self._strs.
        #
        # This normally only happens during enumeration, but may happen more than
        # once (the host will first ask for a minimum length descriptor, and then
        # use the length field request to request the whole thing).
        static = self._usbd.static

        # Rebuild the _strs list as we build the configuration descriptor
        strs = self._get_device_strs()

        if self.include_static:
            desc = bytearray(static.desc_cfg)
        else:
            desc = bytearray(_STD_DESC_CONFIG_LEN)

        self._eps = {}  # rebuild endpoint mapping as we enumerate each interface
        self._ep_cbs = {}
        itf_idx = static.itf_max
        ep_addr = static.ep_max
        str_idx = static.str_max + len(strs)
        for itf in self._itfs:
            # Get the endpoint descriptors first so we know how many endpoints there are
            ep_desc, ep_strs, ep_addrs = itf.get_endpoint_descriptors(ep_addr, str_idx)
            strs += ep_strs
            str_idx += len(ep_strs)

            # Now go back and get the interface descriptor
            itf_desc, itf_strs = itf.get_itf_descriptor(len(ep_addrs), itf_idx, str_idx)
            desc += itf_desc
            strs += itf_strs
            itf_idx += 1
            str_idx += len(itf_strs)

            desc += ep_desc
            for e in ep_addrs:
                self._eps[e] = itf
                self._ep_cbs[e] = None  # no pending callback
                # TODO: check if always incrementing leaves too many gaps
                ep_addr = max((e & ~EP_IN_FLAG) + 1, ep_addr)

        self._update_configuration_descriptor(desc)

        self._strs = strs
        return desc

    def _update_configuration_descriptor(self, desc):
        # Utility function to update the Standard Configuration Descriptor
        # header supplied in the argument with values based on the current state
        # of the device.
        #
        # See USB 2.0 specification section 9.6.3 p264 for details.
        #
        # Currently only one configuration per device is supported.
        bmAttributes = (
            (1 << 7)  # Reserved
            | (0 if self.max_power_ma else (1 << 6))  # Self-Powered
            # Remote Wakeup not currently supported
        )

        iConfiguration = self._get_str_index(self.config_str)
        if self.include_static and not iConfiguration:
            iConfiguration = desc[_OFFS_CONFIG_iConfiguration]

        bNumInterfaces = self._usbd.static.itf_max if self.include_static else 0
        bNumInterfaces += len(self._itfs)

        struct.pack_into(
            "<BBHBBBBB",
            desc,
            0,
            _STD_DESC_CONFIG_LEN,  # bLength
            _STD_DESC_CONFIG_TYPE,  # bDescriptorType
            len(desc),  # wTotalLength
            bNumInterfaces,
            1,  # bConfigurationValue
            iConfiguration,
            bmAttributes,
            self.max_power_ma,
        )

    def _descriptor_string_cb(self, index):
        # Singleton callback from TinyUSB to get a string descriptor.
        #
        # The self._strs list is built during enumeration (each time
        # _descriptor_config_cb is called), so we just return a value indexed from
        # it.
        index -= self._usbd.static.str_max
        assert (
            index >= 0
        )  # Shouldn't get any calls here where index is less than first dynamic string index
        try:
            return self._strs[index]
        except IndexError:
            return None

    def _open_cb(self, interface_desc_view):
        # Singleton callback from TinyUSB custom class driver, when USB host does
        # Set Configuration. The "runtime class device" accepts all interfaces that
        # it has sent in descriptors, and calls this callback.

        # Walk the view of the "claimed" descriptor data provided in the
        # callback and call handle_open() on each claimed interface
        #
        # ... this may be unnecessary at the moment, as only one configuration is supported so we
        # can probably assume all the interfaces will be included.
        i = 0
        while i < len(interface_desc_view):
            # descriptor length, type, and index (if it's an interface descriptor)
            dl, dt, di = interface_desc_view[i : i + 3]
            if dt == _STD_DESC_INTERFACE_TYPE:
                if di >= self._usbd.static.itf_max:
                    di -= self._usbd.static.itf_max
                self._itfs[di].handle_open()
            i += dl
            assert dl

    def _reset_cb(self):
        # Callback when the USB device is reset by the host

        # Cancel outstanding transfer callbacks
        for k in self._ep_cbs.keys():
            self._ep_cbs[k] = None

        # Allow interfaces to respond to the reset
        for itf in self._itfs:
            itf.handle_reset()

    def _submit_xfer(self, ep_addr, data, done_cb=None):
        # Singleton function to submit a USB transfer (of any type except control).
        #
        # Generally, drivers should call USBInterface.submit_xfer() instead. See
        # that function for documentation about the possible parameter values.
        cb = self._ep_cbs[ep_addr]
        if cb:
            raise RuntimeError(f"Pending xfer on EP {ep_addr}")

        # USBD callback may be called immediately, before Python execution
        # continues
        self._ep_cbs[ep_addr] = done_cb

        if not self._usbd.submit_xfer(ep_addr, data):
            self._ep_cbs[ep_addr] = None
            return False
        return True

    def _xfer_cb(self, ep_addr, result, xferred_bytes):
        # Singleton callback from TinyUSB custom class driver when a transfer completes.
        cb = self._ep_cbs.get(ep_addr, None)
        if cb:
            self._ep_cbs[ep_addr] = None
            cb(ep_addr, result, xferred_bytes)

    def _control_xfer_cb(self, stage, request):
        # Singleton callback from TinyUSB custom class driver when a control
        # transfer is in progress.
        #
        # stage determines appropriate responses (possible values
        # utils.STAGE_SETUP, utils.STAGE_DATA, utils.STAGE_ACK).
        #
        # The TinyUSB class driver framework only calls this function for
        # particular types of control transfer, other standard control transfers
        # are handled by TinyUSB itself.
        bmRequestType, _, _, wIndex, _ = request
        recipient, _, _ = split_bmRequestType(bmRequestType)

        itf = None
        result = None

        if recipient == _REQ_RECIPIENT_DEVICE:
            itf = self._get_interface(wIndex & 0xFFFF)
            if itf:
                result = itf.handle_device_control_xfer(stage, request)
        elif recipient == _REQ_RECIPIENT_INTERFACE:
            itf = self._get_interface(wIndex & 0xFFFF)
            if itf:
                result = itf.handle_interface_control_xfer(stage, request)
        elif recipient == _REQ_RECIPIENT_ENDPOINT:
            ep_num = wIndex & 0xFFFF
            itf = self._eps.get(ep_num, None)
            if itf:
                result = itf.handle_endpoint_control_xfer(stage, request)

        if not itf:
            # At time this code was written, only the control transfers shown
            # above are passed to the class driver callback. See
            # invoke_class_control() in tinyusb usbd.c
            print(f"Unexpected control request type {bmRequestType:#x}")
            return False

        # Accept the following possible replies from handle_NNN_control_xfer():
        #
        # True - Continue transfer, no data
        # False - STALL transfer
        # Object with buffer interface - submit this data for the control transfer
        if type(result) == bool:
            return result

        return self._usbd.control_xfer(request, result)


class USBInterface:
    # Abstract base class to implement a USBInterface (and associated endpoints) in Python

    def __init__(
        self,
        bInterfaceClass=0xFF,
        bInterfaceSubClass=0,
        bInterfaceProtocol=0xFF,
        interface_str=None,
    ):
        # Create a new USBInterface object. Optionally can set bInterfaceClass,
        # bInterfaceSubClass, bInterfaceProtocol values to specify the interface
        # type. Can also optionally set a string descriptor value interface_str to describe this
        # interface.
        #
        # The defaults are to set 'vendor' class and protocol values, the host
        # will not attempt to use any standard class driver to talk to this
        # interface.

        # Defaults set "vendor" class and protocol
        self.bInterfaceClass = bInterfaceClass
        self.bInterfaceSubClass = bInterfaceSubClass
        self.bInterfaceProtocol = bInterfaceProtocol
        self.interface_str = interface_str
        self._open = False

    def get_itf_descriptor(self, num_eps, itf_idx, str_idx):
        # Return the interface descriptor binary data and associated other
        # descriptors for the interface (not including endpoint descriptors), plus
        # associated string descriptor data.
        #
        # For most types of USB interface, this function doesn't need to be
        # overriden. Only override if you need to append interface-specific
        # descriptors before the first endpoint descriptor. To return an Interface
        # Descriptor Association, on the first interface this function should
        # return the IAD descriptor followed by the Interface descriptor.
        #
        # Parameters:
        #
        # - num_eps - number of endpoints in the interface, as returned by
        #   get_endpoint_descriptors() which is actually called before this
        #   function.
        #
        # - itf_idx - Interface index number for this interface.
        #
        # - str_idx - First string index number to assign for any string
        #   descriptor indexes included in the result.
        #
        # Result:
        #
        # Should be a 2-tuple:
        #
        # - Interface descriptor binary data, to return as part of the
        #   configuration descriptor.
        #
        # - List of any strings referenced in the interface descriptor data
        #   (indexes in the descriptor data should start from 'str_idx'.)
        #
        # See USB 2.0 specification section 9.6.5 p267 for standard interface descriptors.
        desc = struct.pack(
            "<" + "B" * _STD_DESC_INTERFACE_LEN,
            _STD_DESC_INTERFACE_LEN,  # bLength
            _STD_DESC_INTERFACE_TYPE,  # bDescriptorType
            itf_idx,  # bInterfaceNumber
            0,  # bAlternateSetting, not currently supported
            num_eps,
            self.bInterfaceClass,
            self.bInterfaceSubClass,
            self.bInterfaceProtocol,
            str_idx if self.interface_str else 0,  # iInterface
        )
        strs = [self.interface_str] if self.interface_str else []

        return (desc, strs)

    def get_endpoint_descriptors(self, ep_addr, str_idx):
        # Similar to get_itf_descriptor, returns descriptors for any endpoints
        # in this interface, plus associated other configuration descriptor data.
        #
        # The base class returns no endpoints, so usually this is overriden in the subclass.
        #
        # This function is called any time the host asks for a configuration
        # descriptor. It is actually called before get_itf_descriptor(), so that
        # the number of endpoints is known.
        #
        # Parameters:
        #
        # - ep_addr - Address for this endpoint, without any utils.EP_IN_FLAG (0x80) bit set.
        # - str_idx - Index to use for the first string descriptor in the result, if any.
        #
        # Result:
        #
        # Should be a 3-tuple:
        #
        # - Endpoint descriptor binary data and associated other descriptors for
        #   the endpoint, to return as part of the configuration descriptor.
        #
        # - List of any strings referenced in the descriptor data (indexes in the
        #   descriptor data should start from 'str_idx'.)
        #
        # - List of endpoint addresses referenced in the descriptor data (should
        #   start from ep_addr, optionally with the utils.EP_IN_FLAG bit set.)
        return (b"", [], [])

    def handle_open(self):
        # Callback called when the USB host accepts the device configuration.
        #
        # Override this function to initiate any operations that the USB interface
        # should do when the USB device is configured to the host.
        self._open = True

    def handle_reset(self):
        # Callback called on every registered interface when the USB device is
        # reset by the host. This can happen when the USB device is unplugged,
        # or if the host triggers a reset for some other reason.
        #
        # Override this function to cancel any pending operations specific to
        # the interface (outstanding USB transfers are already cancelled).
        #
        # At this point, no USB functionality is available - handle_open() will
        # be called later if/when the USB host re-enumerates and configures the
        # interface.
        self._open = False

    def is_open(self):
        # Returns True if the interface is in use
        return self._open

    def handle_device_control_xfer(self, stage, request):
        # Control transfer callback. Override to handle a non-standard device
        # control transfer where bmRequestType Recipient is Device, Type is
        # utils.REQ_TYPE_CLASS, and the lower byte of wIndex indicates this interface.
        #
        # (See USB 2.0 specification 9.4 Standard Device Requests, p250).
        #
        # This particular request type seems pretty uncommon for a device class
        # driver to need to handle, most hosts will not send this so most
        # implementations won't need to override it.
        #
        # Parameters:
        #
        # - stage is one of utils.STAGE_SETUP, utils.STAGE_DATA, utils.STAGE_ACK.
        # - request is a tuple of (bmRequestType, bRequest, wValue, wIndex,
        # - wLength), as per USB 2.0 specification 9.3 USB Device Requests, p250.
        #
        # The function can call split_bmRequestType() to split bmRequestType into
        # (Recipient, Type, Direction).
        #
        # Result, any of:
        #
        # - True to continue the request, False to STALL the endpoint.
        # - Buffer interface object to provide a buffer to the host as part of the
        #   transfer, if possible.
        return False

    def handle_interface_control_xfer(self, stage, request):
        # Control transfer callback. Override to handle a device control
        # transfer where bmRequestType Recipient is Interface, and the lower byte
        # of wIndex indicates this interface.
        #
        # (See USB 2.0 specification 9.4 Standard Device Requests, p250).
        #
        # bmRequestType Type field may have different values. It's not necessary
        # to handle the mandatory Standard requests (bmRequestType Type ==
        # utils.REQ_TYPE_STANDARD), if the driver returns False in these cases then
        # TinyUSB will provide the necessary responses.
        #
        # See handle_device_control_xfer() for a description of the arguments and
        # possible return values.
        return False

    def handle_endpoint_control_xfer(self, stage, request):
        # Control transfer callback. Override to handle a device
        # control transfer where bmRequestType Recipient is Endpoint and
        # the lower byte of wIndex indicates an endpoint address associated
        # with this interface.
        #
        # bmRequestType Type will generally have any value except
        # utils.REQ_TYPE_STANDARD, as Standard endpoint requests are handled by
        # TinyUSB. The exception is the the Standard "Set Feature" request. This
        # is handled by Tiny USB but also passed through to the driver in case it
        # needs to change any internal state, but most drivers can ignore and
        # return False in this case.
        #
        # (See USB 2.0 specification 9.4 Standard Device Requests, p250).
        #
        # See handle_device_control_xfer() for a description of the parameters and
        # possible return values.
        return False

    def submit_xfer(self, ep_addr, data, done_cb=None):
        # Submit a USB transfer (of any type except control)
        #
        # Parameters:
        #
        # - ep_addr. Address of the endpoint to submit the transfer on. Caller is
        #   responsible for ensuring that ep_addr is correct and belongs to this
        #   interface. Only one transfer can be active at a time on each endpoint.
        #
        # - data. Buffer containing data to send, or for data to be read into
        #   (depending on endpoint direction).
        #
        # - done_cb. Optional callback function for when the transfer
        # completes. The callback is called with arguments (ep_addr, result,
        # xferred_bytes) where result is one of xfer_result_t enum (see top of
        # this file), and xferred_bytes is an integer.
        #
        # Note that done_cb may be called immediately, possibly before this
        # function has returned to the caller.
        if not self._open:
            raise RuntimeError
        return get_usbdevice()._submit_xfer(ep_addr, data, done_cb)

    def set_ep_stall(self, ep_addr, stall):
        # Set or clear endpoint STALL state, according to the bool "stall" parameter.
        #
        # Generally endpoint STALL is handled automatically by TinyUSB, but
        # there are some device classes that need to explicitly stall or unstall
        # an endpoint under certain conditions.
        if not self._open or ep_addr not in get_usbdevice()._eps:
            raise RuntimeError
        get_usbdevice()._usbd.set_ep_stall(ep_addr, stall)

    def get_ep_stall(self, ep_addr):
        # Get the current endpoint STALL state.
        #
        # Endpoint can be stalled/unstalled by host, TinyUSB stack, or calls to
        # set_ep_stall().
        if not self._open or ep_addr not in get_usbdevice()._eps:
            raise RuntimeError
        return get_usbdevice()._usbd.get_ep_stall(ep_addr)
