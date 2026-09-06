"""test_plan.py — Plan/PlanStep 状态机与注入的 L1 单测（Issue #19）。"""

import unittest

from coding_agent.plan import Plan, PlanStep


class PlanStepTest(unittest.TestCase):
    def test_mark_valid_transitions(self):
        s = PlanStep(id=1, description="x")
        s.mark("done")
        self.assertEqual(s.status, "done")
        s.mark("pending")  # 允许回退（修订语义）
        self.assertEqual(s.status, "pending")

    def test_mark_invalid_status_raises(self):
        s = PlanStep(id=1, description="x")
        with self.assertRaises(ValueError):
            s.mark("finished")


class PlanTest(unittest.TestCase):
    def _plan(self):
        return Plan.from_descriptions(["探索目录", "写脚本", "跑测试"])

    def test_from_descriptions_ids_start_at_1(self):
        p = self._plan()
        self.assertEqual([s.id for s in p.steps], [1, 2, 3])
        self.assertTrue(all(s.status == "pending" for s in p.steps))

    def test_mark_by_id(self):
        p = self._plan()
        p.mark_step(2, "done")
        self.assertEqual(p.steps[1].status, "done")
        self.assertEqual(p.progress, "1/3 done")

    def test_mark_by_description_substring(self):
        p = self._plan()
        hit = p.mark_by_description("脚本", "done")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.id, 2)

    def test_mark_by_description_miss_returns_none(self):
        self.assertIsNone(self._plan().mark_by_description("不存在的步骤", "done"))

    def test_mark_nonexistent_id_raises(self):
        with self.assertRaises(KeyError):
            self._plan().mark_step(99, "done")

    def test_pending_filter(self):
        p = self._plan()
        p.mark_step(1, "done")
        p.mark_step(2, "blocked")
        self.assertEqual([s.id for s in p.pending_steps], [3])

    def test_render_contains_status_and_progress(self):
        p = self._plan()
        p.mark_step(1, "done")
        text = p.render()
        self.assertIn("1/3 done", text)
        self.assertIn("[x] #1 探索目录", text)
        self.assertIn("[ ] #2 写脚本", text)

    def test_render_only_pending(self):
        p = self._plan()
        p.mark_step(1, "done")
        text = p.render(only_pending=True)
        self.assertNotIn("#1", text)
        self.assertIn("#2", text)

    def test_blocked_marker(self):
        p = self._plan()
        p.mark_step(2, "blocked")
        self.assertIn("[!] #2", p.render())
        self.assertIn("1 blocked", p.progress)

    def test_revise_increments_revision(self):
        p = self._plan()
        p.mark_step(1, "done")
        p.revise(["新方向 A", "新方向 B"])
        self.assertEqual(p.revision, 1)
        self.assertEqual(len(p.steps), 2)
        self.assertTrue(all(s.status == "pending" for s in p.steps))  # 修订后全部重置

    def test_roundtrip_serialization(self):
        p = self._plan()
        p.mark_step(1, "done")
        p.revise(["A", "B", "C"])
        p.mark_step(2, "blocked")
        restored = Plan.from_dict(p.to_dict())
        self.assertEqual(restored.revision, p.revision)
        self.assertEqual([s.status for s in restored.steps], [s.status for s in p.steps])
        self.assertEqual([s.description for s in restored.steps], [s.description for s in p.steps])


if __name__ == "__main__":
    unittest.main()
