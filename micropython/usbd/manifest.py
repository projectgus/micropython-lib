metadata(version="0.1.0")

# TODO: split off parts of this to optional sub-packages, most people won't need
# all interface classes
package(
    "usbd",
    files=("__init__.py", "device.py", "hid.py", "midi.py", "utils.py"),
    base_path="..",
)
