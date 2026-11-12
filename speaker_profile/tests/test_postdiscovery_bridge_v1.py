import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from speaker_profile import postdiscovery_bridge_v1 as adapter


def fixture():
    evidence = {rid: {'evidence_key': rid + ':version1', 'artifact': {'path': '/synthetic/evidence', 'sha256': 'b'*64},
                     'content_pointer': '/content/items/' + str(i), 'basis_evidence_ids': []}
                for i, rid in enumerate(['EP1:E1', 'EP1:E2', 'EP1:E3'])}
    accounting = {rid: {'evidence_key': row['evidence_key'], 'memberships': [],
                        'packet_reasons': [{'reason': 'synthetic retained'}]} for rid, row in evidence.items()}
    accounting['EP1:E1']['memberships'] = [
        {'family_id': 'p:F1', 'role': 'potential_support', 'reason': 'conditional support'},
        {'family_id': 'p:F1', 'role': 'potential_counter', 'reason': 'contrary condition'}]
    accounting['EP1:E2']['memberships'] = [
        {'family_id': 'p:F2', 'role': 'uncertain', 'reason': 'rare unresolved'}]
    hypotheses = {'H1': {'supporting_evidence': ['EP1:E3'], 'counterevidence': ['EP1:E1'],
                          'statement_action_relationships': [], 'recovered_comparisons': []}}
    value = {'schema_version': 'profile_family_discovery.v1', 'status': 'bounded_discovery_complete',
             'inventory': {'path': '/synthetic/inventory', 'sha256': 'c'*64},
             'families': [{'family_id': 'p:F1', 'conditions': ['conditional']}, {'family_id': 'p:F2'}],
             'evidence_accounting': accounting, 'hypothesis_accounting': {'H1': {'family_ids': []}}, 'receipts': []}
    return SimpleNamespace(check=lambda: None, artifact=value,
                           catalog={'evidence': evidence, 'hypotheses': hypotheses}, pins={},
                           spec={'artifact': {'path': '/synthetic/discovery', 'sha256': 'a'*64}})


def routing(projected):
    plan = adapter.bridge.plan_pages(projected, max_families=1, max_input_chars=100000)
    page_id, page = next(iter(plan['pages'].items()))
    key = page['context']['families'][0]['family_key']
    responses = {page_id: {'page_sha256': page_id, 'author': 'synthetic test', 'method': 'semantic test fixture',
                           'response': {'family_dispositions': [{'family_key': key, 'status': 'assigned',
                                                                 'group_ids': ['G1'], 'reason': 'test'}],
                                        'groups': [{'group_id': 'G1', 'family_keys': [key],
                                                    'relationship': 'counter_check', 'reason': 'both roles',
                                                    'conditions': [], 'limitations': ['not reviewed']}]}}}
    selection = adapter.bridge.shortlist(projected, plan, responses, max_group_families=2, max_groups_per_page=2)
    return plan, responses, selection, next(iter(selection['groups']))


class Tests(unittest.TestCase):
    def test_cli_plan_private_new_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / 'parent.json'
            spec.write_text('{}', encoding='utf-8')
            output = root / 'plan.json'
            parent = fixture()
            parent.root = root / 'discovery'
            parent.catalog['protected_output_roots'] = [str(root / 'sources')]
            args = ['plan', '--parent', str(spec), '--sha256', adapter.sha256_file(spec),
                    '--bridge-sha256', adapter.sha256_file(Path(adapter.bridge.__file__)), '--output', str(output)]
            with patch.object(adapter.bridge, 'replay_completed', return_value=parent) as replay:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(adapter.main(args), 0)
                replay.assert_called_once()
                stored = json.loads(output.read_text())
                self.assertEqual(len(stored['shortlist']['case_ledger']), 3)
                self.assertFalse(stored['shortlist']['policy']['publication_authorized'])
                with self.assertRaises(FileExistsError):
                    adapter.main(args)

    def test_cli_rejects_public_output_before_replay(self):
        with patch.object(adapter.bridge, 'replay_completed') as replay:
            with self.assertRaises(ValueError):
                adapter.main(['plan', '--parent', '/synthetic/parent', '--sha256', 'a'*64,
                              '--bridge-sha256', 'b'*64, '--output', str(Path(adapter.__file__).parent / 'output.json')])
            replay.assert_not_called()

    def test_cli_rejects_shared_bridge_hash_before_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(adapter.bridge, 'replay_completed') as replay:
                with self.assertRaises(ValueError):
                    adapter.main(['plan', '--parent', '/synthetic/parent', '--sha256', 'a'*64,
                                  '--bridge-sha256', '0'*64, '--output', str(Path(directory) / 'out.json')])
                replay.assert_not_called()

    def test_cli_rejects_protected_source_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / 'parent.json'
            spec.write_text('{}', encoding='utf-8')
            parent = fixture()
            parent.root = root / 'discovery'
            parent.catalog['protected_output_roots'] = [str(root / 'sources')]
            with patch.object(adapter.bridge, 'replay_completed', return_value=parent):
                with self.assertRaises(ValueError):
                    adapter.main(['plan', '--parent', str(spec), '--sha256', adapter.sha256_file(spec),
                                  '--bridge-sha256', adapter.sha256_file(Path(adapter.bridge.__file__)),
                                  '--output', str(root / 'sources' / 'out.json')])
            self.assertFalse((root / 'sources' / 'out.json').exists())

    def test_reference_occurrence_preserves_reason_without_count_inflation(self):
        parent = fixture()
        row = copy.deepcopy(parent.artifact['evidence_accounting']['EP1:E1']['memberships'][0])
        row['reason'] = 'reference-only occurrence'
        parent.artifact['evidence_accounting']['EP1:E1']['memberships'].append(row)
        projected = adapter.project(parent)
        family = next(iter(projected['families'].values()))
        self.assertEqual(len(family['memberships']), 1)
        self.assertEqual(len(family['memberships']['EP1:E1']), 3)
        self.assertEqual(len(projected['evidence']['EP1:E1']['family_keys']), 1)

    def test_real_materializer_reads_episode_and_raw_once_preserves_supplement(self):
        p = fixture()
        evref = {'path': '/synthetic/evidence', 'sha256': 'b'*64}
        rawref = {'path': '/synthetic/raw', 'sha256': 'e'*64}
        p.catalog['episodes'] = {'EP1': {'evidence': {'input': {'kind': 'native', 'artifact': evref}},
                                        'authority': 'candidate', 'original_manifest': None,
                                        'native_lineage': {'source_files': {'raw': rawref}},
                                        'source_hashes': {'raw_sha256': 'e'*64}}}
        for row in p.catalog['evidence'].values():
            row['episode_id'] = 'EP1'
        grounding = {'source_segment_ids': [1], 'source_segment_id_ranges': [],
                     'supplementary_context': [{'grounding': {'source_segment_ids': [2], 'source_segment_id_ranges': []}}]}
        artifact = {'content': {'items': [{'evidence_id': rid} for rid in p.catalog['evidence']], 'unselected_regions': []},
                    'grounding': {'items': {rid: grounding for rid in p.catalog['evidence']}, 'unselected_regions': []},
                    'source': {}, 'policy': {}, 'authorship': {}, 'model_metadata': {}, 'adoption': {}}
        raw = {'segments': [{'segment_id': i, 'start': i, 'end': i+1, 'text': str(i)} for i in [1, 2]]}
        calls = []
        def read(ref, pins):
            calls.append(ref['path'])
            return artifact if ref == evref else raw
        projected = adapter.project(p)
        plan, responses, selected, group = routing(projected)
        with patch.object(adapter.source.native, '_read', side_effect=read):
            work = adapter.expand(p, projected, selected, group, plan=plan, responses=responses, max_bytes=100000)
        self.assertEqual(calls, ['/synthetic/evidence', '/synthetic/raw'])
        self.assertEqual(set(work['evidence_bundle']['observations']), {'EP1:E1', 'EP1:E3'})
        self.assertEqual(len(work['evidence_bundle']['raw_regions']['EP1']), 2)

    def test_dual_roles_and_source_once_with_counter_incident(self):
        parent = fixture()
        projected = adapter.project(parent)
        plan, responses, selected, group = routing(projected)
        with patch.object(adapter.source, '_materialize', return_value=({}, {}, {})) as load:
            work = adapter.expand(parent, projected, selected, group, plan=plan, responses=responses, max_bytes=100000)
        load.assert_called_once_with(parent.catalog, ['EP1:E1', 'EP1:E3'], parent.pins)
        self.assertEqual(work['counts'], {'unique_selected_evidence': 1, 'unique_closure_evidence': 2, 'role_occurrences': 2})
        roles = next(iter(work['role_ledger'].values()))['EP1:E1']
        self.assertEqual({r['membership']['role'] for r in roles}, {'potential_support', 'potential_counter'})
        self.assertEqual(len(work['incident_obligations']), 1)
        self.assertEqual(len(work['family_ledger']), 2)
        self.assertEqual(len(work['evidence_ledger']), 3)
        self.assertEqual(work['evidence_ledger']['EP1:E3']['state'], 'pending_ungrouped_review')
        self.assertIn('pending_unread', {v['state'] for v in work['family_ledger'].values()})

    def test_reverse_roles_preserves_counts_reasons(self):
        a = fixture()
        b = copy.deepcopy(a)
        b.artifact['evidence_accounting']['EP1:E1']['memberships'].reverse()
        def rows(p):
            result = adapter.project(p)
            family = next(iter(result['families'].values()))
            return sorted((r['membership']['role'], r['membership']['reason']) for r in family['memberships']['EP1:E1'])
        self.assertEqual(rows(a), rows(b))

    def test_versions_are_distinct(self):
        a, b = fixture(), fixture()
        b.catalog['evidence']['EP1:E1']['artifact']['sha256'] = 'd'*64
        self.assertNotEqual(adapter.project(a)['evidence']['EP1:E1']['identity_sha256'],
                            adapter.project(b)['evidence']['EP1:E1']['identity_sha256'])

    def test_unknown_family_rejected(self):
        p = fixture()
        p.artifact['evidence_accounting']['EP1:E1']['memberships'][0]['family_id'] = 'unknown'
        with self.assertRaises(ValueError):
            adapter.project(p)

    def test_missing_item_rejected(self):
        p = fixture()
        del p.artifact['evidence_accounting']['EP1:E3']
        with self.assertRaises(ValueError):
            adapter.project(p)

    def test_partial_parent_rejected(self):
        p = fixture()
        p.artifact['status'] = 'in_progress'
        with self.assertRaises(ValueError):
            adapter.project(p)

    def test_oversize_keeps_all_pending_ledgers(self):
        p = fixture()
        projected = adapter.project(p)
        plan, responses, selected, group = routing(projected)
        with patch.object(adapter.source, '_materialize', return_value=({}, {}, {})):
            work = adapter.expand(p, projected, selected, group, plan=plan, responses=responses, max_bytes=1)
        self.assertEqual(work['status'], 'pending_oversized')
        self.assertNotIn('evidence_bundle', work)
        self.assertEqual(len(work['evidence_ledger']), 3)
        self.assertEqual(len(work['closure_ledger']), 2)

    def test_tampered_shortlist_rejected(self):
        p = fixture()
        projected = adapter.project(p)
        plan, responses, selected, group = routing(projected)
        selected['family_ledger'].clear()
        with self.assertRaises(ValueError):
            adapter.expand(p, projected, selected, group, plan=plan, responses=responses, max_bytes=100000)

    def test_no_responses_all_families_pending(self):
        projected = adapter.project(fixture())
        plan = adapter.bridge.plan_pages(projected, max_families=1, max_input_chars=1)
        result = adapter.bridge.shortlist(projected, plan, {}, max_group_families=1, max_groups_per_page=1)
        self.assertEqual(len(result['family_ledger']), 2)
        self.assertEqual({r['state'] for r in result['family_ledger'].values()}, {'pending_oversized'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
