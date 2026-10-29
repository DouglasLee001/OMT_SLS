#!/usr/bin/env python3
"""Mock-only tests for the repeated-baseline timing diagnostic."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'experiment/repeat_profiles.py'


class RepeatTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'missing repeated-baseline diagnostic')
        spec = importlib.util.spec_from_file_location('repeat_profiles_test', SCRIPT)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)

    def test_repeats_same_frozen_executables_and_exact_requested_cases(self):
        parent = dict(global_indices=[5, 12, 19], cases=[dict(source=str(i)) for i in [5, 12, 19]],
                      inputs={str(i): dict(global_index=i) for i in [5, 12, 19]},
                      benchmark_hashes={str(i): str(i) for i in [5, 12, 19]},
                      variants=dict(baseline=dict(executable='base', executable_sha256='B'),
                                    candidate17=dict(executable='candidate', executable_sha256='C')),
                      config=dict(jobs=26, timeout=1200, use_target=False))
        plan = self.m.repeat_plan(parent, [12, 5], 2)
        self.assertEqual(plan['global_indices'], [12, 5])
        self.assertEqual(list(plan['variants']), ['baseline', 'baseline_repeat2', 'candidate17', 'candidate17_repeat2'])
        self.assertEqual(plan['variants']['baseline'], plan['variants']['baseline_repeat2'])
        self.assertEqual(plan['variants']['candidate17'], plan['variants']['candidate17_repeat2'])
        self.assertEqual(plan['config']['jobs'], 2)
        self.assertEqual(plan['maximum_case_concurrency'], 8)
        self.assertEqual(parent['config']['jobs'], 26)
        for indices in [[5, 5], [999]]:
            with self.assertRaises(ValueError): self.m.repeat_plan(parent, indices, 2)

    def test_affinity_chooses_distinct_physical_cores_and_records_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for cpu, core in [(0, 0), (1, 0), (2, 1), (3, 2)]:
                path = root / f'cpu{cpu}' / 'topology'; path.mkdir(parents=True)
                (path / 'physical_package_id').write_text('0')
                (path / 'core_id').write_text(str(core))
            cpus, physical = self.m.distinct_cpus({0, 1, 2, 3}, 3, root)
            self.assertEqual(cpus, [0, 2, 3]); self.assertTrue(physical)
            cpus, physical = self.m.distinct_cpus({0, 1, 2, 3}, 4, root)
            self.assertEqual(cpus, [0, 1, 2, 3]); self.assertFalse(physical)
            with self.assertRaises(ValueError): self.m.distinct_cpus({0, 1}, 3, root)

    def test_affinity_stage_restores_worker_thread_mask_even_on_failure(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); native = root / 'input.nia'; selected = {str(native): 3}
            masks = []
            def fail(*args, **kwargs): raise RuntimeError('native failed')
            stage = self.m.pinned_stage(fail, selected)
            with patch.object(self.m.os, 'sched_getaffinity', side_effect=[{2, 3, 4}, {3}]), \
                 patch.object(self.m.os, 'sched_setaffinity', side_effect=lambda pid, mask: masks.append(set(mask))):
                with self.assertRaisesRegex(RuntimeError, 'native failed'):
                    stage(['exe', str(native), '--optimize'], time.monotonic()+5, threading.Event(), root/'out', root/'err')
            self.assertEqual(masks, [{3}, {2, 3, 4}])
            self.assertTrue((root / 'native-affinity.json').is_file())

    def test_affinity_stage_rejects_cpu_outside_current_allowed_mask(self):
        with patch.object(self.m.os, 'sched_getaffinity', return_value={2}), \
             patch.object(self.m.os, 'sched_setaffinity') as set_mask:
            stage = self.m.pinned_stage(lambda *a: self.fail('must not launch'), {'input.nia': 3})
            with self.assertRaises(ValueError): stage(['exe', 'input.nia', '--optimize'], 1, None, 'out', 'err')
            set_mask.assert_not_called()

    def test_allocation_cpu_ids_are_for_exact_current_node_and_80_cpus(self):
        text = 'JobId=9\n  Nodes=node2 CPU_IDs=0-39,96-135 Mem=0 GRES=\n'
        self.assertEqual(self.m.allocation_cpus(text, 'node2'), set(range(40)) | set(range(96, 136)))
        for invalid in ['Nodes=node3 CPU_IDs=0-79', 'Nodes=node2 CPU_IDs=0-78',
                        'Nodes=node2 CPU_IDs=0-79\nNodes=node2 CPU_IDs=80-159', 'Nodes=node2 CPU_IDs=bad']:
            with self.assertRaises(ValueError): self.m.allocation_cpus(invalid, 'node2')


if __name__ == '__main__': unittest.main()
