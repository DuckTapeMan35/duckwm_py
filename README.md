# duckwm_py

Python client library for [duckwm](https://github.com/DuckTapeMan35/duckwm):
key bindings, rules and arrangers over its socket. Blocking (`duckwm.Client`)
or asyncio (`duckwm.aio.Client`), with the same commands.

```python
from duckwm import Client, cmd, layouts

with Client() as c:
    def on_key():
        print(c.get_focused())  # runs inside the key press
        return cmd.kill_client()

    c.bind("super+u", on_key)               # called here
    c.bind("super+y", cmd.spawn("kitty"))   # run by duckwm itself
    c.default_arranger(layouts.tile)
    c.serve()
```

- **Commands**: every command is a method, run now (`c.focus(3)`), and a
  value in `cmd` (`cmd.focus(3)`) for batches (`c.run(a, b)`, one
  transaction), duckwm-side binds and callback replies. Arguments take plain
  Python: `"left"` for enums, `"#rrggbb"` for colors, functions for callbacks.
- **Callbacks** get their arguments by kind: a key `fn()`, a rule
  `fn(event, id)`, `read_line` `fn(text)`, an arranger `fn(snapshot)`. They
  return nothing, a command, a list of them, or a `Reply`. Their requests run
  inside the transaction that called them.
- **Arrangers**: `layouts.tile` and `layouts.fair`, or your own with
  `@arranger`, built from `row`/`col` trees, `Rect` and `track`.
- **Typed queries**: `get_keybinds()` gives `KeyCombo`s (`str(k)` is
  `"super+shift+q"`), `get_workspace_states()` gives `Workspace`s.
- **`forever(setup)`** runs a config for good: on every connection, setup then
  serve; it waits for duckwm when it's gone and backs off a broken config.
- **Which duckwm**: `c.platform` (`"x11"` or `"river"`), and `c.startup`, true
  for the first client to connect to this duckwm process (shared with
  duckwm_rs and libduckwm), for autostart.

For an event loop of your own, poll `c.fileno()` and call `c.dispatch()`.

## asyncio

```python
import asyncio
from duckwm import aio, cmd, layouts

async def setup(c: aio.Client):
    async def on_key():
        print(await c.get_focused())
        return cmd.kill_client()

    await c.bind("super+u", on_key)
    await c.default_arranger(layouts.tile)
    await c.closed()

asyncio.run(aio.forever(setup))
```

Requests can run concurrently from any number of tasks. duckwm handles one
transaction at a time, so while a callback runs, only its requests go out and
the rest wait for it to return. Which callback a request belongs to follows
the task it's made in, so callbacks use the same client as everything else.
Callbacks can be plain functions or coroutines.

## Developing

```sh
nix develop
python -m pytest            # against a fake duckwm on a socket pair
DUCKWM_SRC=../duckwm scripts/gen   # regenerate from duckwm.proto
```

`src/duckwm/_proto`, `cmd.py`, `_api.py` and `_aio_api.py` are generated;
duckwm's notify workflow regenerates them here when `duckwm.proto` changes.
