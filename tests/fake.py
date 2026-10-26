"""A fake duckwm on the other end of a socket pair, for the tests.

Writes all fit in the socket buffers, so one thread can play both sides: the
fake answers ahead of time, and the client finds the answers waiting.
"""

import socket
import struct

from duckwm import Client
from duckwm._proto import duckwm_pb2 as pb
from duckwm.value import to_value


class Fake:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    # ---- what duckwm sends ----

    def _send(self, msg: pb.ServerMessage) -> None:
        body = msg.SerializeToString()
        self.sock.sendall(struct.pack("<I", len(body)) + body)

    def respond(self, *results: object) -> None:
        r = pb.Response(results=[to_value(v) for v in results])
        self._send(pb.ServerMessage(response=r))

    def reject(self, why: str) -> None:
        self._send(pb.ServerMessage(response=pb.Response(error=why)))

    def call(self, callback: int, nested: bool = True, **args: object) -> None:
        """A call; args is one CallArgs field, e.g. key=pb.Empty()."""
        self._send(
            pb.ServerMessage(
                call=pb.Call(callback=callback, args=pb.CallArgs(**args), nested=nested)
            )
        )

    def hang_up(self) -> None:
        self.sock.shutdown(socket.SHUT_WR)

    # ---- what the client sent ----

    def recv(self) -> pb.ClientMessage:
        (n,) = struct.unpack("<I", self._exact(4))
        msg = pb.ClientMessage()
        msg.ParseFromString(self._exact(n))
        return msg

    def request(self) -> list[str]:
        """The next request's commands, by name."""
        msg = self.recv()
        assert msg.WhichOneof("kind") == "request", msg
        return [c.WhichOneof("kind") for c in msg.request.commands]

    def commands(self) -> list[pb.Command]:
        msg = self.recv()
        assert msg.WhichOneof("kind") == "request", msg
        return list(msg.request.commands)

    def ret(self) -> pb.Return:
        msg = self.recv()
        assert msg.WhichOneof("kind") == "ret", msg
        return msg.ret

    def _exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            assert chunk, "the client hung up"
            buf += chunk
        return buf


def pair() -> tuple[Client, Fake]:
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    return Client(sock=a), Fake(b)
