"""The asyncio client: an async key binding, tile everywhere, and a task of
its own running alongside, across duckwm restarts."""

import asyncio

from duckwm import aio, cmd, layouts


async def setup(c: aio.Client) -> None:
    print(c.platform, "startup:", c.startup)
    for k in await c.get_keybinds():
        print(" ", k)

    async def on_super_u():
        focused = await c.get_focused()  # runs inside the key press
        print("killing", focused)
        return cmd.kill_client()

    await c.bind("super+u", on_super_u)
    await c.default_arranger(layouts.tile)

    async def clock():  # requests from here wait their turn behind key presses
        while True:
            print("workspace", await c.get_current_workspace_position())
            await asyncio.sleep(5)

    task = asyncio.create_task(clock())
    await c.closed()
    task.cancel()


asyncio.run(aio.forever(setup))
