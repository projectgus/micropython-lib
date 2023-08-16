metadata(version="0.1.0")

# TODO: split this up into sub-packages, and some code in example subdirectory
package(
    "usbd",
    files=(
        "__init__.py",
        "device.py",
        "hid.py",
        "hid_keypad.py",
        "midi.py",
        "utils.py",
    ),
    base_path="..",
)
