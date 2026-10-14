"""Model-free checkpoint commands; saved settings never supply consent.

Setup writes inert private configuration, not host bindings. Capture qualifies
only its explicit store and never supplies publication authority. Global capture
requires explicit settings. Host and scheduler changes require separate commands
and fresh approval; prior setup results contain ownership, never consent.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import NoReturn, cast

import backup_store as S
import checkpoint_runtime as H
import checkpoint_setup as Setup
import continuity_config as C


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's original diagnostic can contain private argument values.
        raise C.ContinuityError("invalid_arguments", "Checkpoint arguments are invalid.")


class _Arguments(argparse.Namespace):
    command: str
    project_root: Path | None
    global_config: Path | None
    scope: C.ScopeKind
    identity: str
    backup_root: Path
    source: list[Path]
    context_entrypoint: list[str]
    include_history: bool
    history_ref: str | None
    integration_root: Path
    state_dir: Path
    approve: bool
    python: Path
    setup_result: Path
    crontab: Path
    service_qualified: bool
    exclusive_writer: bool


def _cli_path(value: str) -> Path:
    text = C.validate_path_text(value, absolute=True)
    path = Path(text)
    if str(path) != text:
        raise C.ContinuityError("unsafe_path", "Paths must retain their literal absolute spelling.")
    return path


def _parser() -> _SafeParser:
    parser = _SafeParser(
        prog="checkpoint_cli.py", allow_abbrev=False,
        description="Model-free project preview/setup and project or global one-shot capture.",
        epilog="Use literal absolute paths. Host changes require separate commands; no command supplies a push grant.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "preview": "Inspect the explicit setup request without writes or approval",
        "setup": "Capture once and write private settings with fresh --approve; no service activation",
        "resume-setup": "Resume an exact interrupted local setup claim with fresh --approve",
        "capture": "Capture explicit sources with fresh --approve; publication remains separately denied",
        "host-preview": "Inspect a local connection using the exact successful setup result; no writes",
        "host-install": "Install the inspected repo-local hook with separate fresh --approve",
    }
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description, description=description, allow_abbrev=False)
        command.add_argument("--project-root", type=_cli_path, required=name != "capture")
        command.add_argument("--scope", choices=("project", "global") if name == "capture" else ("project",),
                             required=True)
        command.add_argument("--identity", required=True)
        command.add_argument("--backup-root", type=_cli_path, required=True)
        command.add_argument("--source", type=_cli_path, action="append", required=True)
        command.add_argument("--context-entrypoint", type=C.validate_relpath, action="append", default=[])
        command.add_argument("--include-history", action="store_true", default=False)
        command.add_argument("--history-ref", type=C.validate_ref)
        if name == "capture" or name.startswith("host-"):
            command.add_argument("--state-dir", type=_cli_path, required=True,
                                 help="Existing private directory disjoint from sources, project and store")
            if name == "capture":
                command.add_argument("--global-config", type=_cli_path,
                                     help="Explicit global settings required only for global capture")
            else:
                command.add_argument("--python", type=_cli_path, required=True,
                                     help="Explicit trusted interpreter; never selected from backup data")
                command.add_argument("--setup-result", type=_cli_path, required=True,
                                     help="Private JSON stdout retained from successful setup, not approval")
        else:
            command.add_argument("--integration-root", type=_cli_path, required=True)
        if name not in ("preview", "host-preview"):
            command.add_argument("--approve", action="store_true", default=False,
                                 help="Fresh consent for this invocation only; never restored from settings")
    for name in ("host-remove", "schedule-preview", "schedule-install", "schedule-remove"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--project-root", type=_cli_path, required=True)
        command.add_argument("--state-dir", type=_cli_path, required=True)
        if name.startswith("schedule-"):
            command.add_argument("--crontab", type=_cli_path, required=True)
            command.add_argument("--service-qualified", action="store_true", default=False)
            command.add_argument("--exclusive-writer", action="store_true", default=False)
        if name != "schedule-preview":
            command.add_argument("--approve", action="store_true", default=False)
    for name in ("global-setup", "global-preview", "global-install", "global-remove"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--scope", choices=("global",), required=True)
        command.add_argument("--identity", required=True)
        command.add_argument("--state-dir", type=_cli_path, required=True)
        if name != "global-remove":
            command.add_argument("--global-config", type=_cli_path, required=True)
            command.add_argument("--backup-root", type=_cli_path, required=True)
            command.add_argument("--source", type=_cli_path, action="append", required=True)
            command.add_argument("--context-entrypoint", type=C.validate_relpath, action="append", default=[])
        if name in ("global-preview", "global-install"):
            command.add_argument("--setup-result", type=_cli_path, required=True)
            command.add_argument("--python", type=_cli_path, required=True)
        if name != "global-setup":
            command.add_argument("--crontab", type=_cli_path, required=True)
            command.add_argument("--service-qualified", action="store_true", default=False)
            command.add_argument("--exclusive-writer", action="store_true", default=False)
        if name != "global-preview":
            command.add_argument("--approve", action="store_true", default=False)
    return parser


def _settings(args: _Arguments) -> C.ResolvedSettings:
    if args.scope == "global":
        assert args.global_config is not None
        settings = C.resolve_continuity(
            global_config=args.global_config, scope="global",
            expected_identity=C.ScopeIdentity("global", args.identity),
        )
        if settings.backup_root != args.backup_root:
            raise C.ContinuityError("identity_mismatch", "Backup root does not match the configured scope.")
        return settings
    # Read only the project's inert policy, never model config or remembered
    # roots, locators, sources or grants. Explicit false policy stays false.
    assert args.project_root is not None
    policy_path = C.safe_path(args.project_root / ".thunderkit/continuity.json")
    try:
        policy_path.lstat()
    except FileNotFoundError:
        policy: C.JsonObject = {}
    else:
        policy = C.validate_policy(C.read_json(policy_path))
    backup = cast(C.JsonObject, policy.get("backup", {}))
    git = cast(C.JsonObject, policy.get("git", {}))
    limits = cast(C.JsonObject, backup.get("limits", {})).copy()
    limits.update(cast(C.JsonObject, policy.get("restore", {})))
    return C.ResolvedSettings(
        identity=C.ScopeIdentity(args.scope, args.identity),
        project_root=args.project_root, backup_root=args.backup_root,
        context_path=None, context_source="unconfigured",
        backup=C.BackupPolicy(
            enabled=cast(bool, backup.get("enabled", True)),
            interval_minutes=cast(int, backup.get("interval_minutes", 15)),
            retention=cast(int, backup.get("retention", 20)),
            prune=cast(bool, backup.get("prune", False)),
        ),
        git=C.GitPolicy(push_after_commit=cast(bool, git.get("push_after_commit", True))),
        limits=C.Limits(**{name: cast(int, value) for name, value in limits.items()}),
    )


def _global_preflight(snapshot: C.SnapshotRequest, state_dir: Path) -> None:
    """Reject unsafe global inputs before the storage qualification writes.

    These read-only checks do not replace S/H's descriptor-bound revalidation.
    """
    C.validate_path_text(str(state_dir), absolute=True, max_bytes=snapshot.settings.limits.max_path_bytes)
    state = C.safe_path(state_dir, must_exist=True).lstat()
    if not stat.S_ISDIR(state.st_mode) or stat.S_IMODE(state.st_mode) != 0o700 or state.st_uid != os.geteuid():
        raise C.ContinuityError("unsafe_path", "State requires an existing private owned directory.")
    root = snapshot.storage.root
    for path in (root, *snapshot.approved_sources):
        if state_dir.is_relative_to(path) or path.is_relative_to(state_dir):
            raise C.ContinuityError("unsafe_path", "State must be disjoint from sources and storage.")
    for source in snapshot.approved_sources:
        if source.is_relative_to(root) or root.is_relative_to(source):
            raise C.ContinuityError("unsafe_path", "Sources must be disjoint from storage.")
        info = C.safe_path(source, must_exist=True).lstat()
        if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise C.ContinuityError("unsafe_path", "Sources require safe directories or single-link regular files.")


def _receipt_require(condition: bool) -> None:
    if not condition:
        raise C.ContinuityError("invalid_setup_receipt", "Use the exact private successful setup result.")


def _receipt_object(value: C.JsonValue, keys: set[str]) -> C.JsonObject:
    _receipt_require(isinstance(value, dict) and set(value) == keys)
    return cast(C.JsonObject, value)


def _receipt_list(value: C.JsonValue) -> list[C.JsonValue]:
    _receipt_require(isinstance(value, list))
    return cast(list[C.JsonValue], value)


def _receipt_text(value: C.JsonValue) -> str:
    _receipt_require(isinstance(value, str))
    return cast(str, value)


def _receipt_int(value: C.JsonValue) -> int:
    _receipt_require(type(value) is int and 0 <= value < 2**128)
    return cast(int, value)


def _private_json(path: Path) -> C.JsonValue:
    with Setup._directory(path.parent) as parent:
        data, info = Setup._read_file(parent, path.name, C.CONFIG_MAX_BYTES)
    _receipt_require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
    return C.decode_json(data)


def _owned_paths(value: C.JsonValue) -> tuple[Setup.OwnedPath, ...]:
    result: list[Setup.OwnedPath] = []
    for item in _receipt_list(value):
        row = _receipt_object(item, {"path", "device", "inode", "uid", "mode", "content_sha256"})
        result.append(Setup.OwnedPath(_cli_path(_receipt_text(row["path"])),
            _receipt_int(row["device"]), _receipt_int(row["inode"]), _receipt_int(row["uid"]),
            _receipt_int(row["mode"]), None if row["content_sha256"] is None else C.validate_sha256(row["content_sha256"])))
    return tuple(result)


def _capabilities(value: C.JsonValue) -> tuple[Setup.Capability, ...]:
    result: list[Setup.Capability] = []
    for item in _receipt_list(value):
        row = _receipt_object(item, {"name", "status", "reason"})
        _receipt_require(row["name"] in ("periodic", "ref_events", "auto_push", "full_target")
                         and row["status"] in ("supported", "disabled", "blocked"))
        result.append(Setup.Capability(cast(Setup.CapabilityName, row["name"]),
            cast(Setup.CapabilityStatus, row["status"]), _receipt_text(row["reason"])))
    _receipt_require(len(result) == 4 and len({item.name for item in result}) == 4)
    return tuple(result)


def _setup_receipt(path: Path) -> Setup.InstalledState:
    """Strict data decoder; no deserialization of commands, types or authority."""
    try:
        raw = _receipt_object(_private_json(path), {"status", "changed_paths", "receipt",
                                                   "blockers", "capabilities", "retained_paths"})
        _receipt_require(raw["status"] == "configured" and raw["blockers"] == [] and raw["retained_paths"] == [])
        row = _receipt_object(raw["receipt"], {"identity", "project_root", "integration_root", "storage",
            "manifest_path", "manifest_sha256", "files", "directories", "parents", "capabilities"})
        store = _receipt_object(row["storage"], {"root", "device", "inode", "backing_fingerprint",
                                                "mount_fingerprint", "durability"})
        storage = C.StorageIdentity(_cli_path(_receipt_text(store["root"])),
            _receipt_int(store["device"]), _receipt_int(store["inode"]),
            None if store["backing_fingerprint"] is None else _receipt_text(store["backing_fingerprint"]),
            None if store["mount_fingerprint"] is None else _receipt_text(store["mount_fingerprint"]),
            _receipt_text(store["durability"]))
        receipt = Setup.InstalledState(C.decode_identity(row["identity"]),
            _cli_path(_receipt_text(row["project_root"])), _cli_path(_receipt_text(row["integration_root"])),
            storage, _cli_path(_receipt_text(row["manifest_path"])), C.validate_sha256(row["manifest_sha256"]),
            _owned_paths(row["files"]), _owned_paths(row["directories"]), _owned_paths(row["parents"]),
            _capabilities(row["capabilities"]))
        _receipt_require(_capabilities(raw["capabilities"]) == receipt.capabilities
                         and raw["changed_paths"] == [str(item.path) for item in receipt.files])
        return receipt
    except (C.ContinuityError, OSError, ValueError, TypeError, KeyError):
        raise C.ContinuityError("invalid_setup_receipt", "Use the exact private successful setup result.") from None


def _host_snapshot(args: _Arguments) -> C.SnapshotRequest:
    settings = _settings(args)
    receipt = _setup_receipt(args.setup_result)
    if (receipt.identity != settings.identity or receipt.project_root != settings.project_root
            or receipt.storage.root != settings.backup_root):
        raise C.ContinuityError("identity_mismatch", "Setup scope does not match the explicit host request.")
    # Reuse the recorded backing, never inspect/qualify a same-path replacement.
    S._check_storage(receipt.storage)
    Setup._validate_receipt(receipt)
    request = Setup.SetupRequest(settings, tuple(args.source), receipt.integration_root,
        context_entrypoints=tuple(args.context_entrypoint), include_history=args.include_history,
        history_ref=args.history_ref)
    private, integration = Setup._documents(request, receipt.manifest_path)
    if (_private_json(receipt.project_root / ".thunderkit/continuity.local.json") != private
            or _private_json(receipt.integration_root / "config.json") != integration):
        raise C.ContinuityError("setup_scope_changed", "Repeat setup review for the changed explicit scope.")
    scope = receipt.storage.root / "projects" / receipt.identity.id / "snapshots"
    manifest_path = receipt.manifest_path
    _receipt_require(manifest_path.name == "manifest.json" and manifest_path.parent.parent == scope)
    manifest = S.verify_snapshot(manifest_path, receipt.identity, settings.limits)
    with Setup._directory(manifest_path.parent) as parent:
        content, _ = Setup._read_file(parent, manifest_path.name, C.MANIFEST_MAX_BYTES)
    _receipt_require(hashlib.sha256(content).hexdigest() == receipt.manifest_sha256
                     and manifest.completion == "complete" and manifest.repository == str(receipt.project_root)
                     and manifest.ref == args.history_ref)
    return C.SnapshotRequest(settings, receipt.storage, tuple(args.source),
                            tuple(args.context_entrypoint), args.include_history, args.history_ref)


def _host_command(args: _Arguments) -> tuple[dict[str, object], bool]:
    import checkpoint_host as Host
    assert args.project_root is not None
    if args.command in ("host-preview", "host-install"):
        snapshot = _host_snapshot(args)
        preview = Host.preview_connections(Host.HostRequest(snapshot, args.state_dir, args.python))
        if args.command == "host-preview":
            # Raw bootstrap/binding bytes are not part of the operator display.
            return {"status": preview.status, "hook_path": preview.hook_path,
                    "periodic_command": preview.periodic_command, "blockers": preview.blockers}, preview.status == "approval_required"
        installed = Host.apply_connections(preview, approval=True)
        return asdict(installed), installed.status == "configured"
    connection = Host.load_connections(args.state_dir, project_root=args.project_root)
    if args.command == "host-remove":
        removed = Host.remove_connections(connection, approval=True)
        return asdict(removed), removed.status == "removed"
    driver = Host.CrontabDriver(args.crontab, service_qualified=args.service_qualified,
                               exclusive_writer=args.exclusive_writer)
    if args.command == "schedule-remove":
        result = Host.remove_schedule(Host.load_schedule(args.state_dir, project_root=args.project_root),
                                      driver, approval=True)
    else:
        schedule = Host.preview_schedule(connection, driver, service_qualified=args.service_qualified,
                                         exclusive_writer=args.exclusive_writer)
        if args.command == "schedule-preview":
            return {"status": schedule.status, "block": schedule.block.decode("utf-8"),
                    "blockers": schedule.blockers}, schedule.status == "approval_required"
        result = Host.apply_schedule(schedule, driver, approval=True)
    # Never expose unrelated table bytes or serialize executable authority.
    return {"status": result.status, "blockers": result.blockers,
            "ownership_retained": result.installation is not None and result.status == "blocked"}, result.status in ("configured", "removed")


def _global_setup(args: _Arguments) -> tuple[dict[str, object], bool]:
    settings = _settings(args)
    if not settings.backup.enabled:
        raise C.ContinuityError("backup_disabled", "Global policy disables checkpoint capture.")
    snapshot = C.SnapshotRequest(settings, S.inspect_storage(args.backup_root), tuple(args.source),
                                tuple(args.context_entrypoint))
    _global_preflight(snapshot, args.state_dir)
    qualified = S.qualify_storage(snapshot.storage, approval=True)
    _receipt_require(qualified.readback_verified)
    snapshot = C.SnapshotRequest(settings, qualified.identity, snapshot.approved_sources,
                                snapshot.context_entrypoints)
    result = H.checkpoint(H.CheckpointRequest(snapshot, args.state_dir))
    success = (result.status == "captured" and result.snapshot is not None
               and result.snapshot.status in ("complete", "unchanged"))
    return {"schema": 1, "kind": "global-setup-result", "status": "configured" if success else "blocked",
            "settings": C.settings_to_json(settings), "storage": asdict(qualified.identity),
            "sources": list(args.source), "context_entrypoints": list(args.context_entrypoint),
            "state_dir": args.state_dir,
            "snapshot": asdict(result.snapshot) if result.snapshot is not None else None,
            "blockers": list(result.warnings) if not success else []}, success


def _global_snapshot(args: _Arguments) -> C.SnapshotRequest:
    settings = _settings(args)
    raw = _receipt_object(_private_json(args.setup_result), {"schema", "kind", "status", "settings",
        "storage", "sources", "context_entrypoints", "state_dir", "snapshot", "blockers"})
    _receipt_require(type(raw["schema"]) is int and raw["schema"] == 1
                     and raw["kind"] == "global-setup-result" and raw["status"] == "configured"
                     and raw["blockers"] == [])
    if (raw["settings"] != C.settings_to_json(settings) or raw["sources"] != list(map(str, args.source))
            or raw["context_entrypoints"] != args.context_entrypoint or raw["state_dir"] != str(args.state_dir)):
        raise C.ContinuityError("setup_scope_changed", "Global setup scope has changed; review it again.")
    store = _receipt_object(raw["storage"], {"root", "device", "inode", "backing_fingerprint",
                                            "mount_fingerprint", "durability"})
    storage = C.StorageIdentity(_cli_path(_receipt_text(store["root"])),
        _receipt_int(store["device"]), _receipt_int(store["inode"]),
        None if store["backing_fingerprint"] is None else _receipt_text(store["backing_fingerprint"]),
        None if store["mount_fingerprint"] is None else _receipt_text(store["mount_fingerprint"]),
        _receipt_text(store["durability"]))
    request = C.SnapshotRequest(settings, storage, tuple(args.source), tuple(args.context_entrypoint))
    S._check_storage(storage)
    _global_preflight(request, args.state_dir)
    saved = _receipt_object(raw["snapshot"], {"identity", "snapshot_path", "status", "omissions", "history_status",
                                             "manifest_sha256", "last_locator", "last_verified_snapshot"})
    _receipt_require(saved["identity"] == asdict(settings.identity) and saved["status"] in ("complete", "unchanged")
                     and saved["omissions"] == [] and saved["history_status"] == "not-applicable")
    path = _cli_path(_receipt_text(saved["snapshot_path"]))
    digest = C.validate_sha256(saved["manifest_sha256"])
    _receipt_require(path.parent == storage.root / "global" / settings.identity.id / "snapshots")
    manifest = S.verify_snapshot(path, settings.identity, settings.limits)
    with Setup._directory(path) as parent:
        content, _ = Setup._read_file(parent, "manifest.json", C.MANIFEST_MAX_BYTES)
    _receipt_require(hashlib.sha256(content).hexdigest() == digest and manifest.completion == "complete"
                     and manifest.repository is None and manifest.context_entrypoints == request.context_entrypoints)
    return request


def _global_command(args: _Arguments) -> tuple[dict[str, object], bool]:
    if args.command == "global-setup":
        return _global_setup(args)
    if not args.service_qualified or not args.exclusive_writer:
        raise C.ContinuityError("scheduler_unqualified", "Qualified service and exclusive writer evidence are required.")
    # Read-only input checks precede construction/launch of the explicit transport.
    snapshot = None if args.command == "global-remove" else _global_snapshot(args)
    import checkpoint_global as Global
    import checkpoint_host as Host
    driver = Host.CrontabDriver(args.crontab, service_qualified=args.service_qualified,
                               exclusive_writer=args.exclusive_writer)
    if args.command == "global-remove":
        installation = Global.load_global(args.state_dir, identity=C.ScopeIdentity("global", args.identity))
        result = Global.remove_global(installation, driver, approval=True)
    else:
        assert snapshot is not None
        assert args.global_config is not None
        request = Global.GlobalScheduleRequest(snapshot, args.state_dir, args.python, args.global_config)
        preview = Global.preview_global(request, driver, service_qualified=args.service_qualified,
                                        exclusive_writer=args.exclusive_writer)
        if args.command == "global-preview":
            return {"status": preview.status, "blockers": preview.blockers}, preview.status == "approval_required"
        result = Global.apply_global(preview, driver, approval=True)
    output = asdict(result)
    if result.installation is not None:
        # Durable loading uses the private state directory, not an exported
        # bootstrap payload. Keep raw binding bytes out of the operator JSON.
        del output["installation"]["binding"]
    return output, result.status in ("configured", "removed")


def _json_path(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError("Unsupported checkpoint result value.")


def _error(code: str, detail: str) -> int:
    print(json.dumps({"error": {"code": code, "detail": detail}}, ensure_ascii=True), file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Return actual API dataclasses as JSON; refuse missing consent before I/O."""
    try:
        args = _parser().parse_args(argv, namespace=_Arguments())
        if args.command == "capture":
            if ((args.scope == "global" and (args.project_root is not None or args.global_config is None))
                    or (args.scope == "project" and (args.project_root is None or args.global_config is not None))):
                return _error("invalid_arguments", "Capture arguments must match the selected scope.")
        if args.command not in ("preview", "host-preview", "schedule-preview", "global-preview") and not args.approve:
            return _error("approval_required", "Fresh --approve is required for this invocation.")
        if args.command.startswith("global-"):
            output, success = _global_command(args)
            print(json.dumps(output, default=_json_path, ensure_ascii=True, allow_nan=False))
            return 0 if success else 2
        if args.command.startswith(("host-", "schedule-")):
            output, success = _host_command(args)
            print(json.dumps(output, default=_json_path, ensure_ascii=True, allow_nan=False))
            return 0 if success else 2
        settings = _settings(args)
        result: Setup.SetupPreview | Setup.SetupResult | H.CheckpointResult
        if args.command == "capture":
            if not settings.backup.enabled:
                scope = "Global" if args.scope == "global" else "Project"
                return _error("backup_disabled", f"{scope} policy disables checkpoint capture.")
            storage = S.inspect_storage(args.backup_root)
            snapshot = C.SnapshotRequest(settings, storage, tuple(args.source),
                                         tuple(args.context_entrypoint), args.include_history, args.history_ref)
            if args.scope == "global":
                _global_preflight(snapshot, args.state_dir)
            qualified = S.qualify_storage(storage, approval=True)
            if not qualified.readback_verified:
                return _error("storage_unqualified", "Storage readback qualification was not established.")
            snapshot = C.SnapshotRequest(settings, qualified.identity, snapshot.approved_sources,
                                         snapshot.context_entrypoints, snapshot.include_history, snapshot.history_ref)
            result = H.checkpoint(H.CheckpointRequest(snapshot, args.state_dir))
            success = result.status == "captured"
        else:
            request = Setup.SetupRequest(settings, tuple(args.source), args.integration_root,
                                         context_entrypoints=tuple(args.context_entrypoint),
                                         include_history=args.include_history, history_ref=args.history_ref)
            if args.command == "resume-setup":
                result = Setup.resume_setup(request, approval=True)
                success = result.status == "configured"
            else:
                preview = Setup.preview_setup(request)
                if args.command == "preview":
                    result = preview
                    success = preview.status == "approval_required"
                else:
                    result = Setup.apply_setup(preview, approval=True)
                    success = result.status == "configured"
        encoded = json.dumps(asdict(result), default=_json_path, ensure_ascii=True, allow_nan=False)
    except C.ContinuityError as error:
        return _error(error.code, error.detail)
    except Exception:
        # Never stringify raw OS/input errors or emit a potentially private traceback.
        return _error("checkpoint_failed", "Checkpoint could not complete safely.")
    print(encoded)
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
