"""Smoke test: one batch over the socket, results printed."""

from google.protobuf import json_format

from duckwm._proto import duckwm_pb2 as pb
from duckwm._wire import Connection
from duckwm.value import from_value

# The same JSON shape as the Nix commands file.
batch = {"commands": [{"get_focused": {}}, {"get_current_workspace": {}}]}

with Connection() as c:
    c.send(pb.ClientMessage(request=json_format.ParseDict(batch, pb.Request())))
    reply = c.recv()
    print(reply)
    if reply.WhichOneof("kind") == "response":
        print([from_value(v) for v in reply.response.results])
