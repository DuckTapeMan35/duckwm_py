"""Callbacks: a function bind, a duckwm-side bind, a nested request, a rule."""

from duckwm import Client, cmd

with Client() as c:
    c.bind("super+u", lambda: print("super+u pressed"))
    c.bind("super+y", cmd.spawn("kitty"))

    def show_focused() -> None:
        focused = c.get_focused()
        if focused is not None:
            print("focused:", focused, c.get_window_class(focused))

    c.bind("super+i", show_focused)
    c.bind("super+ctrl+h", cmd.resize_focused_edge("left", 20))
    c.add_rule(lambda event, id: print("rule:", event, id))

    print("serving; ctrl+c to stop")
    c.serve()
