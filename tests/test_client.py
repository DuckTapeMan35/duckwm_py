"""The blocking client against the fake duckwm."""

import pytest

from duckwm import Closed, Rejected, cmd
from duckwm._proto import duckwm_pb2 as pb

from .fake import pair


def test_a_request_and_its_results():
    c, duck = pair()
    duck.respond(0.5)
    assert c.get_zoom() == 0.5
    assert duck.request() == ["get_zoom"]


def test_a_rejected_batch():
    c, duck = pair()
    duck.reject("no such node")
    with pytest.raises(Rejected, match="no such node"):
        c.focus(99)


def test_a_callback_with_a_nested_request():
    c, duck = pair()
    seen = []

    def on_key():
        seen.append(c.get_focused())  # inside duckwm's transaction
        return cmd.kill_client()

    duck.respond(None)
    c.bind("super+u", on_key)
    bind = duck.commands()[0].bind
    assert (bind.mods, bind.key) == (64, "u")

    duck.call(bind.callback.id, key=pb.Empty())
    duck.respond(7)
    duck.hang_up()
    with pytest.raises(Closed):
        c.serve()
    assert seen == [7]
    assert duck.request() == ["get_focused"]
    assert duck.ret().commands[0].WhichOneof("kind") == "kill_client"


def test_a_call_crossing_a_request():
    """A key press crossing a request on the wire: duckwm answers the request
    first, so the callback runs after it, and its own request comes second."""
    c, duck = pair()
    seen = []
    cb = c.callback(lambda: seen.append(c.get_focused()))

    duck.call(cb.id, nested=False, key=pb.Empty())  # already on its way
    duck.respond(0.5)  # get_zoom's answer
    duck.respond(7)  # the callback's get_focused
    assert c.get_zoom() == 0.5
    assert seen == [7]
    assert duck.request() == ["get_zoom"]
    assert duck.request() == ["get_focused"]
    assert duck.ret().err == ""


def test_failing_and_unknown_callbacks_still_answer():
    c, duck = pair()

    def fails():
        raise ValueError("nope")

    cb = c.callback(fails)
    duck.call(cb.id, key=pb.Empty())
    duck.call(999, key=pb.Empty())
    duck.hang_up()
    with pytest.raises(Closed):
        c.serve()
    assert duck.ret().err == "ValueError: nope"
    assert duck.ret().err == "no such callback"


def test_rules_and_read_line_get_their_arguments():
    c, duck = pair()
    got = []
    rule = c.callback(lambda event, id: got.append((event, id)))
    line = c.callback(lambda text: got.append(text))
    duck.call(rule.id, rule=pb.CallArgsRule(event="map", id=4))
    duck.call(line.id, line=pb.CallArgsLine(text="hello"))
    duck.hang_up()
    with pytest.raises(Closed):
        c.serve()
    assert got == [("map", 4), "hello"]
