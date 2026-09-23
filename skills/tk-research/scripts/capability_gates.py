"""Qualify loaded peers against trusted manifests without executing them.

Peers report package/version/source/root and loaded_skills[selector].path/sha256.
Model slots report descriptor, method and members[{catalog_key,provider,model_id}].
Home-dependent methods require identical path/parent_home/dispatcher_home strings
and a local Linux mount proof; other platforms fail closed, not guessed safe.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
import sys
from typing import Final, Literal, TypeAlias

from model_config import ConfigError, JsonObject, JsonValue, load_json

Decision: TypeAlias = Literal["delegate", "owned", "fallback", "blocked"]
Method: TypeAlias = Literal["configured", "delegate_route", "explicit_dispatch"]
Reason: TypeAlias = Literal["compatible", "disabled", "owned_policy", "invalid_config", "peer_missing",
                            "unsupported_host", "version_mismatch", "source_mismatch", "capability_missing",
                            "model_mismatch", "unsafe_runtime_home", "missing_evidence"]
LOCAL_FS: Final = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs"})
MOUNT_LIMIT: Final = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Request:
    host: str
    project_root: Path
    selection: JsonObject
    catalog: JsonObject


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    reason: Reason
    detail: str
    effective: JsonObject
    runtime_home: str | None


@dataclass(frozen=True, slots=True)
class _Denied(Exception):
    reason: Reason
    detail: str

    def __str__(self) -> str:
        return self.detail


def need(condition: bool, reason: Reason, detail: str) -> None:
    if not condition:
        raise _Denied(reason, detail)


def expect_object(value: JsonValue, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a JSON object")
    return value


def expect_text(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a string")
    return value


def expect_list(value: JsonValue, field: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be a list")
    return value


def expect_strings(value: JsonValue, field: str) -> list[str]:
    return [expect_text(item, field) for item in expect_list(value, field)]


def path_text(value: JsonValue, field: str) -> str:
    result = expect_text(value, field)
    if any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ConfigError(f"{field} must not contain control characters")
    return result


def digest(value: JsonValue) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def evidence(document: JsonObject, field: str) -> JsonValue:
    need(field in document, "missing_evidence", f"Missing {field} evidence")
    return document[field]


def _resolved(path: str) -> Path:
    need(Path(path).is_absolute(), "source_mismatch", "Peer path must be absolute")
    try:
        return Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _Denied("source_mismatch", "Peer path cannot be resolved") from None


def _locate(root: Path, relative: str, directory: bool = False) -> Path:
    current = root
    mode = 0
    for part in relative.split("/"):
        current = current / part
        mode = current.lstat().st_mode
        need(not stat.S_ISLNK(mode), "source_mismatch", "Peer path crosses a symlink")
    need(stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode), "source_mismatch", "Wrong peer file type")
    return current


def _provenance(candidate: JsonObject, snapshot: JsonObject) -> None:
    peers = expect_object(snapshot.get("peers", {}), "peers")
    ecosystem = expect_text(candidate.get("ecosystem"), "ecosystem")
    need(ecosystem in peers, "peer_missing", "No peer evidence")
    peer = expect_object(peers[ecosystem], "peer")
    pin = expect_object(candidate.get("pin"), "pin")
    for field in ("package", "version", "source", "source_commit"):
        if field == "source_commit" and (field not in pin or field not in peer):
            continue
        value = expect_text(evidence(peer, field), field)
        need(value == pin.get(field), "version_mismatch" if field == "version" else "source_mismatch", f"Peer {field} differs from pin")
    root = _resolved(path_text(evidence(peer, "root"), "root"))
    need(root.is_dir(), "source_mismatch", "Peer root is not a directory")
    identity = expect_object(pin.get("provenance_root"), "provenance_root")
    provenance = expect_object(candidate.get("provenance"), "provenance")
    files = expect_object(provenance.get("files"), "files")
    entry = expect_text(provenance.get("entrypoint"), "entrypoint")
    identity_path = _locate(root, expect_text(identity.get("identity_file"), "identity_file"))
    try:
        document = load_json(str(identity_path))
        expected = expect_object(identity.get("identity_fields"), "identity_fields")
        need(all(type(document.get(key)) is type(value) and document.get(key) == value for key, value in expected.items()),
             "source_mismatch", "Installed identity differs from pin")
        if provenance.get("root_kind") == "omh":
            records = [{key: expect_text(expect_object(row, "skill record").get(key), key)
                        for key in ("name", "path", "sha256", "source")} for row in expect_list(document.get("skills"), "skills")]
            for record in records:
                need(digest(record.get("sha256")), "source_mismatch", "Malformed installer digest")
            need(all(len({row[key] for row in records}) == len(records) for key in ("name", "path")), "source_mismatch", "Duplicate installer record")
            matched = [row for row in records if row.get("path") == entry.removeprefix("skills/")]
            need(bool(matched), "peer_missing", "Skill is not registered")
            record = matched[0]
            need(record.get("name") == candidate.get("canonical_name") and record.get("source") in expect_strings(identity.get("manifest_source_values"), "sources")
                 and record.get("sha256") == files.get(entry), "source_mismatch", "Installer record contradicts pin")
            if "skills_dir" in document:
                skills_dir = path_text(document["skills_dir"], "skills_dir")
                need(skills_dir == "skills" or _resolved(skills_dir) == root / "skills", "source_mismatch", "Wrong installed skills directory")
    except ConfigError:
        raise _Denied("source_mismatch", "Malformed installed identity") from None
    loaded_skills = expect_object(peer.get("loaded_skills", {}), "loaded_skills")
    selector = expect_text(candidate.get("selector"), "selector")
    need(not (ecosystem == "omh" and loaded_skills.get(selector) is None and candidate.get("skill_name") in loaded_skills), "source_mismatch", "Ambiguous bare selector")
    need(loaded_skills.get(selector) is not None, "peer_missing", "Skill is not loaded")
    loaded = expect_object(loaded_skills[selector], "loaded skill")
    need(_resolved(path_text(evidence(loaded, "path"), "loaded path")) == root / entry, "source_mismatch", "Wrong loaded entrypoint")
    observed = evidence(loaded, "sha256")
    need(observed is not None, "missing_evidence", "Loaded digest is unproven")
    expect_text(observed, "loaded sha256")
    need(digest(observed) and observed == files.get(entry), "source_mismatch", "Loaded digest contradicts pin")
    for relative, expected_digest in files.items():
        need(hashlib.sha256(_locate(root, relative).read_bytes()).hexdigest() == expected_digest, "source_mismatch", f"Required file differs: {relative}")


def mount_type(path: str, mountinfo: str) -> str | None:
    if not mountinfo or len(mountinfo) > MOUNT_LIMIT or not mountinfo.endswith("\n"):
        return None
    winner, filesystem = "", None
    escapes = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}
    for line in mountinfo.split("\n")[:-1]:
        fields = line.split(" ")
        if "-" not in fields or any(not field for field in fields):
            return None
        separator = fields.index("-")
        if separator < 6 or len(fields) != separator + 4:
            return None
        parts = fields[4].split("\\")
        if any(part[:3] not in escapes for part in parts[1:]):
            return None
        mountpoint = parts[0] + "".join(escapes[part[:3]] + part[3:] for part in parts[1:])
        if not mountpoint.startswith("/"):
            return None
        if (path == mountpoint or path.startswith(mountpoint.rstrip("/") + "/")) and len(mountpoint) >= len(winner):
            winner, filesystem = mountpoint, fields[separator + 1]
    return filesystem


def read_mountinfo() -> str | None:
    if sys.platform != "linux":
        return None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            data = stream.read(MOUNT_LIMIT + 1)
        return data if len(data) <= MOUNT_LIMIT else None
    except (OSError, UnicodeError):
        return None


def _home(value: JsonValue, project_root: Path) -> str:
    need(value is not None, "unsafe_runtime_home", "No active task home evidence")
    home = expect_object(value, "runtime_home")
    need(all(key in home for key in ("path", "parent_home", "dispatcher_home")), "unsafe_runtime_home", "Incomplete process home evidence")
    path, parent, dispatcher = (path_text(home[key], key) for key in ("path", "parent_home", "dispatcher_home"))
    need(path == parent == dispatcher, "unsafe_runtime_home", "Parent and dispatcher must use identical homes")
    try:
        relative = Path(path).relative_to(project_root)
        need(len(relative.parts) == 4 and relative.parts[:2] == (".thunderkit", "runs") and relative.parts[-1] == "hermes-home"
             and relative.parts[2] not in (".", "..") and path == str(project_root / relative), "unsafe_runtime_home", "Wrong task home structure")
        _locate(project_root, relative.as_posix(), directory=True)
    except (OSError, RuntimeError, ValueError, _Denied):
        raise _Denied("unsafe_runtime_home", "Task home must be an existing nonsymlink project directory") from None
    data = read_mountinfo()
    filesystem = mount_type(path, data) if data is not None else None
    need(filesystem in LOCAL_FS, "unsafe_runtime_home", f"Local-disk proof unavailable or unsupported: {filesystem}")
    return path


def qualify(candidate: JsonObject, snapshot: JsonObject, request: Request) -> Verdict:
    decision: Decision = "fallback"
    try:
        _provenance(candidate, snapshot)
        tools = expect_strings(snapshot.get("tools", []), "tools")
        consents = expect_strings(snapshot.get("consents", []), "consents")
        classes: JsonObject = {}
        home_required = False
        for requirement in expect_strings(candidate.get("requires"), "requires"):
            match requirement.partition(":"):
                case ("tool", ":", name):
                    need(name in tools, "capability_missing", f"Required tool absent: {name}")
                case ("model-binding", ":", cls):
                    classes[cls] = cls
                case ("runtime_home", ":", "isolated"):
                    home_required = True
                case ("delivery", ":", "disabled"):
                    if requirement not in consents:
                        return Verdict("blocked", "capability_missing", "Execution requires a delivery opt-out", {}, None)
                case ("user-request", ":", "explicit"):
                    need("lookup" in consents, "missing_evidence", "Lookup requires an explicit request")
                case _:
                    raise ConfigError("Unknown target requirement")
        decision = "blocked" if candidate.get("mode") == "handoff" else "fallback"
        reported = expect_object(snapshot.get("model_bindings", {}), "model_bindings")
        effective: JsonObject = {}
        for slot, raw_class in expect_object(candidate.get("native_roles", classes), "native_roles").items():
            cls = expect_text(raw_class, "class")
            binding = expect_object(evidence(reported, slot), slot)
            descriptor = expect_text(evidence(binding, "descriptor"), "descriptor")
            need(bool(descriptor.strip()), "missing_evidence", f"Empty descriptor for {slot}")
            method = expect_text(evidence(binding, "method"), "method")
            if method not in ("configured", "delegate_route", "explicit_dispatch"):
                raise ConfigError("method must be configured, delegate_route or explicit_dispatch")
            chosen = request.selection[cls]
            keys = [chosen] if isinstance(chosen, str) else expect_strings(chosen, cls)
            members = [{key: expect_text(evidence(expect_object(row, "member"), key), key) for key in ("catalog_key", "provider", "model_id")}
                       for row in expect_list(evidence(binding, "members"), "members")]
            need(bool(members), "missing_evidence", f"Empty members for {slot}")
            for member in members:
                key = member["catalog_key"]
                need(key in keys, "model_mismatch", f"Model outside selected {cls}")
                model = expect_object(expect_object(request.catalog.get("models"), "models").get(key), "model")
                identities = [expect_object(row, "harness") for row in expect_list(model.get("harnesses"), "harnesses")]
                need(any(row.get("harness") == request.host and row.get("provider") == member["provider"] and row.get("model_id") == member["model_id"]
                         for row in identities), "model_mismatch", f"Wrong host model identity for {slot}")
            actual = [member["catalog_key"] for member in members]
            all_reviewers = cls == "reviewers" and request.selection.get("reviewers_mode") == "all"
            need(len(set(actual)) == len(actual) if all_reviewers else actual == keys, "capability_missing", f"Selection collapsed or reordered for {slot}")
            match method:
                case "configured":
                    pass
                case "delegate_route":
                    need(request.host == "hermes" and "omh_delegate_route" in tools, "capability_missing", "Routing method requires the Hermes tool")
                    home_required = True
                case "explicit_dispatch":
                    need(request.host == "hermes" and "dispatch" in consents, "capability_missing", "Explicit dispatch requires Hermes and consent")
                case _:
                    raise ConfigError("method must be configured, delegate_route or explicit_dispatch")
            effective[slot] = {"class": cls, "descriptor": descriptor, "method": method,
                               **(members[0] if cls == "planner" else {"members": [dict(member) for member in members]})}
        home = _home(snapshot.get("runtime_home"), request.project_root) if home_required else None
        return Verdict("delegate", "compatible", "Pinned bytes and native bindings are compatible", effective, home)
    except _Denied as exc:
        return Verdict(decision, exc.reason, exc.detail, {}, None)
    except (FileNotFoundError, NotADirectoryError, RuntimeError):
        return Verdict("fallback", "source_mismatch", "Declared peer file is absent or unresolvable", {}, None)
    except OSError:
        return Verdict("fallback", "missing_evidence", "Peer files could not be read", {}, None)
