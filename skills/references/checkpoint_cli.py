"""Model-free checkpoint commands; saved settings never supply consent.

Setup writes inert private configuration, not host bindings. Capture qualifies
only its explicit store and never supplies publication authority. Global capture
requires explicit settings; host activation and uninstall remain out of scope.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
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
        epilog="Use literal absolute paths and existing store/state directories. No host activation or push grant.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "preview": "Inspect the explicit setup request without writes or approval",
        "setup": "Capture once and write private settings with fresh --approve; no service activation",
        "capture": "Capture explicit sources with fresh --approve; publication remains separately denied",
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
        if name == "capture":
            command.add_argument("--global-config", type=_cli_path,
                                 help="Explicit global settings required only for global capture")
            command.add_argument("--state-dir", type=_cli_path, required=True,
                                 help="Existing private directory disjoint from sources, project and store")
        else:
            command.add_argument("--integration-root", type=_cli_path, required=True)
        if name != "preview":
            command.add_argument("--approve", action="store_true", default=False,
                                 help="Fresh consent for this invocation only; never restored from settings")
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
        if args.command != "preview" and not args.approve:
            return _error("approval_required", "Fresh --approve is required for this invocation.")
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
