"""Turning untrusted bytes into a plain Python object -- and nothing else.

Owner from wave 2 on: portability-dev (ORCHESTRIERUNG.md section 5).

This module is the only place in the plugin that reads a file somebody else
wrote. It has exactly one job: hand back a ``dict`` of JSON primitives, or
refuse. It knows nothing about reports, fields or the registry, and it must
stay that way -- the security argument for the whole import path is that the
parsing step cannot be talked into doing anything.

What it defends against
-----------------------

============================  ==================================================
Attack                        Defence
============================  ==================================================
huge upload                   :data:`MAX_PAYLOAD_BYTES`, checked on the raw
                              bytes before decoding
deeply nested JSON            :func:`_max_depth` scans the *text* and refuses
                              before ``json.loads`` recurses -- a RecursionError
                              inside the C scanner is not something to rely on
"JSON bomb" (many nodes)      :data:`MAX_NODES`, counted iteratively
oversized strings             :data:`MAX_STRING_CHARS`
decimal explosion             :data:`MAX_NUMBER_DIGITS` on the literal, plus a
                              finiteness check -- ``1e999`` parses to ``inf``,
                              which then travels silently through comparisons
``NaN``/``Infinity``          ``parse_constant`` refuses them; they are a
                              JavaScript extension, not JSON, and they break
                              every downstream comparison
duplicate members             ``object_pairs_hook`` refuses them: two
                              ``"columns"`` members mean the validator and a
                              human reader disagree about the document
executable content            there is none. ``json.loads`` builds dicts, lists,
                              strings, numbers, booleans and ``None`` -- no
                              ``pickle``, no ``yaml``, no ``eval``, no
                              ``__reduce__``, no object hooks that could
                              instantiate a class
============================  ==================================================

The last row is the one that matters most and is asserted by a test that greps
this package for the names of the dangerous deserialisers.
"""

from typing import Any, Dict, List, Sequence, Tuple

import json

from pretix_custom_reports.portability.errors import (
    REASON_DUPLICATE_KEY,
    REASON_EMPTY,
    REASON_NOT_JSON,
    REASON_NOT_UTF8,
    REASON_NUMBER_TOO_LONG,
    REASON_STRING_TOO_LONG,
    REASON_TOO_DEEP,
    REASON_TOO_LARGE,
    REASON_TOO_MANY_NODES,
    PayloadRejected,
)

__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_NUMBER_DIGITS",
    "MAX_PAYLOAD_BYTES",
    "MAX_STRING_CHARS",
    "load_json_object",
]

#: Hard size limit for an uploaded or pasted document, in bytes. A report with
#: the maximum 200 columns, 100 filter conditions and 8 sort stages serialises
#: to roughly 40 KB, so this is two orders of magnitude of head room and still
#: small enough that the depth scan below is instant.
MAX_PAYLOAD_BYTES = 512 * 1024

#: Maximum nesting of arrays and objects. Our deepest legitimate document is the
#: export envelope around a definition with a filter group, a sub-group, a
#: condition and a list value: nine levels.
MAX_DEPTH = 20

#: Maximum number of JSON values (containers, strings, numbers, booleans, nulls)
#: in the whole document. ``MAX_COLUMNS`` (200) columns with a format object
#: each stay below 3.000.
MAX_NODES = 20_000

#: Maximum length of a single JSON string. The structural validator caps the
#: strings it knows about (labels, filter values); this one also covers the
#: free-form ``meta`` object of the envelope.
MAX_STRING_CHARS = 10_000

#: Maximum number of characters in a number literal. Python parses arbitrarily
#: long integers, and CPython's own ``int``/``str`` conversion limit turns that
#: into a confusing ``ValueError`` rather than a clean refusal.
MAX_NUMBER_DIGITS = 30


def _reject(reason: str, message: str) -> "PayloadRejected":
    return PayloadRejected(reason, message)


def _max_depth(text: str) -> int:
    """Deepest nesting of ``[``/``{`` in *text*, ignoring brackets in strings.

    A linear scan over the raw text, deliberately done *before* parsing:
    ``json.loads`` recurses per level, and while CPython raises
    ``RecursionError`` eventually, relying on that means relying on the C stack
    of whichever interpreter runs this in production.
    """
    depth = 0
    deepest = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > deepest:
                deepest = depth
        elif char in "]}":
            depth -= 1
    return deepest


def _parse_int(literal: str) -> int:
    if len(literal.lstrip("-")) > MAX_NUMBER_DIGITS:
        raise _reject(
            REASON_NUMBER_TOO_LONG,
            "A number in this file has more than " f"{MAX_NUMBER_DIGITS} digits.",
        )
    return int(literal)


def _parse_float(literal: str) -> float:
    if len(literal) > MAX_NUMBER_DIGITS + 10:
        raise _reject(
            REASON_NUMBER_TOO_LONG,
            "A number in this file is longer than this importer accepts.",
        )
    value = float(literal)
    # ``1e999`` parses without an error and becomes ``inf``, which compares
    # happily against anything and would end up in a stored definition.
    if value != value or value in (float("inf"), float("-inf")):
        raise _reject(REASON_NOT_JSON, "A number in this file is not a finite value.")
    return value


def _parse_constant(name: str) -> Any:
    raise _reject(
        REASON_NOT_JSON,
        f"'{name}' is a JavaScript value, not JSON, and is not accepted here.",
    )


def _object_pairs(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _reject(
                REASON_DUPLICATE_KEY,
                f"The member '{key}' appears twice in the same object. "
                "Which one applies would depend on the parser, so this file is "
                "refused.",
            )
        out[key] = value
    return out


def _walk(root: Any) -> None:
    """Count nodes and check string lengths without recursing."""
    stack: List[Any] = [root]
    nodes = 0
    while stack:
        node = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise _reject(
                REASON_TOO_MANY_NODES,
                f"This file contains more than {MAX_NODES} values.",
            )
        if isinstance(node, str):
            if len(node) > MAX_STRING_CHARS:
                raise _reject(
                    REASON_STRING_TOO_LONG,
                    "A text in this file is longer than "
                    f"{MAX_STRING_CHARS} characters.",
                )
        elif isinstance(node, dict):
            for key, value in node.items():
                stack.append(key)
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def load_json_object(raw: Any) -> Dict[str, Any]:
    """Parse *raw* (``bytes`` or ``str``) into a JSON object.

    :returns: a ``dict`` containing only JSON primitives.
    :raises PayloadRejected: for every refusal, with a stable ``reason`` code.

    The return type is deliberately narrow: a report file is always an object.
    A top-level array or string is refused here rather than confusing the
    structural validator later.
    """
    if raw is None:
        raise _reject(REASON_EMPTY, "There is nothing to import.")

    if isinstance(raw, (bytes, bytearray, memoryview)):
        data = bytes(raw)
        if len(data) > MAX_PAYLOAD_BYTES:
            raise _reject(
                REASON_TOO_LARGE,
                f"The file is larger than {MAX_PAYLOAD_BYTES // 1024} KiB.",
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _reject(
                REASON_NOT_UTF8,
                "The file is not UTF-8 encoded text. Report files are JSON.",
            )
    elif isinstance(raw, str):
        text = raw
        if len(text.encode("utf-8", errors="ignore")) > MAX_PAYLOAD_BYTES:
            raise _reject(
                REASON_TOO_LARGE,
                f"The text is larger than {MAX_PAYLOAD_BYTES // 1024} KiB.",
            )
    else:
        raise _reject(
            REASON_NOT_JSON,
            "Expected the contents of a file or a block of text.",
        )

    # A byte-order mark is what a Windows editor leaves behind; it is not an
    # attack and refusing it would only produce support tickets.
    text = text.lstrip("﻿").strip()
    if not text:
        raise _reject(REASON_EMPTY, "There is nothing to import.")

    depth = _max_depth(text)
    if depth > MAX_DEPTH:
        raise _reject(
            REASON_TOO_DEEP,
            f"This file nests more than {MAX_DEPTH} levels deep.",
        )

    try:
        parsed = json.loads(
            text,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_parse_constant,
            object_pairs_hook=_object_pairs,
        )
    except PayloadRejected:
        raise
    except ValueError as e:
        raise _reject(REASON_NOT_JSON, f"This is not valid JSON: {e}")
    except RecursionError:  # pragma: no cover - _max_depth catches this first
        raise _reject(REASON_TOO_DEEP, "This file nests too deeply.")

    if not isinstance(parsed, dict):
        raise _reject(
            REASON_NOT_JSON,
            "A report file must be a JSON object, not a " f"{type(parsed).__name__}.",
        )

    _walk(parsed)
    return parsed
