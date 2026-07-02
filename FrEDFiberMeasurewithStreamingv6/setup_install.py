"""Install the Python packages needed by *FrED Fiber Measure with Streaming*.

Run it with the SAME Python you use to run ``fiber_measure.py`` so the packages
land in that interpreter. On this computer that is the Anaconda Python:

    "C:/Users/saish/anaconda3/python.exe" setup_install.py

On any other computer just run it with that machine's Python:

    python setup_install.py

It uses ``sys.executable``, so it always installs into the interpreter that
runs it -- handy when moving between different computers.
"""
import importlib
import subprocess
import sys

# (import_name, pip_name, friendly_name)
# The WiFi link to the Pi uses the standard-library ``socket`` module, so no
# streaming package (such as pyserial) needs to be installed here.
PACKAGES = [
    ("cv2", "opencv-python", "OpenCV"),
    ("numpy", "numpy", "NumPy"),
    ("PIL", "Pillow", "Pillow"),
    ("openpyxl", "openpyxl", "openpyxl (Excel export)"),
]


def is_installed(import_name):
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def main():
    print(f"Using Python: {sys.executable}\n")

    # Make sure pip itself is current.
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

    for import_name, pip_name, friendly in PACKAGES:
        if is_installed(import_name):
            print(f"{friendly} is already installed.")
            continue
        print(f"\nInstalling {friendly}...")
        result = subprocess.run([sys.executable, "-m", "pip", "install", pip_name])
        if result.returncode != 0:
            print(f"\nERROR: Failed to install {friendly}.")
            sys.exit(result.returncode)

    # tkinter ships with standard CPython / Anaconda but is a separate OS package
    # on some Linux installs -- it cannot be pip-installed, so we only warn.
    if not is_installed("tkinter"):
        print("\nWARNING: tkinter is not available in this Python. It is part of "
              "the standard library on Windows/Anaconda; on Linux install it via "
              "your package manager, e.g. 'sudo apt install python3-tk'.")

    print("\nAll packages are checked and installed if necessary.")


if __name__ == "__main__":
    main()
