"""Decode completion evidence without treating configuration as serving identity."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from math import ceil, isfinite
import re
from typing import Final, Literal, TypeAlias, assert_never

from model_config import JsonObject, JsonValue

Status: TypeAlias = Literal["reachable", "unverified", "unreachable", "substituted", "malformed", "not-installed", "timeout"]
PING: Final = "Reply with exactly one word: pong"


class Harness(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    HERMES = "hermes"
    OPENCODE = "opencode"


@dataclass(frozen=True, slots=True)
class Wire:
    harness: Harness
    provider: str
    model_id: str


@dataclass(frozen=True, slots=True)
class Outcome:
    wire: Wire
    status: Status
    reason_code: str
    observed_models: tuple[str, ...] = ()
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    session: JsonValue
    models: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProtocolError(ValueError):
    reason_code: str = "malformed"

    def __str__(self) -> str:
        return self.reason_code


def mapping(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ProtocolError()
    return value


def text(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ProtocolError()
    return value


def integer(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProtocolError()
    return value


def _pairs(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError()
        result[key] = value
    return result


def _finite(number: str) -> float:
    value = float(number)
    if not isfinite(value):
        raise ProtocolError()
    return value


def command(wire: Wire, timeout: float) -> list[str]:
    match wire.harness:
        case Harness.CLAUDE:
            return ["claude", "-p", PING, "--model", wire.model_id, "--output-format", "json", "--tools", "", "--max-turns", "1"]
        case Harness.CODEX:
            return ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-m", wire.model_id, PING]
        case Harness.HERMES:
            return ["hermes", "chat", "-q", PING, "--oneshot", "--format", "stream-json", "--provider", wire.provider,
                    "-m", wire.model_id, "--max-turns", "1", "--run-budget", str(ceil(timeout)), "--source", "tool"]
        case Harness.OPENCODE:
            return ["opencode", "run", "--format", "json", "-m", f"{wire.provider}/{wire.model_id}", PING]
        case unreachable:
            assert_never(unreachable)


def _session(harness: Harness, value: JsonValue) -> str | None:
    pattern = {Harness.CLAUDE: r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
               Harness.CODEX: r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
               Harness.HERMES: r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", Harness.OPENCODE: r"ses_[A-Za-z0-9]{1,128}"}
    return value if isinstance(value, str) and re.fullmatch(pattern[harness], value) else None


def _claude(record: JsonObject) -> Reply:
    if record["type"] != "result" or type(record.get("is_error")) is not bool:
        raise ProtocolError()
    if record["is_error"] or text(record.get("subtype")) != "success" or record.get("errors"):
        raise ProtocolError("terminal_error")
    models = tuple(model for model, usage in mapping(record.get("modelUsage", {})).items()
                   if integer(mapping(usage).get("outputTokens")) > 0)
    return Reply(text(record.get("result")), record.get("session_id"), models)


def _codex(events: list[JsonObject]) -> Reply:
    if any(event["type"] == "turn.failed" for event in events) or events[-1]["type"] == "error":
        raise ProtocolError("terminal_error")
    if events[0]["type"] != "thread.started":
        raise ProtocolError()
    session = text(events[0].get("thread_id"))
    active, completed, answer = False, False, ""
    for event in events[1:]:
        match event["type"]:
            case "turn.started":
                if active or completed:
                    raise ProtocolError()
                active = True
            case "turn.completed":
                if not active or completed:
                    raise ProtocolError()
                mapping(event.get("usage"))
                active, completed = False, True
            case "error":
                text(event.get("message"))
                if not active:
                    raise ProtocolError("terminal_error")
            case "item.started" | "item.updated" | "item.completed":
                if not active:
                    raise ProtocolError()
                item = mapping(event.get("item"))
                match text(item.get("type")):
                    case "agent_message":
                        content = text(item.get("text"))
                        if event["type"] == "item.completed":
                            answer = content
                    case "reasoning":
                        text(item.get("text"))
                    case "error":
                        if text(item.get("message")).casefold().startswith("model rerouted:"):
                            raise ProtocolError("model_mismatch")
                    case "command_execution" | "mcp_tool_call" | "web_search" | "todo_list":
                        raise ProtocolError("tool_activity")
                    case _:
                        raise ProtocolError()
            case _:
                raise ProtocolError()
    if not completed or not answer:
        raise ProtocolError("missing_completion")
    return Reply(answer, session)


def _hermes(events: list[JsonObject]) -> Reply:
    terminal = events[-1]
    if any(event["type"] in ("tool_use", "tool_result") for event in events):
        raise ProtocolError("tool_activity")
    if any(event["type"] == "result" and (integer(event.get("exit_code")) != 0 or event.get("error")) for event in events):
        raise ProtocolError("terminal_error")
    if events[0]["type"] != "system" or events[0].get("subtype") != "init":
        raise ProtocolError()
    text(events[0].get("model"))
    if terminal["type"] != "result":
        raise ProtocolError("missing_completion")
    for event in events[1:-1]:
        if event["type"] != "text":
            raise ProtocolError()
        text(event.get("text"))
    if terminal.get("session_id") != events[0].get("session_id"):
        raise ProtocolError()
    return Reply(text(terminal.get("text")), terminal.get("session_id"))


def _opencode(events: list[JsonObject]) -> Reply:
    if any(event["type"] == "error" for event in events):
        raise ProtocolError("terminal_error")
    if any(event["type"] == "tool_use" for event in events):
        raise ProtocolError("tool_activity")
    session = text(events[0].get("sessionID"))
    message, answer, completed = "", "", False
    for event in events:
        part = mapping(event.get("part"))
        if event.get("sessionID") != session or part.get("sessionID") != session:
            raise ProtocolError()
        if event["type"] == "step_start":
            message = text(part.get("messageID"))
            answer, completed = "", False
        if not message or part.get("messageID") != message:
            raise ProtocolError()
        match event["type"]:
            case "step_start":
                if part.get("type") != "step-start":
                    raise ProtocolError()
            case "text":
                if completed or part.get("type") != "text":
                    raise ProtocolError()
                integer(mapping(part.get("time")).get("end"))
                answer = text(part.get("text"))
            case "step_finish":
                if completed or part.get("type") != "step-finish":
                    raise ProtocolError()
                completed = text(part.get("reason")) == "stop"
            case "reasoning":
                text(part.get("text"))
            case _:
                raise ProtocolError()
    if not completed or not answer:
        raise ProtocolError("missing_completion")
    return Reply(answer, session)


def decode(wire: Wire, output: str) -> Outcome:
    try:
        chunks = [output] if wire.harness == Harness.CLAUDE else output.splitlines()
        events = [mapping(json.loads(chunk, object_pairs_hook=_pairs, parse_constant=_finite, parse_float=_finite))
                  for chunk in chunks]
        if not events or any(not text(event.get("type")) for event in events):
            raise ProtocolError()
        match wire.harness:
            case Harness.CLAUDE:
                reply = _claude(events[0])
            case Harness.CODEX:
                reply = _codex(events)
            case Harness.HERMES:
                reply = _hermes(events)
            case Harness.OPENCODE:
                reply = _opencode(events)
            case unreachable:
                assert_never(unreachable)
        session = _session(wire.harness, reply.session)
        if reply.models and reply.models != (wire.model_id,):
            return Outcome(wire, "substituted", "model_mismatch", reply.models, session)
        if reply.text.strip().casefold() != "pong":
            return Outcome(wire, "unreachable", "unexpected_response", reply.models, session)
        if not reply.models:
            return Outcome(wire, "unverified", "identity_unavailable", session_id=session)
        return Outcome(wire, "reachable", "verified", reply.models, session)
    except ProtocolError as exc:
        statuses: dict[str, Status] = {"malformed": "malformed", "model_mismatch": "substituted"}
        return Outcome(wire, statuses.get(exc.reason_code, "unreachable"), exc.reason_code)
    except (ValueError, RecursionError):
        return Outcome(wire, "malformed", "malformed")
