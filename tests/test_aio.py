"""The asyncio client against a fake duckwm: the same cases as duckwm_rs's
tests/fake.rs."""

import asyncio
import socket
import struct

import pytest

from duckwm import Closed, KeyCombo, Rejected, aio, cmd, layouts
from duckwm._proto import duckwm_pb2 as pb
from duckwm.value import from_value, to_value

KEY = pb.CallArgs(key=pb.Empty())  # a key press


class Fake:
    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.reader, self.writer = reader, writer

    async def recv(self) -> pb.ClientMessage:
        (n,) = struct.unpack("<I", await self.reader.readexactly(4))
        msg = pb.ClientMessage()
        msg.ParseFromString(await self.reader.readexactly(n))
        return msg

    async def send(self, msg: pb.ServerMessage) -> None:
        body = msg.SerializeToString()
        self.writer.write(struct.pack("<I", len(body)) + body)
        await self.writer.drain()

    async def request(self) -> list[pb.Command]:
        msg = await self.recv()
        assert msg.WhichOneof("kind") == "request", msg
        return list(msg.request.commands)

    async def names(self) -> list[str]:
        return [str(c.WhichOneof("kind")) for c in await self.request()]

    async def ret(self) -> pb.Return:
        msg = await self.recv()
        assert msg.WhichOneof("kind") == "ret", msg
        return msg.ret

    async def respond(self, *results: object) -> None:
        await self.send(
            pb.ServerMessage(
                response=pb.Response(results=[to_value(v) for v in results])
            )
        )

    async def reject(self, why: str) -> None:
        await self.send(pb.ServerMessage(response=pb.Response(error=why)))

    async def call(self, callback: int, args: pb.CallArgs, nested: bool = True) -> None:
        call = pb.Call(callback=callback, args=args, nested=nested)
        await self.send(pb.ServerMessage(call=call))


async def pair() -> tuple[aio.Client, Fake]:
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    c = await aio.Client.connect(sock=a)
    return c, Fake(*await asyncio.open_unix_connection(sock=b))


def run(test):
    """Each test as a coroutine, with a timeout so a deadlock fails instead of hanging."""

    def wrapper():
        asyncio.run(asyncio.wait_for(test(), 5))

    wrapper.__name__ = test.__name__
    return wrapper


@run
async def test_a_request_and_its_results():
    c, duck = await pair()
    zoom = asyncio.create_task(c.get_zoom())
    assert await duck.names() == ["get_zoom"]
    await duck.respond(0.5)
    assert await zoom == 0.5


@run
async def test_a_rejected_batch():
    c, duck = await pair()
    focus = asyncio.create_task(c.focus(99))
    await duck.request()
    await duck.reject("no such node")
    with pytest.raises(Rejected, match="no such node"):
        await focus


@run
async def test_typed_keybinds():
    c, duck = await pair()
    binds = asyncio.create_task(c.get_keybinds())
    await duck.request()
    await duck.respond([{"mods": 65, "key": "q"}, {"mods": 64, "key": "Return"}])
    assert [str(k) for k in await binds] == ["super+shift+q", "super+Return"]
    assert (await binds)[0] == KeyCombo(65, "q")


@run
async def test_a_callback_with_a_nested_request():
    """A key press: duckwm calls the bind, the bind asks something inside the
    transaction, and a request from elsewhere waits until the call returns."""
    c, duck = await pair()
    presses = []

    async def on_key():
        presses.append(await c.get_focused())  # the same client: scoped by the task
        return cmd.kill_client()

    bound = asyncio.create_task(c.bind("super+u", on_key))
    bind = (await duck.request())[0].bind
    assert (bind.mods, bind.key) == (64, "u")
    await duck.respond(None)
    await bound

    await duck.call(bind.callback.id, KEY)
    assert await duck.names() == ["get_focused"]
    outside = asyncio.create_task(c.get_zoom())
    await asyncio.sleep(0.05)  # every chance to jump the queue
    await duck.respond(7)
    ret = await duck.ret()
    assert ret.err == ""
    assert ret.commands[0].WhichOneof("kind") == "kill_client"
    assert presses == [7]

    # Only now does the outside request go.
    assert await duck.names() == ["get_zoom"]
    await duck.respond(1.0)
    assert await outside == 1.0


@run
async def test_a_failing_callback_reports_back():
    c, duck = await pair()

    async def fails():
        raise ValueError("nope")

    cb = c.callback(fails)
    await duck.call(cb.id, KEY)
    assert (await duck.ret()).err == "ValueError: nope"
    await duck.call(999, KEY)
    assert (await duck.ret()).err == "no such callback"


@run
async def test_the_arranger_handshake():
    c, duck = await pair()
    setting = asyncio.create_task(c.run(cmd.set_arranger(layouts.tile)))
    sa = (await duck.request())[0].set_arranger
    assert sa.name == "tile"

    # duckwm calls the factory while set_arranger is still in flight.
    await duck.call(sa.factory.id, KEY)
    arranger = (await duck.ret()).callback
    await duck.respond(None)
    await setting

    snap = pb.Snapshot(
        event=pb.ARRANGER_EVENT_MAP,
        id=2,
        prev_id=1,
        work_area=pb.Rect(x=0, y=0, width=1000, height=500),
        windows=[pb.WindowInfo(id=1), pb.WindowInfo(id=2)],
    )
    await duck.call(arranger, pb.CallArgs(arranger=snap))
    ret = await duck.ret()
    assert from_value(ret.state) == [1, 2]
    assert ret.commands[0].focus.id == 2
    split = next(
        x.split
        for x in ret.layout.nodes[0].constraints
        if x.WhichOneof("kind") == "split"
    )
    assert list(split.ratios) == [0.55, 0.45]


@run
async def test_hanging_up_fails_whats_pending():
    c, duck = await pair()
    pending = asyncio.create_task(c.get_zoom())
    await duck.request()
    duck.writer.close()
    with pytest.raises(Closed):
        await pending
    await c.closed()  # returns rather than hanging
    with pytest.raises(Closed):
        await c.get_zoom()


@run
async def test_a_call_crossing_a_request():
    """A key press crossing a request on the wire: duckwm runs the request
    inside the call and answers it first. The callback's own request must wait
    for that answer, or the two responses would go to the wrong requests."""
    c, duck = await pair()

    async def on_key():
        assert await c.get_focused() == 7

    cb = c.callback(on_key)
    zoom = asyncio.create_task(c.get_zoom())
    assert await duck.names() == ["get_zoom"]
    await duck.call(cb.id, KEY, nested=False)  # already on its way
    await asyncio.sleep(0.05)  # every chance to jump the queue
    await duck.respond(0.5)
    assert await duck.names() == ["get_focused"]
    await duck.respond(7)
    assert (await duck.ret()).err == ""
    assert await zoom == 0.5


@run
async def test_within_adds_up_and_puts_the_scope_back():
    c, duck = await pair()

    async def config():
        async with c.within("web"), c.within("chat"):
            await c.get_zoom()

    task = asyncio.create_task(config())
    scopes = []
    for answer in [None, None, "web", None, 1.0, None, None]:
        cmd_ = (await duck.request())[0]
        if cmd_.WhichOneof("kind") == "set_scope":
            scopes.append(
                cmd_.set_scope.path if cmd_.set_scope.HasField("path") else None
            )
        await duck.respond(answer)
    await task
    assert scopes == ["web", "web/chat", "web", None]
