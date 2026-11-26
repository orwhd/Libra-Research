"""Validate V2 event samples, projection and OpenAPI internal refs.

Requires: pip install -r scripts/requirements-contracts.txt
This does not test an actual backend/SDK/data analysis.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, validate

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / 'contracts'


def assert_internal_refs(doc: dict) -> int:
    refs: list[str] = []

    def visit(obj):
        if isinstance(obj, dict):
            if '$ref' in obj:
                refs.append(obj['$ref'])
            for value in obj.values():
                visit(value)
        elif isinstance(obj, list):
            for value in obj:
                visit(value)

    visit(doc)
    for ref in refs:
        if not ref.startswith('#/'):
            raise AssertionError('Unexpected external OpenAPI ref: ' + ref)
        node = doc
        for segment in ref[2:].split('/'):
            node = node[segment]
    return len(refs)


def main() -> None:
    schema_names = ['event.schema.json', 'canvas-projection.schema.json', 'collaboration-payload.schema.json']
    schemas = {name: json.loads((C / name).read_text(encoding='utf-8')) for name in schema_names}
    for name, value in schemas.items():
        Draft202012Validator.check_schema(value)
        print('[PASS] schema', name)

    seq_seen = set()
    count = 0
    previous = -1
    with (C / 'examples' / 'collaboration_sequence.simulation.jsonl').open(encoding='utf-8') as f:
        for line in f:
            event = json.loads(line)
            validate(event, schemas['event.schema.json'])
            assert event['mode'] == 'simulation', 'Sample events are not LIVE scientific evidence.'
            assert event['seq'] > previous
            assert event['seq'] not in seq_seen
            previous = event['seq']
            seq_seen.add(event['seq'])
            if event['type'].startswith('collaboration.'):
                validate(event, schemas['collaboration-payload.schema.json'])
            count += 1
    print('[PASS] event stream samples', count)

    snapshot = json.loads((C / 'examples' / 'canvas_snapshot.simulation.json').read_text(encoding='utf-8'))
    validate(snapshot, schemas['canvas-projection.schema.json'])
    assert len(snapshot['personas']) == 6
    assert len([p for p in snapshot['personas'] if p['is_main']]) == 1
    print('[PASS] six-person initial roster projection with one Main')

    api = yaml.safe_load((C / 'openapi.yaml').read_text(encoding='utf-8'))
    assert api['openapi'].startswith('3.1')
    old_paths = ['/api/runs', '/api/runs/{run_id}', '/api/runs/{run_id}/chat',
                 '/api/runs/{run_id}/events', '/api/runs/{run_id}/snapshot',
                 '/api/runs/{run_id}/artifacts', '/api/artifacts/{artifact_id}',
                 '/api/runs/{run_id}/report']
    assert all(path in api['paths'] for path in old_paths), 'V1 API path regression'
    assert '/api/runs/{run_id}/messages' in api['paths']
    count_refs = assert_internal_refs(api)
    print('[PASS] OpenAPI 3.1 with all V1 endpoints', count_refs, 'refs checked')
    print('All contract fixtures validated. No live model/science execution was performed.')


if __name__ == '__main__':
    main()
