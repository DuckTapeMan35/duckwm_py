"""duckwm's Value <-> plain Python: None, bool, int, float, str, list, dict.

Several return values (Value.multi) come back as a tuple.
"""

from typing import Any

from ._proto import duckwm_pb2 as pb


def to_value(x: Any) -> pb.Value:
    v = pb.Value()
    match x:
        case None:
            v.nil.SetInParent()
        case bool():  # before int: bool is an int
            v.boolean = x
        case int():
            v.integer = x
        case float():
            v.number = x
        case str():
            v.str = x
        case dict():
            v.map.SetInParent()
            for k, item in x.items():
                v.map.fields.add(key=str(k), value=to_value(item))
        case list() | tuple():
            v.list.SetInParent()
            v.list.items.extend(to_value(i) for i in x)
        case _:
            raise TypeError(f"can't send a {type(x).__name__} to duckwm")
    return v


def from_value(v: pb.Value) -> Any:
    match v.WhichOneof("kind"):
        case None | "nil":
            return None
        case "boolean":
            return v.boolean
        case "integer":
            return v.integer
        case "number":
            return v.number
        case "str":
            return v.str
        case "list":
            return [from_value(i) for i in v.list.items]
        case "multi":
            return tuple(from_value(i) for i in v.multi.items)
        case "map":
            return {f.key: from_value(f.value) for f in v.map.fields}
        case other:
            raise ValueError(f"unknown Value kind {other!r}")
