# MicroPython USB MIDI module
# MIT license; Copyright (c) 2023 Angus Gratton, Paul Hamshere
from micropython import const
import struct

from .device import USBInterface
from .utils import endpoint_descriptor, EP_IN_FLAG

_INTERFACE_CLASS_AUDIO = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_CONTROL = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING = const(0x03)
_PROTOCOL_NONE = const(0x00)

_JACK_TYPE_EMBEDDED = const(0x01)
_JACK_TYPE_EXTERNAL = const(0x02)


class RingBuf:
    def __init__(self, size):
        self.data = bytearray(size)
        self.size = size
        self.index_put = 0
        self.index_get = 0

    def put(self, value):
        next_index = (self.index_put + 1) % self.size
        # check for overflow
        if self.index_get != next_index:
            self.data[self.index_put] = value
            self.index_put = next_index
            return value
        else:
            return None

    def get(self):
        if self.index_get == self.index_put:
            return None  # buffer empty
        else:
            value = self.data[self.index_get]
            self.index_get = (self.index_get + 1) % self.size
            return value

    def is_empty(self):
        return self.index_get == self.index_put


class DummyAudioInterface(USBInterface):
    # An Audio Class interface is mandatory for MIDI Interfaces as well, this
    # class implements the minimum necessary for this.
    def __init__(self):
        super().__init__(_INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL, _PROTOCOL_NONE)

    def get_itf_descriptor(self, num_eps, itf_idx, str_idx):
        # Return the MIDI USB interface descriptors.

        # Get the parent interface class
        desc, strs = super().get_itf_descriptor(num_eps, itf_idx, str_idx)

        # Append the class-specific AudioControl interface descriptor
        desc += struct.pack(
            "<BBBHHBB",
            9,  # bLength
            0x24,  # bDescriptorType CS_INTERFACE
            0x01,  # bDescriptorSubtype MS_HEADER
            0x0100,  # BcdADC
            0x0009,  # wTotalLength
            0x01,  # bInCollection,
            # baInterfaceNr value assumes the next interface will be MIDIInterface
            itf_idx + 1,  # baInterfaceNr
        )

        return (desc, strs)


class MIDIInterface(USBInterface):
    # Base class to implement a USB MIDI device in Python.

    # To be compliant two USB interfaces should be registered in series, first a
    # _DummyAudioInterface() and then this one immediately after.
    def __init__(self, num_rx=1, num_tx=1):
        # Arguments are number of MIDI IN and OUT connections (default 1 each way).

        # 'rx' and 'tx' are from the point of view of this device, i.e. a 'tx'
        # connection is device to host. RX and TX are used here to avoid the
        # even more confusing "MIDI IN" and "MIDI OUT", which varies depending
        # on whether you look from the perspective of the device or the USB
        # interface.
        super().__init__(
            _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING, _PROTOCOL_NONE
        )
        self._num_rx = num_rx
        self._num_tx = num_tx
        self.ep_out = None  # Set during enumeration
        self.ep_in = None
        self._rx_buf = bytearray(64)

    def send_data(self, tx_data):
        """Helper function to send data."""
        self.submit_xfer(self.ep_out, tx_data)

    def midi_received(self):
        return not self.rb.is_empty()

    def get_rb(self):
        return self.rb.get()

    def receive_data_callback(self, ep_addr, result, xferred_bytes):
        for i in range(0, xferred_bytes):
            self.rb.put(self.rx_data[i])
        self.submit_xfer(0x03, self.rx_data, self.receive_data_callback)

    def start_receive_data(self):
        self.submit_xfer(self.ep_in, self.rx_data, self.receive_data_callback)

    def get_itf_descriptor(self, num_eps, itf_idx, str_idx):
        # Return the MIDI USB interface descriptors.

        # Get the parent interface class
        desc, strs = super().get_itf_descriptor(num_eps, itf_idx, str_idx)

        # Append the class-specific interface descriptors

        _JACK_IN_DESC_LEN = const(6)
        _JACK_OUT_DESC_LEN = const(9)

        # Midi Streaming interface descriptor
        cs_ms_interface = struct.pack(
            "<BBBHH",
            7,  # bLength
            0x24,  # bDescriptorType CS_INTERFACE
            0x01,  # bDescriptorSubtype MS_HEADER
            0x0100,  # BcdADC
            # wTotalLength: this descriptor, plus length of all Jack descriptors
            (7 + (2 * (_JACK_IN_DESC_LEN + _JACK_OUT_DESC_LEN) * (self._num_rx + self._num_tx))),
        )

        def jack_in_desc(bJackType, bJackID):
            return struct.pack(
                "<BBBBBB",
                _JACK_IN_DESC_LEN,  # bLength
                0x24,  # bDescriptorType CS_INTERFACE
                0x02,  # bDescriptorSubtype MIDI_IN_JACK
                bJackType,
                bJackID,
                0x00,  # iJack, no string descriptor support yet
            )

        def jack_out_desc(bJackType, bJackID, bSourceId, bSourcePin):
            return struct.pack(
                "<BBBBBBBBB",
                _JACK_OUT_DESC_LEN,  # bLength
                0x24,  # bDescriptorType CS_INTERFACE
                0x03,  # bDescriptorSubtype MIDI_OUT_JACK
                bJackType,
                bJackID,
                0x01,  # bNrInputPins
                bSourceId,  # baSourceID(1)
                bSourcePin,  # baSourcePin(1)
                0x00,  # iJack, no string descriptor support yet
            )

        jacks = bytearray()  # TODO: pre-allocate this whole descriptor and pack into it

        # The USB MIDI standard 1.0 allows modelling a baffling range of MIDI
        # devices with different permutations of Jack descriptors, with a lot of
        # scope for indicating internal connections in the device (as
        # "virtualised" by the USB MIDI standard). Much of the options don't
        # really change the USB behaviour but provide metadata to the host.
        #
        # As observed elsewhere online, the standard ends up being pretty
        # complex and unclear in parts, but there is a clear simple example in
        # an Appendix. So nearly everyone implements the device from the
        # Appendix as-is, even when it's not a good fit for their application,
        # and ignores the rest of the standard.
        #
        # We'll try to implement a slightly more flexible subset that's still
        # very simple, without getting caught in the weeds:
        #
        # - For each rx (total _num_rx), we have data flowing from the USB host
        #   to the USB MIDI device:
        #   * Data comes from a MIDI OUT Endpoint (Host->Device)
        #   * Data goes via an Embedded MIDI IN Jack ("into" the USB-MIDI device)
        #   * Data goes out via a virtual External MIDI OUT Jack ("out" of the
        #      USB-MIDI device and into the world). This "out" jack may be
        #      theoretical, and only exists in the USB descriptor.
        #
        # - For each tx (total _num_tx), we have data flowing from the USB MIDI
        #   device to the USB host:
        #   * Data comes in via a virtual External MIDI IN Jack (from the
        #     outside world, theoretically)
        #   * Data goes via an Embedded MIDI OUT Jack ("out" of the USB-MIDI
        #     device).
        #   * Data goes into the host via MIDI IN Endpoint (Device->Host)

        # rx side
        for idx in range(self._num_rx):
            emb_id = self._emb_id(False, idx)
            ext_id = emb_id + 1
            pin = idx + 1
            jacks += jack_in_desc(_JACK_TYPE_EMBEDDED, emb_id)  # bJackID)
            jacks += jack_out_desc(
                _JACK_TYPE_EXTERNAL,
                ext_id,  # bJackID
                emb_id,  # baSourceID(1)
                pin,  # baSourcePin(1)
            )

        # tx side
        for idx in range(self._num_tx):
            emb_id = self._emb_id(True, idx)
            ext_id = emb_id + 1
            pin = idx + 1

            jacks += jack_in_desc(
                _JACK_TYPE_EXTERNAL,
                ext_id,  # bJackID
            )
            jacks += jack_out_desc(
                _JACK_TYPE_EMBEDDED,
                emb_id,
                ext_id,  # baSourceID(1)
                pin,  # baSourcePin(1)
            )

        iface = desc + cs_ms_interface + jacks
        return (iface, strs)

    def _emb_id(self, is_tx, idx):
        # Given a direction (False==rx, True==tx) and a 0-index
        # of the MIDI connection, return the embedded JackID value.
        #
        # Embedded JackIDs take odd numbers 1,3,5,etc with all
        # 'RX' jack numbers first and then all 'TX' jack numbers
        # (see long comment above for explanation of RX, TX in
        # this context.)
        #
        # This is used to keep jack IDs in sync between
        # get_itf_descriptor() and get_endpoint_descriptors()
        return 1 + 2 * (idx + (is_tx * self._num_rx))

    def get_endpoint_descriptors(self, ep_addr, str_idx):
        # One MIDI endpoint in each direction, plus the
        # associated CS descriptors

        # The following implementation is *very* memory inefficient
        # and needs optimising

        self.ep_out = ep_addr + 1
        self.ep_in = ep_addr + 2 | EP_IN_FLAG

        # rx side, USB "in" endpoint and embedded MIDI IN Jacks
        e_out = endpoint_descriptor(self.ep_in, "bulk", 64, 0)
        cs_out = struct.pack(
            "<BBBB" + "B" * self._num_rx,
            4 + self._num_rx,  # bLength
            0x25,  # bDescriptorType CS_ENDPOINT
            0x01,  # bDescriptorSubtype MS_GENERAL
            self._num_rx,  # bNumEmbMIDIJack
            *(self._emb_id(False, idx) for idx in range(self._num_rx))  # baSourcePin(1..._num_rx)
        )

        # tx side, USB "out" endpoint and embedded MIDI OUT jacks
        e_in = endpoint_descriptor(self.ep_out, "bulk", 64, 0)
        cs_in = struct.pack(
            "<BBBB" + "B" * self._num_tx,
            4 + self._num_tx,  # bLength
            0x25,  # bDescriptorType CS_ENDPOINT
            0x01,  # bDescriptorSubtype MS_GENERAL
            self._num_tx,  # bNumEmbMIDIJack
            *(self._emb_id(True, idx) for idx in range(self._num_tx))  # baSourcePin(1..._num_rx)
        )

        desc = e_out + cs_out + e_in + cs_in

        return (desc, [], (self.ep_out, self.ep_in))


class MidiUSB(MIDIInterface):
    # Very basic synchronous USB MIDI interface

    def __init__(self):
        super().__init__()

    def note_on(self, channel, pitch, vel):
        obuf = struct.pack("<BBBB", 0x09, 0x90 | channel, pitch, vel)
        super().send_data(obuf)

    def note_off(self, channel, pitch, vel):
        obuf = struct.pack("<BBBB", 0x08, 0x80 | channel, pitch, vel)
        super().send_data(obuf)

    def start(self):
        super().start_receive_data()

    def midi_received(self):
        return super().midi_received()

    def get_midi(self):
        if super().midi_received():
            cin = super().get_rb()
            cmd = super().get_rb()
            val1 = super().get_rb()
            val2 = super().get_rb()
            return (cin, cmd, val1, val2)
        else:
            return (None, None, None, None)
