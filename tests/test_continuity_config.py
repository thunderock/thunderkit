"""Tests-first contract for model-free continuity, not model_config schema v2.

Proposed JSON v1 shapes (all objects reject unknown/duplicate keys):
* portable: {schema: 1, backup: {enabled, interval_minutes, retention, prune,
  limits: {max_file_bytes, max_snapshot_bytes, max_files, max_path_bytes,
  max_sources}}, restore: {max_context_bytes}, git: {push_after_commit}}.
* project private: {schema: 1, kind: "project-settings", identity, project_root,
  backup_root?, latest_context?}.
* global private: {schema: 1, kind: "global-settings", policy?, global?,
  projects?: {exact_project_id: project_entry}}. Project entries have the same
  identity/project_root/backup_root/latest_context fields as project private
  settings, without schema/kind. The global entry has identity, backup_root?,
  latest_context?, approved_sources?: [absolute_path, ...].
* identity: {kind: "project" | "global", id: opaque_nonempty_id}.
* latest_context: {identity, path}; relative paths stay below the approved root.
* supplied host pointer: {schema: 1, kind: "context-pointer", identity, path}.

Absent fields inherit policy defaults; false is not absence. Portable policy
cannot persist host paths, model choices or grants. explicit_backup_path is the
explicit context locator, NOT permission to create a backup destination.
Snapshot payload validation belongs to R; fixtures here are inert locators.
The model_config tests are intentionally not reused: continuity has its own
schema and must resolve even when model configuration/readiness is unavailable.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, is_dataclass
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType
from typing import Any, TypeAlias
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "skills/references/continuity_config.py"
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
MISSING_BEHAVIOR = (
    "Missing model-free continuity resolver and frozen shared contracts: "
    "skills/references/continuity_config.py is not implemented"
)


class ContinuityConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        # Never fall back to the machine's default tempfile directory or home.
        scratch = os.environ.get("TMPDIR")
        self.assertTrue(scratch, "TMPDIR must name the approved fixture parent")
        self.temp = tempfile.TemporaryDirectory(prefix="thunderkit-C-red-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project-a"
        self.other_project = self.base / "project-b"
        self.home = self.base / "home"
        self.global_dir = self.base / "selected-global"
        self.global_config = self.global_dir / "continuity.json"
        self.backup_root = self.base / "approved-backups"
        for directory in (self.project / ".thunderkit", self.other_project,
                          self.home, self.global_dir, self.backup_root):
            directory.mkdir(parents=True)
        self.policy_path = self.project / ".thunderkit/continuity.json"
        self.private_path = self.project / ".thunderkit/continuity.local.json"
        self.identity_json: JsonObject = {"kind": "project", "id": "project-a"}
        self.global_identity_json: JsonObject = {"kind": "global", "id": "profile-a"}
        self.environment = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "THUNDERKIT_HOME": str(self.global_dir),
            "TMPDIR": str(self.base),
        }
        environment_patch = patch.dict(os.environ, self.environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        previous_cwd = Path.cwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous_cwd)
        # Any accidental model/readiness dependency must fail even if another
        # test module has already imported it into the process.
        modules_patch = patch.dict(sys.modules, {
            "model_config": None, "runtime_readiness": None, "capability_gates": None,
        })
        modules_patch.start()
        self.addCleanup(modules_patch.stop)
        self.api: ModuleType | None = None
        self._api()  # One explicit missing-behavior failure per test, not per subcase.

    def _api(self) -> ModuleType:
        # Explicit assertion gives RED, rather than an import/discovery error.
        self.assertTrue(MODULE.is_file(), MISSING_BEHAVIOR)
        if self.api is None:
            spec = importlib.util.spec_from_file_location("continuity_config", MODULE)
            self.assertIsNotNone(spec)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            module_patch = patch.dict(sys.modules, {"continuity_config": module})
            module_patch.start()
            self.addCleanup(module_patch.stop)
            old_bytecode = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                spec.loader.exec_module(module)
            finally:
                sys.dont_write_bytecode = old_bytecode
            self.api = module
        return self.api

    def _resolve(self, **overrides: Any) -> Any:
        api = self._api()
        kwargs: dict[str, Any] = {
            "project_root": self.project, "global_config": None,
            "explicit_backup_path": None, "scope": "project",
            "expected_identity": api.ScopeIdentity("project", "project-a"),
            "memory_pointer": None, "environment": dict(self.environment),
        }
        kwargs.update(overrides)
        return api.resolve_continuity(**kwargs)

    def _write(self, path: Path, value: JsonValue) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")

    def _context(self, name: str) -> Path:
        path = self.backup_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Historical context\nUnfinished fixture work.\n", encoding="utf-8")
        return path

    def _locator(self, path: Path, identity: JsonObject | None = None) -> JsonObject:
        return {"identity": deepcopy(identity or self.identity_json), "path": str(path)}

    def _project_entry(self, context: Path | None = None) -> JsonObject:
        entry: JsonObject = {
            "identity": deepcopy(self.identity_json), "project_root": str(self.project),
            "backup_root": str(self.backup_root),
        }
        if context is not None:
            entry["latest_context"] = self._locator(context)
        return entry

    def _private(self, context: Path | None = None) -> JsonObject:
        value = {"schema": 1, "kind": "project-settings", **self._project_entry(context)}
        self._write(self.private_path, value)
        return value

    def _global(self, context: Path | None = None) -> JsonObject:
        value: JsonObject = {
            "schema": 1, "kind": "global-settings",
            "projects": {"project-a": self._project_entry(context)},
        }
        self._write(self.global_config, value)
        return value

    def _pointer(self, context: Path, identity: JsonObject | None = None) -> Path:
        path = self.base / "supplied-pointer.json"
        self._write(path, {"schema": 1, "kind": "context-pointer",
                           **self._locator(context, identity)})
        return path

    def _tree(self) -> dict[str, tuple[int, int, bytes | str | None]]:
        """Read only owned fixtures; never follow symlinks or inspect host state."""
        result: dict[str, tuple[int, int, bytes | str | None]] = {}
        for path in self.base.rglob("*"):
            info = path.lstat()
            payload: bytes | str | None = None
            if stat.S_ISLNK(info.st_mode):
                payload = os.readlink(path)
            elif stat.S_ISREG(info.st_mode):
                payload = path.read_bytes()
            result[str(path.relative_to(self.base))] = (info.st_mode, info.st_mtime_ns, payload)
        return result

    def _error(self, code: str, action: Callable[[], object]) -> None:
        api = self._api()
        before = self._tree()
        with self.assertRaises(api.ContinuityError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertIsInstance(caught.exception.detail, str)
        self.assertTrue(caught.exception.detail)
        self.assertEqual(self._tree(), before, "Rejected configuration must be read-only")

    def test_model_free_resolver_is_available(self) -> None:
        self.assertTrue(callable(getattr(self._api(), "resolve_continuity", None)))

    def test_unconfigured_scope_has_no_guessed_root_or_context(self) -> None:
        before = self._tree()
        result = self._resolve()
        self.assertIsNone(result.backup_root)
        self.assertIsNone(result.context_path)
        self.assertEqual(result.context_source, "unconfigured")
        self.assertFalse(result.backup.enabled)
        self.assertTrue(result.git.push_after_commit)
        self.assertEqual(result.approved_sources, ())
        self.assertEqual(self._tree(), before)

    def test_approved_root_enables_defaults_without_implying_context_or_grant(self) -> None:
        value = self._private()
        nonexistent = self.base / "not-created" / "approved-root"
        value["backup_root"] = str(nonexistent)
        self._write(self.private_path, value)
        before = self._tree()
        result = self._resolve()
        self.assertEqual(result.backup_root, nonexistent)
        self.assertIsNone(result.context_path)
        self.assertEqual(result.context_source, "unconfigured")
        self.assertTrue(result.backup.enabled)
        self.assertEqual(result.backup.interval_minutes, 15)
        self.assertEqual(result.backup.retention, 20)
        self.assertFalse(result.backup.prune)
        self.assertTrue(result.git.push_after_commit)
        self.assertFalse(hasattr(result, "grant"))
        self.assertFalse(hasattr(result, "push_grant"))
        self.assertEqual(result.limits.max_file_bytes, 10 * 1024 * 1024)
        self.assertEqual(result.limits.max_snapshot_bytes, 100 * 1024 * 1024)
        self.assertEqual(result.limits.max_context_bytes, 64 * 1024)
        self.assertEqual(result.limits.max_files, 10_000)
        self.assertEqual(result.limits.max_path_bytes, 4096)
        self.assertEqual(result.limits.max_sources, 1000)
        self.assertFalse(nonexistent.exists())
        self.assertEqual(self._tree(), before)

    def test_project_false_overrides_global_true_without_erasing_other_defaults(self) -> None:
        self._private()
        self._write(self.global_config, {
            "schema": 1, "kind": "global-settings",
            "policy": {"schema": 1, "backup": {"enabled": True, "interval_minutes": 30},
                       "git": {"push_after_commit": True}},
        })
        self._write(self.policy_path, {
            "schema": 1, "backup": {"enabled": False}, "git": {"push_after_commit": False},
        })
        result = self._resolve()
        self.assertFalse(result.backup.enabled)
        self.assertFalse(result.git.push_after_commit)
        self.assertEqual(result.backup.interval_minutes, 30)
        self.assertEqual(result.backup.retention, 20)

    def test_portable_policy_accepts_configurable_bounded_limits(self) -> None:
        self._private()
        self._write(self.policy_path, {
            "schema": 1,
            "backup": {"enabled": True, "interval_minutes": 7, "retention": 3, "prune": True,
                       "limits": {"max_file_bytes": 2048, "max_snapshot_bytes": 8192,
                                  "max_files": 12, "max_path_bytes": 256, "max_sources": 4}},
            "restore": {"max_context_bytes": 1024}, "git": {"push_after_commit": False},
        })
        result = self._resolve()
        for field, expected in (("max_file_bytes", 2048), ("max_snapshot_bytes", 8192),
                                ("max_files", 12), ("max_path_bytes", 256),
                                ("max_sources", 4), ("max_context_bytes", 1024)):
            self.assertEqual(getattr(result.limits, field), expected)
        self.assertEqual(result.backup.interval_minutes, 7)
        self.assertEqual(result.backup.retention, 3)
        self.assertTrue(result.backup.prune)

    def test_explicit_context_wins_over_all_remembered_locators(self) -> None:
        explicit = self._context("explicit.md")
        self._private(self._context("project.md"))
        self._global(self._context("indexed.md"))
        pointer = self._pointer(self._context("host.md"))
        before = self._tree()
        result = self._resolve(explicit_backup_path=explicit, memory_pointer=pointer)
        self.assertEqual(result.context_path, explicit)
        self.assertEqual(result.context_source, "explicit")
        self.assertEqual(self._tree(), before, "Reading must not remember the explicit path")

    def test_invalid_explicit_context_never_falls_back(self) -> None:
        self._private(self._context("previous-valid.md"))
        self._error("context_not_found", lambda: self._resolve(
            explicit_backup_path=self.backup_root / "missing.md"))

    def test_project_locator_precedes_global_exact_project_and_host_pointer(self) -> None:
        context = self._context("project.md")
        self._private(context)
        self._global(self._context("indexed.md"))
        pointer = self._pointer(self._context("host.md"))
        result = self._resolve(memory_pointer=pointer)
        self.assertEqual(result.context_path, context)
        self.assertEqual(result.context_source, "project")

    def test_global_exact_project_locator_precedes_supplied_host_pointer(self) -> None:
        context = self._context("indexed.md")
        self._global(context)
        result = self._resolve(memory_pointer=self._pointer(self._context("host.md")))
        self.assertEqual(result.context_path, context)
        self.assertEqual(result.context_source, "global-project")

    def test_matching_explicitly_supplied_host_pointer_is_last_resort(self) -> None:
        context = self._context("host.md")
        result = self._resolve(memory_pointer=self._pointer(context))
        self.assertEqual(result.context_path, context)
        self.assertEqual(result.context_source, "memory")
        self.assertIsNone(result.backup_root, "A locator is not backup destination approval")

    def test_unsupplied_host_pointer_is_not_discovered_by_scanning_home(self) -> None:
        self._pointer(self._context("not-approved-for-discovery.md"))
        self._write(self.home / "unrelated/continuity.json", {"schema": "poison"})
        result = self._resolve()
        self.assertIsNone(result.context_path)
        self.assertEqual(result.context_source, "unconfigured")

    def test_stale_project_locator_does_not_silently_fall_back(self) -> None:
        self._private(self.backup_root / "deleted.md")
        self._global(self._context("other.md"))
        self._error("context_not_found", self._resolve)

    def test_explicit_legacy_markdown_works_without_checkout_or_model_configuration(self) -> None:
        path = self._context("legacy.md")
        result = self._resolve(project_root=None, explicit_backup_path=path)
        self.assertEqual(result.context_path, path)
        self.assertEqual(result.context_source, "explicit")
        self.assertEqual(result.identity, self._api().ScopeIdentity("project", "project-a"))
        self.assertIsNone(result.backup_root)

    def test_no_checkout_implicit_discovery_requires_expected_identity(self) -> None:
        self._global(self._context("indexed.md"))
        self._error("identity_required", lambda: self._resolve(
            project_root=None, expected_identity=None))

    def test_no_checkout_exact_identity_can_use_global_project_index(self) -> None:
        context = self._context("indexed.md")
        self._global(context)
        result = self._resolve(project_root=None)
        self.assertEqual(result.context_path, context)
        self.assertEqual(result.context_source, "global-project")

    def test_project_identity_can_be_resolved_from_bound_private_settings(self) -> None:
        self._private()
        result = self._resolve(expected_identity=None)
        self.assertEqual(result.identity, self._api().ScopeIdentity("project", "project-a"))

    def test_project_root_override_does_not_take_the_global_project_root(self) -> None:
        value = self._global()
        projects = value["projects"]
        assert isinstance(projects, dict)
        entry = projects["project-a"]
        assert isinstance(entry, dict)
        entry["backup_root"] = str(self.base / "global-default-approved-root")
        self._write(self.global_config, value)
        self._private()
        self.assertEqual(self._resolve().backup_root, self.backup_root)

    def test_global_context_and_other_projects_cannot_substitute_for_this_project(self) -> None:
        other = self._project_entry(self._context("project-b.md"))
        other["identity"] = {"kind": "project", "id": "project-b"}
        other["project_root"] = str(self.other_project)
        other["latest_context"] = self._locator(
            self.backup_root / "project-b.md", {"kind": "project", "id": "project-b"})
        global_context = self._context("global.md")
        self._write(self.global_config, {
            "schema": 1, "kind": "global-settings", "projects": {"project-b": other},
            "global": {"identity": self.global_identity_json,
                       "backup_root": str(self.backup_root),
                       "latest_context": self._locator(global_context, self.global_identity_json)},
        })
        result = self._resolve()
        self.assertIsNone(result.backup_root)
        self.assertIsNone(result.context_path)

    def test_identity_mismatches_are_rejected_for_every_locator_source(self) -> None:
        api = self._api()
        context = self._context("context.md")
        wrong: JsonObject = {"kind": "project", "id": "project-b"}
        for source in ("private", "global-index", "memory"):
            with self.subTest(source=source):
                self.private_path.unlink(missing_ok=True)
                self.global_config.unlink(missing_ok=True)
                kwargs: dict[str, Any] = {}
                if source == "private":
                    value = self._private(context)
                    value["identity"] = wrong
                    self._write(self.private_path, value)
                elif source == "global-index":
                    entry = self._project_entry(context)
                    entry["identity"] = wrong
                    self._write(self.global_config, {"schema": 1, "kind": "global-settings",
                                                    "projects": {"project-a": entry}})
                else:
                    kwargs["memory_pointer"] = self._pointer(context, wrong)
                self._error("identity_mismatch", lambda: self._resolve(**kwargs))
        self.private_path.unlink(missing_ok=True)
        self._error("identity_mismatch", lambda: self._resolve(
            expected_identity=api.ScopeIdentity("global", "profile-a")))

    def test_project_path_binding_cannot_be_reused_for_another_checkout(self) -> None:
        for source in ("private", "global-index"):
            with self.subTest(source=source):
                self.private_path.unlink(missing_ok=True)
                self.global_config.unlink(missing_ok=True)
                entry = self._project_entry()
                entry["project_root"] = str(self.other_project)
                if source == "private":
                    self._write(self.private_path, {"schema": 1, "kind": "project-settings", **entry})
                else:
                    self._write(self.global_config, {"schema": 1, "kind": "global-settings",
                                                    "projects": {"project-a": entry}})
                self._error("identity_mismatch", self._resolve)

    def test_remembered_locator_identity_must_match_its_approved_scope(self) -> None:
        value = self._private()
        value["latest_context"] = self._locator(
            self._context("wrong-scope.md"), self.global_identity_json)
        self._write(self.private_path, value)
        self._error("identity_mismatch", self._resolve)

    def test_global_scope_uses_only_its_explicit_source_allowlist(self) -> None:
        api = self._api()
        allowed = self.base / "approved-notes"
        allowed.mkdir()
        (allowed / "note.md").write_text("owned fixture", encoding="utf-8")
        context = self._context("global.md")
        self._write(self.global_config, {
            "schema": 1, "kind": "global-settings",
            "global": {"identity": self.global_identity_json, "backup_root": str(self.backup_root),
                       "latest_context": self._locator(context, self.global_identity_json),
                       "approved_sources": [str(allowed)]},
            "projects": {"project-a": self._project_entry(self._context("project.md"))},
        })
        before = self._tree()
        result = self._resolve(project_root=None, scope="global",
                               expected_identity=api.ScopeIdentity("global", "profile-a"))
        self.assertEqual(result.context_path, context)
        self.assertEqual(result.context_source, "global")
        self.assertEqual(result.approved_sources, (allowed,))
        self.assertEqual(self._tree(), before)

    def test_configuration_location_uses_explicit_then_thunderkit_then_xdg_then_home(self) -> None:
        candidates = (
            self.base / "explicit-global.json", self.global_config,
            self.home / "xdg/thunderkit/continuity.json",
            self.home / ".config/thunderkit/continuity.json",
        )
        for index, path in enumerate(candidates):
            self._write(path, {"schema": 1, "kind": "global-settings",
                               "policy": {"schema": 1, "backup": {"interval_minutes": index + 21}}})
        for index in range(len(candidates)):
            with self.subTest(location=index):
                environment = dict(self.environment)
                if index >= 2:
                    environment.pop("THUNDERKIT_HOME")
                if index >= 3:
                    environment.pop("XDG_CONFIG_HOME")
                result = self._resolve(global_config=candidates[0] if index == 0 else None,
                                       environment=environment)
                self.assertEqual(result.backup.interval_minutes, index + 21)
                self.assertIsNone(result.backup_root)

    def test_explicit_environment_mapping_does_not_merge_ambient_home_selection(self) -> None:
        self._write(self.global_config, {"schema": "invalid-ambient-file"})
        environment = {"HOME": str(self.base / "other-isolated-home")}
        result = self._resolve(environment=environment)
        self.assertEqual(result.backup.interval_minutes, 15)
        self.assertIsNone(result.backup_root)

    def test_supplied_paths_are_literal_not_shell_or_environment_expanded(self) -> None:
        paths = (Path("~/literal.md"), Path("$HOME/literal.md"), Path("${HOME}/literal.md"),
                 Path("$(touch forbidden-marker)/literal.md"))
        for path in paths:
            with self.subTest(path=str(path)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# Literal fixture\n", encoding="utf-8")
                result = self._resolve(explicit_backup_path=path)
                self.assertEqual(result.context_path, self.base / path)
        self.assertFalse((self.base / "forbidden-marker").exists())
        literal_config = Path("$HOME/private-global.json")
        self._write(literal_config, {"schema": 1, "kind": "global-settings",
                                    "policy": {"schema": 1, "backup": {"interval_minutes": 19}}})
        self.assertEqual(self._resolve(global_config=literal_config).backup.interval_minutes, 19)

    def test_model_files_are_neither_read_for_readiness_nor_rewritten(self) -> None:
        self._private()
        # Deliberately invalid model bytes make accidental model-policy reads fail.
        for path in (self.project / ".thunderkit/models.json",
                     self.project / ".thunderkit/model-config.json",
                     self.global_dir / "models.json"):
            path.write_bytes(b"not-json\x00\xff\nfixture model selection must remain unchanged\n")
        before = self._tree()
        result = self._resolve()
        self.assertTrue(result.backup.enabled)
        self.assertEqual(self._tree(), before)

    def test_corrupt_configs_never_become_defaults(self) -> None:
        for path in (self.policy_path, self.private_path, self.global_config):
            for text in ("{", "[]", "null", "\ufeff{}", '{"schema":1} trailing'):
                with self.subTest(source=path.name, text=text):
                    for other in (self.policy_path, self.private_path, self.global_config):
                        other.unlink(missing_ok=True)
                    path.write_text(text, encoding="utf-8")
                    self._error("invalid_config", self._resolve)

    def test_duplicate_keys_are_rejected_at_top_level_and_nested_levels(self) -> None:
        cases = (
            (self.policy_path, '{"schema":1,"schema":1}'),
            (self.policy_path, '{"schema":1,"git":{"push_after_commit":true,"push_after_commit":false}}'),
            (self.private_path, '{"schema":1,"kind":"project-settings","kind":"project-settings"}'),
            (self.global_config, '{"schema":1,"kind":"global-settings","projects":{"a":{},"a":{}}}'),
        )
        for path, text in cases:
            with self.subTest(source=path.name, text=text):
                for other in (self.policy_path, self.private_path, self.global_config):
                    other.unlink(missing_ok=True)
                path.write_text(text, encoding="utf-8")
                self._error("invalid_config", self._resolve)

    def test_unknown_policy_keys_cannot_smuggle_paths_models_or_grants(self) -> None:
        cases: tuple[JsonObject, ...] = (
            {"schema": 1, "backup_root": "/unapproved"},
            {"schema": 1, "grant": {"push": True}},
            {"schema": 1, "classes": {"planner": "unselected-model"}},
            {"schema": 1, "backup": {"path": "/unapproved"}},
            {"schema": 1, "backup": {"limits": {"unbounded": True}}},
            {"schema": 1, "restore": {"execute": True}},
            {"schema": 1, "git": {"grant": True}},
        )
        for raw in cases:
            with self.subTest(raw=raw):
                self._write(self.policy_path, raw)
                self._error("invalid_config", self._resolve)

    def test_private_settings_kinds_and_unknown_nested_keys_are_strict(self) -> None:
        private = {"schema": 1, "kind": "project-settings", **self._project_entry()}
        global_settings: JsonObject = {"schema": 1, "kind": "global-settings"}
        cases: list[tuple[Path, JsonObject]] = [
            (self.private_path, global_settings), (self.global_config, private),
            (self.private_path, {**private, "grant": True}),
            (self.private_path, {**private, "identity": {**self.identity_json, "commands": []}}),
            (self.private_path, {**private, "latest_context": {
                **self._locator(self._context("context.md")), "grant": True}}),
            (self.global_config, {**global_settings, "scan_home": True}),
            (self.global_config, {**global_settings, "global": {
                "identity": self.global_identity_json, "grant": True}}),
        ]
        for path, value in cases:
            with self.subTest(source=path.name, value=value):
                self.private_path.unlink(missing_ok=True)
                self.global_config.unlink(missing_ok=True)
                self._write(path, value)
                self._error("invalid_config", self._resolve)

    def test_policy_schema_types_and_boolean_fields_are_strict(self) -> None:
        for value in (None, True, False, 0, 2, "1", 1.0):
            with self.subTest(schema=value):
                self._write(self.policy_path, {"schema": value})
                self._error("invalid_config", self._resolve)
        for section, key in (("backup", "enabled"), ("backup", "prune"), ("git", "push_after_commit")):
            for value in (None, 0, 1, "true", [], {}):
                with self.subTest(section=section, key=key, value=value):
                    self._write(self.policy_path, {"schema": 1, section: {key: value}})
                    self._error("invalid_config", self._resolve)
        for section in ("backup", "restore", "git"):
            for value in (None, True, [], "policy"):
                with self.subTest(section=section, value=value):
                    self._write(self.policy_path, {"schema": 1, section: value})
                    self._error("invalid_config", self._resolve)

    def test_numeric_budgets_reject_bools_nonfinite_and_out_of_range_values(self) -> None:
        # Upper bounds are part of this proposed v1 contract, not platform limits.
        bounds = {
            ("backup", "interval_minutes"): 10_080,
            ("backup", "retention"): 10_000,
            ("limits", "max_file_bytes"): 1024 ** 3,
            ("limits", "max_snapshot_bytes"): 10 * 1024 ** 3,
            ("limits", "max_files"): 100_000,
            ("limits", "max_path_bytes"): 4096,
            ("limits", "max_sources"): 10_000,
            ("restore", "max_context_bytes"): 16 * 1024 ** 2,
        }
        for (section, key), upper in bounds.items():
            for value in (True, False, None, "2", 1.5, float("nan"), float("inf"),
                          float("-inf"), 0, -1, upper + 1):
                with self.subTest(section=section, key=key, value=value):
                    raw: JsonObject = {"schema": 1}
                    raw["backup" if section == "limits" else section] = (
                        {"limits": {key: value}} if section == "limits" else {key: value})
                    self._write(self.policy_path, raw)
                    self._error("invalid_config", self._resolve)
        self._write(self.policy_path, {"schema": 1, "backup": {
            "limits": {"max_file_bytes": 4096, "max_snapshot_bytes": 1024}}})
        self._error("invalid_config", self._resolve)

    def test_symlinked_config_files_and_ancestor_directories_are_rejected(self) -> None:
        target = self.base / "owned-config-target.json"
        self._write(target, {"schema": 1})
        self.policy_path.symlink_to(target)
        self._error("unsafe_path", self._resolve)
        self.policy_path.unlink()
        source = self.project / ".thunderkit"
        relocated = self.base / "relocated-private-dir"
        source.rename(relocated)
        self._write(relocated / "continuity.json", {"schema": 1})
        source.symlink_to(relocated, target_is_directory=True)
        self._error("unsafe_path", self._resolve)

    def test_symlinked_backup_roots_and_explicit_context_are_rejected(self) -> None:
        value = self._private()
        link = self.base / "root-link"
        link.symlink_to(self.backup_root, target_is_directory=True)
        value["backup_root"] = str(link)
        self._write(self.private_path, value)
        self._error("unsafe_path", self._resolve)
        self.private_path.unlink()
        context = self._context("real.md")
        linked_context = self.base / "context-link.md"
        linked_context.symlink_to(context)
        self._error("unsafe_path", lambda: self._resolve(explicit_backup_path=linked_context))

    def test_remembered_paths_cannot_escape_approved_backup_root(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("# Outside approved root", encoding="utf-8")
        for path in ("../outside.md", "nested/../../outside.md", str(outside)):
            with self.subTest(path=path):
                value = self._private()
                value["latest_context"] = {"identity": self.identity_json, "path": path}
                self._write(self.private_path, value)
                self._error("unsafe_path", self._resolve)

    def test_relative_remembered_path_is_bound_to_approved_root(self) -> None:
        context = self._context("snapshots/context.md")
        value = self._private()
        value["latest_context"] = {"identity": self.identity_json, "path": "snapshots/context.md"}
        self._write(self.private_path, value)
        self.assertEqual(self._resolve().context_path, context)

    def test_path_controls_are_rejected_without_echoing_raw_unsafe_values(self) -> None:
        api = self._api()
        for control in ("\x00", "\n", "\r", "\t", "\x1b", "\x7f"):
            with self.subTest(control=ord(control)):
                unsafe = str(self.base / ("PRIVATE_PATH_SENTINEL" + control + "context.md"))
                before = self._tree()
                with self.assertRaises(api.ContinuityError) as caught:
                    self._resolve(explicit_backup_path=Path(unsafe))
                self.assertEqual(caught.exception.code, "unsafe_path")
                self.assertNotIn(control, caught.exception.detail)
                self.assertNotIn("PRIVATE_PATH_SENTINEL", caught.exception.detail)
                self.assertEqual(self._tree(), before)

    def test_scope_identity_values_are_validated_without_normalizing_tokens(self) -> None:
        api = self._api()
        for kind, identifier in (("other", "project-a"), ("project", ""),
                                 ("project", "../project-a"), ("project", "project/a"),
                                 ("project", " project-a "), ("global", "profile\na")):
            with self.subTest(kind=kind, identifier=identifier):
                with self.assertRaises(api.ContinuityError):
                    api.ScopeIdentity(kind, identifier)
        identity = api.ScopeIdentity("project", "Case-Sensitive_Project.1")
        self.assertEqual(identity.id, "Case-Sensitive_Project.1")
        self._error("invalid_config", lambda: self._resolve(scope="automatic"))

    def test_shared_request_and_result_interfaces_are_frozen_dataclasses(self) -> None:
        api = self._api()
        required = {
            "ScopeIdentity": {"kind", "id"},
            "Limits": {"max_file_bytes", "max_snapshot_bytes", "max_context_bytes",
                       "max_files", "max_path_bytes", "max_sources"},
            "BackupPolicy": {"enabled", "interval_minutes", "retention", "prune"},
            "GitPolicy": {"push_after_commit"},
            "ResolvedSettings": {"identity", "project_root", "backup_root", "context_path",
                                 "context_source", "backup", "git", "limits", "approved_sources"},
            "StorageIdentity": {"root", "device", "inode"},
            "SnapshotRequest": {"settings", "storage", "approved_sources"},
            "SnapshotResult": {"identity", "snapshot_path", "status", "omissions", "history_status"},
            "ContextLoad": {"identity", "source", "text", "integrity_verified", "warnings"},
            "ExportResult": {"identity", "destination", "status", "exported_paths", "omissions", "history_status"},
            "PushRequest": {"identity", "repository", "remote", "destination_ref", "oid"},
            "PushGrant": {"identity", "repository", "remote_url", "destination_ref"},
            "PushResult": {"status", "oid", "destination_ref", "remote_oid"},
        }
        for name, required_fields in required.items():
            with self.subTest(export=name):
                cls: Any = getattr(api, name, None)
                self.assertIsNotNone(cls, f"Missing exported contract: {name}")
                self.assertTrue(is_dataclass(cls), f"{name} must be an exported dataclass")
                self.assertTrue(cls.__dataclass_params__.frozen, f"{name} must be frozen")
                self.assertLessEqual(required_fields, {field.name for field in fields(cls)})
        settings = self._resolve()
        for value, field, replacement in (
            (settings, "context_path", Path("not-authorized")),
            (settings.identity, "id", "other-project"),
            (settings.backup, "enabled", True),
            (settings.git, "push_after_commit", False),
            (settings.limits, "max_file_bytes", 0),
        ):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, field, replacement)

    def test_error_contract_exposes_stable_code_and_safe_detail(self) -> None:
        api = self._api()
        error = api.ContinuityError("invalid_config", "continuity.schema must be 1")
        self.assertIsInstance(error, Exception)
        self.assertEqual(error.code, "invalid_config")
        self.assertEqual(error.detail, "continuity.schema must be 1")
        self.assertIn(error.detail, str(error))


if __name__ == "__main__":
    unittest.main()
