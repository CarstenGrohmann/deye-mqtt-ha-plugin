"""Make the deye-inverter-mqtt submodule importable for plugin tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "deye-inverter-mqtt" / "src"))
