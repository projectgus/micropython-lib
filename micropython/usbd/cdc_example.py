from usbd import device, cdc

ud = device.get_usbdevice()
cdc.setup_CDC_device()
ctrl_cdc = cdc.CDCControlInterface('')
data_cdc = cdc.CDCDataInterface('')
ud.add_interface(ctrl_cdc)
ud.add_interface(data_cdc)
ud.reenumerate()

# sending something over CDC
data_cdc.write(b'Hello World')
# receiving something..
print(data_cdc.read(10))
