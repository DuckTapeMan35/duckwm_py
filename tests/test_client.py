"""The blocking client against the fake duckwm."""

import pytest

from duckwm import Closed, Rejected, cmd
from duckwm._proto import duckwm_pb2 as pb

from .fake import pair

KEY = pb.CallArgs(key=pb.Empty())  # a key press


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

    duck.call(bind.callback.id, KEY)
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

    duck.call(cb.id, KEY, nested=False)  # already on its way
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
    duck.call(cb.id, KEY)
    duck.call(999, KEY)
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
    duck.call(rule.id, pb.CallArgs(rule=pb.CallArgsRule(event="map", id=4)))
    duck.call(line.id, pb.CallArgs(line=pb.CallArgsLine(text="hello")))
    duck.hang_up()
    with pytest.raises(Closed):
        c.serve()
    assert got == [("map", 4), "hello"]


def test_combos():
    from duckwm import KeyCombo

    k = KeyCombo.parse("shift+Super+q")
    assert (k.mods, k.key) == (65, "q")
    assert str(k) == "super+shift+q"
    assert str(KeyCombo.parse("Return")) == "Return"
    with pytest.raises(ValueError):
        KeyCombo.parse("hyper+q")
    with pytest.raises(ValueError):
        KeyCombo.parse("super+")


def test_typed_keybinds_and_workspaces():
    from duckwm import KeyCombo, Workspace

    c, duck = pair()
    duck.respond([{"mods": 65, "key": "q"}, {"mods": 64, "key": "Return"}])
    assert c.get_keybinds() == [KeyCombo(65, "q"), KeyCombo(64, "Return")]
    duck.respond(
        [{"position": 1, "state": "focused"}, {"position": 2, "state": "inactive"}]
    )
    assert c.get_workspace_states() == [
        Workspace(1, "focused"),
        Workspace(2, "inactive"),
    ]


def test_dispatch_answers_whats_waiting_and_returns():
    c, duck = pair()
    presses = []
    cb = c.callback(lambda: presses.append(1))
    c.dispatch()  # nothing waiting: returns at once
    duck.call(cb.id, KEY)
    duck.call(cb.id, KEY)
    c.dispatch()
    assert presses == [1, 1]
    duck.hang_up()
    with pytest.raises(Closed):
        c.dispatch()


def test_within_adds_up_and_puts_the_scope_back():
    c, duck = pair()
    # get_scope, set_scope, the inner get_scope and set_scope, get_zoom, then both put back.
    for r in [None, None, "web", None, 1.0, None, None]:
        duck.respond(r)
    with c.within("web"), c.within("chat"):
        c.get_zoom()
    sent = [duck.commands()[0] for _ in range(7)]
    scopes = [
        (s.set_scope.path if s.set_scope.HasField("path") else None)
        for s in sent
        if s.WhichOneof("kind") == "set_scope"
    ]
    assert scopes == ["web", "web/chat", "web", None]


def test_within_puts_the_scope_back_after_an_error():
    c, duck = pair()
    for r in ["DP-1", None, None]:
        duck.respond(r)
    with pytest.raises(RuntimeError), c.within("web"):
        raise RuntimeError("boom")
    assert duck.request() == ["get_scope"]
    assert duck.commands()[0].set_scope.path == "DP-1/web"
    assert duck.commands()[0].set_scope.path == "DP-1"


def test_workspaces_have_their_ids():
    from duckwm import Workspace

    c, duck = pair()
    duck.respond([{"position": 1, "state": "focused", "id": 12}])
    assert c.get_workspace_states() == [Workspace(1, "focused", 12)]
