"""H25: changed portable policy must gate the real installed host boundary.

Only disposable repositories are committed. Scheduler storage is the unchanged
transport fixture, not evidence of an activated service. Core runtime receives
explicit requests and is deliberately not patched or made ambient-config-aware.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest

import test_checkpoint_host_lifecycle as lifecycle
import test_checkpoint_schedule as schedule_tests


def inventory(rows):
    """Receipt hashes; actual assertions compare the original bytes and identity."""
    return {name: {"device": row[0], "inode": row[1], "mode": row[2],
                   "size": len(row[3]) if row[3] is not None else None,
                   "sha256": hashlib.sha256(row[3]).hexdigest() if row[3] is not None else None}
            for name, row in rows.items()}


class CheckpointHostPolicyTests(lifecycle.HostLifecycleFixture):
    def clean(self):
        try:
            super().clean()
        finally:
            processes = []
            for process in getattr(self, "child_processes", []):
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    group_absent = True
                else:
                    group_absent = False
                processes.append({"pid": process.pid, "exit": process.returncode,
                                  "absent": not Path(f"/proc/{process.pid}").exists(),
                                  "group_absent": group_absent})
            local = [{"pid": p.pid, "exit": p.returncode,
                      "absent": not Path(f"/proc/{p.pid}").exists()}
                     for p in getattr(self.case, "processes", [])]
            restored = lifecycle.signals() == self.initial_signals
            root = getattr(getattr(self.case, "fixture", None), "base", None)
            lifecycle.record({"event": "policy_after_doCleanups", "case": self.id(),
                              "root": str(root), "root_absent": root is not None and not root.exists(),
                              "signals_before": self.initial_signals,
                              "signals_after": lifecycle.signals(), "signals_restored": restored,
                              "owned_children": processes, "local_git_python": local,
                              "denied": getattr(self.case, "denied", [])})
            self.assertTrue(all(p["group_absent"] and p["absent"] and p["exit"] is not None
                                for p in processes))
            self.assertTrue(all(p["absent"] and p["exit"] is not None for p in local))
            self.assertTrue(restored)

    def prepare_enabled_host(self):
        self.policy = self.f.project / ".thunderkit/continuity.json"
        # validate_policy allows schema only at the document root, not in backup.
        self.enabled = {"schema": 1, "backup": {"enabled": True}}
        self.assertEqual(self.f.c.validate_policy(self.enabled), self.enabled)
        self.policy.write_text(json.dumps(self.enabled) + "\n")
        self.receipt = self.install()
        self.scheduler = schedule_tests.SchedulerFixture()
        preview = self.api.preview_schedule(self.receipt, self.scheduler,
                                           service_qualified=True, exclusive_writer=True)
        scheduled = self.api.apply_schedule(preview, self.scheduler, approval=True)
        self.assertEqual(scheduled.status, "configured", scheduled.blockers)
        self.schedule_table = self.scheduler.read()
        self.schedule_writes = tuple(self.scheduler.writes)
        self.assertIn(self.receipt.periodic_command.encode(), self.schedule_table)
        code, out, err, _ = self.child([str(self.hook), "--tick"], timeout=65)
        self.assertEqual(code, 0, err)
        value = self.f.c.decode_json(out)
        self.assertEqual(value["status"], "captured", value)
        self.assertEqual(value["push"]["status"], "denied")
        self.previous = Path(value["snapshot"]["snapshot_path"])
        self.previous_manifest = self.f.s.verify_snapshot(self.previous, self.f.identity,
                                                         self.f.settings.limits)
        self.assertEqual(self.previous_manifest.completion, "complete")
        self.previous_tree = lifecycle.tree(self.previous)
        self.previous_head = self.f.git("rev-parse", "HEAD")
        self.assertEqual(self.previous_manifest.head, self.previous_head)
        lifecycle.record({"event": "policy_positive_control", "case": self.id(),
                          "policy_validated": self.enabled, "status": value["status"],
                          "snapshot": str(self.previous), "head": self.previous_head,
                          "snapshot_inventory": inventory(self.previous_tree),
                          "schedule_transport": "simulation only; no scheduler service invoked"})

    def change_policy(self, *, invalid=False):
        changed = {"schema": 1, "backup": {"enabled": "false" if invalid else False}}
        if invalid:
            with self.assertRaises(self.f.c.ContinuityError) as rejected:
                self.f.c.validate_policy(changed)
            self.policy_error = rejected.exception.code
        else:
            self.assertEqual(self.f.c.validate_policy(changed), changed)
            self.assertIs(changed["backup"]["enabled"], False)
            self.policy_error = "backup_disabled"
        self.policy.write_text(json.dumps(changed) + "\n")
        # Ensure there is real new payload to protect, not a duplicate no-op.
        (self.f.project / "notes.md").write_text("# Work after portable policy changed\n")
        lifecycle.record({"event": "policy_changed", "case": self.id(), "document": changed,
                          "valid": not invalid, "expected_refusal": self.policy_error})

    def protected_tree(self, *, manual=False):
        rows = lifecycle.tree(self.f.base)
        if manual:
            # A real commit may alter Git's objects/index/refs/logs, never its
            # installed hook/config or any backup, locator or runtime artifact.
            rows = {name: row for name, row in rows.items()
                    if not name.startswith("project/.git/")
                    or name == "project/.git/config"
                    or name.startswith("project/.git/hooks/")}
        return rows

    def verify_refusal(self, before, outcome, *, manual=False):
        code, out, err, _ = outcome
        after = self.protected_tree(manual=manual)
        ownership_preserved = self.api.load_connections(self.state, project_root=self.f.project) == self.receipt
        previous_verified = self.f.s.verify_snapshot(self.previous, self.f.identity,
                                                     self.f.settings.limits) == self.previous_manifest
        unchanged_previous = lifecycle.tree(self.previous) == self.previous_tree
        head = self.f.git("rev-parse", "HEAD")
        refusal_codes = []
        for line in (out + b"\n" + err).splitlines():
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict):
                error = value.get("error")
                if isinstance(error, dict):
                    refusal_codes.append(error.get("code"))
                refusal_codes.extend([value.get("status"), value.get("reason")])
        if manual:
            commit_exists = self.f.git("cat-file", "-t", head) == "commit"
            commit_content = self.f.git("show", "HEAD:notes.md") == "# Work after portable policy changed"
        else:
            commit_exists = head == self.previous_head
            commit_content = True
        checks = {
            "snapshot_locator_runtime_tree_unchanged": before == after,
            "previous_snapshot_bytes_identity_unchanged": unchanged_previous,
            "previous_snapshot_still_verified": previous_verified,
            "installed_host_ownership_unchanged": ownership_preserved,
            "schedule_table_unchanged": self.scheduler.read() == self.schedule_table,
            "no_additional_schedule_writes": tuple(self.scheduler.writes) == self.schedule_writes,
            "explicit_policy_refusal": self.policy_error in refusal_codes,
            "safe_exit": code == (0 if manual else 2),
            "commit_exists": commit_exists,
            "manual_commit_advanced": not manual or head != self.previous_head,
            "manual_commit_contains_user_work": commit_content,
        }
        changed_paths = sorted(name for name in before.keys() | after.keys()
                               if before.get(name) != after.get(name))
        lifecycle.record({"event": "policy_behavior", "case": self.id(), "checks": checks,
                          "exit": code, "stdout": out.decode(errors="replace"),
                          "stderr": err.decode(errors="replace"), "observed_refusal_codes": refusal_codes,
                          "expected_refusal": self.policy_error, "head_before": self.previous_head,
                          "head_after": head, "changed_paths": changed_paths,
                          "before_inventory": inventory(before), "after_inventory": inventory(after),
                          "schedule_table_hex": self.scheduler.read().hex()})
        self.assertEqual(checks, dict.fromkeys(checks, True),
                         "H25 installed host ignored changed portable policy: "
                         + json.dumps({"failed_checks": [key for key, ok in checks.items() if not ok],
                                       "changed_paths": changed_paths, "exit": code,
                                       "expected_refusal": self.policy_error}, sort_keys=True))

    def test_installed_tick_honors_new_backup_disabled_policy_without_writes(self):
        self.prepare_enabled_host()
        self.change_policy()
        before = self.protected_tree()
        outcome = self.child([str(self.hook), "--tick"], timeout=65)
        self.verify_refusal(before, outcome)

    def test_installed_tick_rejects_invalid_changed_policy_without_cached_capture(self):
        self.prepare_enabled_host()
        self.change_policy(invalid=True)
        before = self.protected_tree()
        outcome = self.child([str(self.hook), "--tick"], timeout=65)
        self.verify_refusal(before, outcome)

    def test_manual_commit_survives_disabled_backup_without_checkpoint_writes(self):
        self.prepare_enabled_host()
        self.change_policy()
        self.f.git("add", "--", "notes.md")
        before = self.protected_tree(manual=True)
        outcome = self.child(["/usr/bin/git", "commit", "-m", "manual work after backup disabled"], timeout=65)
        self.verify_refusal(before, outcome, manual=True)


if __name__ == "__main__":
    unittest.main()
