"""The socket: duckwm's path, and length-prefixed protobuf frames."""

import os
import socket
import struct
from typing import Self

from ._proto import duckwm_pb2 as pb

MAX_FRAME = 16 * 1024 * 1024


class Closed(ConnectionError):
    """duckwm closed the connection (it quit, restarted, or dropped us)."""


def socket_path() -> str:
    """$XDG_RUNTIME_DIR/duckwm.sock, or /tmp/duckwm-<uid>.sock, like duckwm."""
    run = os.environ.get("XDG_RUNTIME_DIR")
    return f"{run}/duckwm.sock" if run else f"/tmp/duckwm-{os.getuid()}.sock"


class Connection:
    """One connection to duckwm. Frames are a little-endian u32 length, then the message."""

    def __init__(self, path: str | None = None) -> None:
        self.sock = socket.socket(
            socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC
        )
        try:
            self.sock.connect(path or socket_path())
        except BaseException:
            self.sock.close()
            raise

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def send(self, msg: pb.ClientMessage) -> None:
        body = msg.SerializeToString()
        try:
            self.sock.sendall(struct.pack("<I", len(body)) + body)
        except (BrokenPipeError, ConnectionResetError) as e:
            raise Closed("duckwm closed the connection") from e

    def recv(self) -> pb.ServerMessage:
        (n,) = struct.unpack("<I", self._exact(4))
        if n > MAX_FRAME:
            raise ValueError(f"frame of {n} bytes is larger than {MAX_FRAME}")
        msg = pb.ServerMessage()
        msg.ParseFromString(self._exact(n))
        return msg

    def _exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except ConnectionResetError as e:
                raise Closed("duckwm closed the connection") from e
            if not chunk:
                raise Closed("duckwm closed the connection")
            buf += chunk
        return bytes(buf)
