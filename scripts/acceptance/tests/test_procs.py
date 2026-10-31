"""Which processes cleanup may signal, from a synthetic process table (nothing here signals anything)."""

import os
import unittest

import _path  # noqa: F401
from acceptance.procs import Proc, group_members

LEADER_STARTED = "Sat Oct 24 15:51:09 2026"
LATER = "Sat Oct 24 15:51:12 2026"
EARLIER = "Sat Oct 24 15:50:00 2026"
STRANGER_STARTED = "Sat Oct 24 17:00:00 2026"


def proc(pid, pgid, started, ppid=1):
    return Proc(pid, ppid, pgid, started, "cmd")


class GroupMembersTests(unittest.TestCase):
    def test_a_leader_that_still_holds_its_pid_keeps_its_members_and_their_descendants(self):
        table = [proc(100, 100, LEADER_STARTED), proc(101, 100, LATER, ppid=100), proc(102, 102, LATER, ppid=101),
                 proc(900, 900, EARLIER)]
        self.assertEqual(group_members(table, {100: LEADER_STARTED}), [100, 101, 102])

    def test_a_reused_leader_pid_excludes_every_member_of_the_group(self):
        # pid 100 now belongs to a stranger that leads a group of its own, with a child and a group-mate.
        table = [proc(100, 100, STRANGER_STARTED), proc(101, 100, STRANGER_STARTED, ppid=100),
                 proc(102, 100, STRANGER_STARTED), proc(103, 103, STRANGER_STARTED, ppid=102)]
        self.assertEqual(group_members(table, {100: LEADER_STARTED}), [])

    def test_a_reused_leader_pid_that_leads_no_group_excludes_the_group_too(self):
        table = [proc(100, 55, LEADER_STARTED), proc(101, 100, LATER)]
        self.assertEqual(group_members(table, {100: LEADER_STARTED}), [])

    def test_a_reused_group_does_not_hide_another_that_is_ours(self):
        table = [proc(100, 100, STRANGER_STARTED), proc(101, 100, STRANGER_STARTED),
                 proc(200, 200, LEADER_STARTED), proc(201, 200, LATER)]
        found = group_members(table, {100: LEADER_STARTED, 200: LEADER_STARTED})
        self.assertEqual(found, [200, 201])

    def test_after_the_leader_exits_the_members_that_began_since_are_kept(self):
        table = [proc(101, 100, LATER), proc(102, 100, LEADER_STARTED), proc(103, 103, LATER, ppid=101)]
        self.assertEqual(group_members(table, {100: LEADER_STARTED}), [101, 102, 103])

    def test_after_the_leader_exits_a_member_that_began_before_the_group_is_left_alone(self):
        table = [proc(101, 100, EARLIER), proc(102, 100, LATER), proc(103, 103, EARLIER, ppid=101)]
        self.assertEqual(group_members(table, {100: LEADER_STARTED}), [102])

    def test_a_start_time_that_cannot_be_compared_proves_nothing(self):
        table = [proc(101, 100, LATER), proc(102, 100, "not a date")]
        self.assertEqual(group_members(table, {100: ""}), [])
        self.assertEqual(group_members(table, {100: LEADER_STARTED}), [101])

    def test_a_live_child_is_kept_without_a_group_and_the_caller_is_never_returned(self):
        table = [proc(300, 300, LATER), proc(301, 300, LATER, ppid=300), proc(os.getpid(), 300, LATER, ppid=300)]
        self.assertEqual(group_members(table, {}, [300]), [300, 301])


if __name__ == "__main__":
    unittest.main()
