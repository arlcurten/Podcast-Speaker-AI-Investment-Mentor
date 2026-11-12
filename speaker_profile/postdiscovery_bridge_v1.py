"""Profile projection and bounded source expansion over the shared shortlist bridge.

No model calls or automatic semantic grouping. Commands replay an exact completed
parent and write new private outputs; preparation never grants review credit.
"""
import argparse
import copy
import json
from pathlib import Path

from knowledge import postdiscovery_bridge_v1 as bridge
from knowledge import provenance
from speaker_profile import corpus_inventory as inventory
from speaker_profile import native_synthesis as native
from speaker_profile import native_synthesis_extension as source
from transcription.common import atomic_write_json, private_path, sha256_file

VERSION = 'profile_postdiscovery_bridge.v1'


def project(parent):
    parent.check()
    value, catalog = parent.artifact, parent.catalog
    if value.get('schema_version') != 'profile_family_discovery.v1' or value.get('status') != 'bounded_discovery_complete':
        raise ValueError('Expected completed Profile discovery, not a partial snapshot.')
    if (set(value['evidence_accounting']) != set(catalog['evidence']) or
            set(value['hypothesis_accounting']) != set(catalog['hypotheses'])):
        raise ValueError('Incomplete Profile item accounting.')
    prefix = parent.spec['artifact']['sha256'] + ':'
    families, evidence = {}, {}
    for row in value['families']:
        key = prefix + row['family_id']
        if key in families:
            raise ValueError('Duplicate family identity.')
        families[key] = {'descriptor': copy.deepcopy(row), 'memberships': {}, 'known_obligation_ids': []}
    for rid, row in value['evidence_accounting'].items():
        binding = catalog['evidence'][rid]
        if row['evidence_key'] != binding['evidence_key']:
            raise ValueError('Evidence version mismatch.')
        identity = {'record_id': rid, 'artifact_sha256': binding['artifact']['sha256'],
                    'content_pointer': binding['content_pointer']}
        evidence[rid] = {'identity': identity, 'identity_sha256': provenance.digest(identity),
                         'binding': copy.deepcopy(binding), 'family_keys': []}
        escaped = rid.replace('~', '~0').replace('/', '~1')
        seen_keys = set()
        for i, member in enumerate(row['memberships']):
            key = prefix + member['family_id']
            if key not in families:
                raise ValueError('Unknown membership family.')
            occurrence = {'membership': copy.deepcopy(member),
                          'accounting_pointer': f'/evidence_accounting/{escaped}/memberships/{i}',
                          'artifact': copy.deepcopy(parent.spec['artifact'])}
            families[key]['memberships'].setdefault(rid, []).append(occurrence)
            if key not in seen_keys:
                evidence[rid]['family_keys'].append(key)
                seen_keys.add(key)
    for row in value['hypothesis_accounting'].values():
        if any(prefix + fid not in families for fid in row['family_ids']):
            raise ValueError('Unknown hypothesis navigation family.')
    # One inventory-wide incident pass, not a family-by-hypothesis Cartesian scan.
    contexts = native._incident_context(catalog, {'navigation': set(catalog['evidence'])})
    obligations = {}
    for context in contexts:
        hid = context['prior_hypothesis_key']
        oid = 'prior_hypothesis:' + hid
        required = context['required_evidence_ids']
        obligations[oid] = {'hypothesis_key': hid, 'required_evidence_ids': required,
                            'prior_hypothesis': copy.deepcopy(catalog['hypotheses'][hid]),
                            'state': 'pending_explicit_disposition'}
        targets = {key for rid in required for key in evidence[rid]['family_keys']}
        targets.update(prefix + fid for fid in value['hypothesis_accounting'][hid]['family_ids'])
        for key in sorted(targets):
            families[key]['known_obligation_ids'].append(oid)
    parent.check()
    return {'schema_version': VERSION, 'parent': copy.deepcopy(parent.spec),
            'inventory': copy.deepcopy(value['inventory']), 'families': families, 'evidence': evidence,
            'known_counter_obligations': obligations,
            'evidence_accounting': copy.deepcopy(value['evidence_accounting']),
            'hypothesis_accounting': copy.deepcopy(value['hypothesis_accounting']),
            # Shared shortlist uses this field only to retain navigation-only pending IDs.
            'principle_accounting': copy.deepcopy(value['hypothesis_accounting']),
            'receipts': copy.deepcopy(value['receipts']), 'policy': copy.deepcopy(bridge.POLICY),
            'counter_scope': 'Known native incident closure expands at source selection; global counter review pending.'}


def expand(parent, projected, selection, group_key, *, plan, responses, max_bytes):
    bridge._positive(max_bytes)
    if projected != project(parent):
        raise ValueError('Projection differs from replayed Profile parent.')
    if selection != bridge.shortlist(projected, plan, responses, **selection['limits']):
        raise ValueError('Selection differs from exact authored responses.')
    keys = selection['groups'][group_key]['family_keys']
    selected = sorted({rid for key in keys for rid in projected['families'][key]['memberships']})
    seeds = inventory._basis_closure(parent.catalog['evidence'], selected)
    # Native one-incident closure checks either side and comparison endpoints, not support alone.
    obligations = native._incident_context(parent.catalog, {'selected_group': seeds})
    required = set(seeds)
    for row in obligations:
        required.update(row['required_evidence_ids'])
    required = sorted(inventory._basis_closure(parent.catalog['evidence'], required))
    roles = {key: {rid: [{**copy.deepcopy(row), 'state': 'pending_source_review'} for row in rows]
                   for rid, rows in projected['families'][key]['memberships'].items()} for key in keys}
    observations, sources, raw = source._materialize(parent.catalog, required, parent.pins)
    result = {'schema_version': 'profile_shortlisted_source_workset.v1',
              'parent': copy.deepcopy(parent.spec), 'inventory': projected['inventory'],
              'bridge_sha256': provenance.digest(projected), 'shortlist_sha256': provenance.digest(selection),
              'group_key': group_key, 'family_keys': keys, 'selected_evidence_ids': selected,
              'required_evidence_ids': required, 'role_ledger': roles,
              'family_ledger': copy.deepcopy(selection['family_ledger']),
              'evidence_ledger': copy.deepcopy(selection['case_ledger']),
              'hypothesis_ledger': copy.deepcopy(selection['principle_ledger']),
              'incident_obligations': obligations,
              'known_hypothesis_context': {
                  'prior_hypothesis:' + row['prior_hypothesis_key']:
                  copy.deepcopy(projected['known_counter_obligations']['prior_hypothesis:' + row['prior_hypothesis_key']])
                  for row in obligations},
              'counter_ledger': copy.deepcopy(selection['counter_ledger']),
              'closure_ledger': {rid: 'pending_source_review' for rid in required},
              'counts': {'unique_selected_evidence': len(selected), 'unique_closure_evidence': len(required),
                         'role_occurrences': sum(len(rows) for family in roles.values() for rows in family.values())},
              'evidence_bundle': {'observations': observations, 'sources': sources, 'raw_regions': raw},
              'status': 'prepared_pending_source_review', 'policy': copy.deepcopy(bridge.POLICY)}
    size = len((json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())
    if size > max_bytes:
        result.pop('evidence_bundle')
        result.update(status='pending_oversized', required_bytes=size, max_bytes=max_bytes)
    parent.check()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'shortlist', 'expand'])
    for key in ['bridge-sha256', 'output']:
        parser.add_argument('--' + key, required=True)
    for key in ['parent', 'sha256', 'config', 'config-sha256', 'adapter', 'adapter-sha256',
                'run-version', 'request-version']:
        parser.add_argument('--' + key)
    parser.add_argument('--max-families', type=int, default=32)
    parser.add_argument('--max-input-chars', type=int, default=100000)
    parser.add_argument('--max-group-families', type=int, default=16)
    parser.add_argument('--max-groups-per-page', type=int, default=32)
    parser.add_argument('--max-workset-bytes', type=int, default=20000000)
    parser.add_argument('--responses')
    parser.add_argument('--responses-sha256')
    parser.add_argument('--group-key')
    args = parser.parse_args(argv)
    output = private_path(args.output)
    if output.exists():
        raise FileExistsError('Use a new private output version.')
    bridge_file = Path(bridge.__file__)
    implementation = sha256_file(Path(__file__))
    if sha256_file(bridge_file) != args.bridge_sha256:
        raise ValueError('Shared bridge implementation pin mismatch.')
    pins = {}
    completion = (args.config, args.config_sha256, args.adapter, args.adapter_sha256,
                  args.run_version, args.request_version)
    if args.parent:
        if not args.sha256 or any(completion):
            parser.error('Use parent/hash OR explicit completion-hook config and adapter pins.')
        spec = bridge.inventory._read({'path': args.parent, 'sha256': args.sha256}, pins)
        parent = bridge.replay_completed(spec)
        parent.pins.update(pins)
    else:
        if args.sha256 or not all(completion):
            parser.error('Completion hook requires config/hash, adapter/hash and exact run/request versions.')
        parent = bridge.capture_parent({'path': args.config, 'sha256': args.config_sha256},
                                      adapter=args.adapter, adapter_sha256=args.adapter_sha256,
                                      run_version=args.run_version, request_version=args.request_version)
    projected = project(parent)
    plan = bridge.plan_pages(projected, max_families=args.max_families, max_input_chars=args.max_input_chars)
    responses = {}
    if args.command != 'plan':
        if not args.responses or not args.responses_sha256:
            parser.error('shortlist/expand require hash-bound responses')
        responses = bridge.inventory._read({'path': args.responses, 'sha256': args.responses_sha256}, parent.pins)
    selection = bridge.shortlist(projected, plan, responses, max_group_families=args.max_group_families,
                                 max_groups_per_page=args.max_groups_per_page)
    result = {'bridge': projected, 'navigation_plan': plan, 'shortlist': selection,
              'implementation_sha256': implementation, 'shared_bridge_sha256': args.bridge_sha256}
    if args.command == 'expand':
        if not args.group_key:
            parser.error('expand requires --group-key')
        result['workset'] = expand(parent, projected, selection, args.group_key, plan=plan,
                                   responses=responses, max_bytes=args.max_workset_bytes)
    if sha256_file(bridge_file) != args.bridge_sha256 or sha256_file(Path(__file__)) != implementation:
        raise ValueError('Implementation changed during execution.')
    protected = [parent.root, *map(Path, parent.catalog['protected_output_roots'])]
    if output in parent.pins or any(output.is_relative_to(path.resolve()) for path in protected):
        raise ValueError('Output overlaps protected inputs.')
    parent.check()
    atomic_write_json(output, result, overwrite=False)
    print(json.dumps({'path': str(output), 'sha256': sha256_file(output),
                      'pages': len(plan['pages']), 'family_count': len(selection['family_ledger']),
                      'evidence_count': len(selection['case_ledger'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
