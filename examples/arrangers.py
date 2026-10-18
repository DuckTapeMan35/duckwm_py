import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # examples/layouts

from duckwm import Client
from duckwm.layouts import fair, tile
from layouts.fibonacci import fibonacci

with Client() as c:
    c.bind("super+a", fibonacci)
    c.bind("super+t", tile)
    c.bind("super+ctrl+f", fair)
    c.default_arranger(fair)
    print("serving; ctrl+c to stop")
    c.serve()
