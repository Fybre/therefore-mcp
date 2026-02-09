#!/usr/bin/env python3
import base64
import json
import re
import ssl
import urllib.request
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / 'docs' / 'reference' / 'user' / '.env.local'
REPORT_PATH = ROOT / 'docs' / 'notes' / 'validation_report.md'
NOTES_DIR = ROOT / 'docs' / 'notes'

CTX = ssl.create_default_context()
TIMEOUT = 20


def load_env(path: Path) -> dict:
    data = {}
    if not path.exists():
        raise FileNotFoundError(f'env file not found: {path}')
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        data[k.strip()] = v.strip()
    return data


def normalize_env(env: dict) -> dict:
    def clean(v):
        if v is None:
            return None
        v = v.strip()
        if not v or v.upper().startswith('THIS IS NOT NEEDED'):
            return None
        return v

    out = {
        'base_url': clean(env.get('THEREFORE_BASE_URL')),
        'auth_method': clean(env.get('THEREFORE_AUTH_METHOD')),
        'username': clean(env.get('THEREFORE_USERNAME')),
        'password': clean(env.get('THEREFORE_PASSWORD')),
        'tenant_name': clean(env.get('THEREFORE_TENANTNAME')),
        'safe_doc_id': clean(env.get('THEREFORE_SAFE_DOC_ID')),
        'safe_category_id': clean(env.get('THEREFORE_SAFE_CATEGORY_ID')),
        'allow_writes': clean(env.get('THEREFORE_ALLOW_WRITES')),
        'tenant_key': clean(env.get('THEREFORE_TENANT_KEY')),
    }
    return out


def post_json(url: str, payload: dict, headers: dict) -> tuple[int, dict | None, str | None, float]:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json; charset=utf-8')
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        start = time.monotonic()
        with urllib.request.urlopen(req, context=CTX, timeout=TIMEOUT) as r:
            body = r.read().decode('utf-8', errors='replace')
            content_type = r.headers.get('Content-Type', '')
            if 'application/json' in content_type or body.strip().startswith('{'):
                try:
                    return r.status, json.loads(body), None, time.monotonic() - start
                except Exception:
                    return r.status, None, body, time.monotonic() - start
            return r.status, None, body, time.monotonic() - start
    except urllib.error.HTTPError as e:
        elapsed = 0.0
        try:
            elapsed = time.monotonic() - start
        except Exception:
            elapsed = 0.0
        body = e.read().decode('utf-8', errors='replace') if hasattr(e, 'read') else ''
        return e.code, None, body, elapsed


def build_auth_headers(env: dict) -> dict:
    auth_method = (env.get('auth_method') or '').lower()
    headers = {}
    if auth_method == 'basic':
        if not env.get('username') or not env.get('password'):
            raise ValueError('Basic auth requires username/password')
        token = base64.b64encode(f"{env['username']}:{env['password']}".encode('utf-8')).decode('ascii')
        headers['Authorization'] = f'Basic {token}'
    if env.get('tenant_name'):
        headers['TenantName'] = env['tenant_name']
    return headers


def redact(value: str, max_len: int = 8) -> str:
    if not value:
        return ''
    if len(value) <= max_len:
        return '*' * len(value)
    return value[:2] + '*' * (len(value) - 4) + value[-2:]


def main():
    env_raw = load_env(ENV_PATH)

    tenants_raw = (env_raw.get('THEREFORE_TENANTS') or '').strip()
    if tenants_raw:
        tenants = [t.strip() for t in tenants_raw.split(',') if t.strip()]
        default_name = (env_raw.get('THEREFORE_DEFAULT_TENANT') or (tenants[0] if tenants else '')).strip()
        if default_name not in tenants and tenants:
            default_name = tenants[0]

        prefix = f"THEREFORE_{default_name.upper()}_"
        def pick(suffix: str):
            return env_raw.get(prefix + suffix) or env_raw.get('THEREFORE_' + suffix)

        env = normalize_env({
            'THEREFORE_BASE_URL': pick('BASE_URL'),
            'THEREFORE_AUTH_METHOD': pick('AUTH_METHOD'),
            'THEREFORE_USERNAME': pick('USERNAME'),
            'THEREFORE_PASSWORD': pick('PASSWORD'),
            'THEREFORE_TENANTNAME': pick('TENANTNAME'),
            'THEREFORE_SAFE_DOC_ID': pick('SAFE_DOC_ID'),
            'THEREFORE_SAFE_CATEGORY_ID': pick('SAFE_CATEGORY_ID'),
            'THEREFORE_ALLOW_WRITES': pick('ALLOW_WRITES'),
            'THEREFORE_TENANT_KEY': default_name,
        })
    else:
        env = normalize_env(env_raw)

    if not env.get('base_url'):
        raise ValueError('Missing THEREFORE_BASE_URL')

    headers = build_auth_headers(env)
    base_url = env['base_url'].rstrip('/')

    results = []
    extra_outputs = {}
    allow_writes = str(env.get('allow_writes') or '').lower() in ('true', '1', 'yes')

    def run_test(name: str, path: str, payload: dict, skip_reason: str | None = None):
        if skip_reason:
            results.append({
                'operation': name,
                'url': f'{base_url}/{path}',
                'status': 'SKIPPED',
                'skip_reason': skip_reason,
            })
            return
        url = f'{base_url}/{path}'
        status, body, raw, elapsed = post_json(url, payload, headers)
        snippet = None
        if raw:
            snippet = raw.strip().replace('\\n', ' ')[:300]
        results.append({
            'operation': name,
            'url': url,
            'status': status,
            'elapsed_ms': int(elapsed * 1000),
            'json_keys': list(body.keys()) if isinstance(body, dict) else None,
            'has_body': bool(body or raw),
            'raw_snippet': snippet,
        })

    # Basic capability tests (read-only)
    run_test('GetWebAPIServerVersion', 'GetWebAPIServerVersion', {})
    run_test('GetConnectionToken', 'GetConnectionToken', {})
    run_test('GetDomainInfo', 'GetDomainInfo', {})
    run_test('GetClientDiscoveryInfo', 'GetClientDiscoveryInfo', {})
    run_test('GetConnectedUser', 'GetConnectedUser', {'Create': False})
    run_test('GetPermissionConstants', 'GetPermissionConstants', {})
    run_test('GetRolePermissionConstants', 'GetRolePermissionConstants', {})

    # Categories tree
    categories_tree_payload = {}
    status, body, raw, elapsed = post_json(f"{base_url}/GetCategoriesTree", categories_tree_payload, headers)
    results.append({
        'operation': 'GetCategoriesTree',
        'url': f"{base_url}/GetCategoriesTree",
        'status': status,
        'elapsed_ms': int(elapsed * 1000),
        'json_keys': list(body.keys()) if isinstance(body, dict) else None,
        'has_body': bool(body or raw),
        'raw_snippet': (raw.strip().replace('\\n', ' ')[:300] if raw else None),
    })
    if isinstance(body, dict) and 'TreeItems' in body:
        def flatten_tree(items, parent_path=''):
            out = []
            for item in items:
                name = item.get('Name') or ''
                path = f"{parent_path}/{name}" if parent_path else name
                out.append({
                    'name': name,
                    'path': path,
                    'item_no': item.get('ItemNo'),
                    'item_type': item.get('ItemType'),
                    'folder_type': item.get('FolderType'),
                    'parent_case_def_no': item.get('ParentCaseDefNo'),
                    'parent_folder_no': item.get('ParentFolderNo'),
                    'guid': item.get('Guid'),
                })
                children = item.get('ChildItems') or []
                if children:
                    out.extend(flatten_tree(children, path))
            return out

        flat = flatten_tree(body.get('TreeItems') or [])
        extra_outputs['categories_index'] = flat

    # Category-based tests
    if env.get('safe_category_id'):
        status, body, raw, elapsed = post_json(
            f"{base_url}/GetCategoryInfo",
            {
                'CategoryNo': int(env['safe_category_id']),
                'IsAccessMaskNeeded': True,
                'IsSearchFieldOrderNeeded': True,
            },
            headers,
        )
        results.append({
            'operation': 'GetCategoryInfo',
            'url': f"{base_url}/GetCategoryInfo",
            'status': status,
            'elapsed_ms': int(elapsed * 1000),
            'json_keys': list(body.keys()) if isinstance(body, dict) else None,
            'has_body': bool(body or raw),
            'raw_snippet': (raw.strip().replace('\\n', ' ')[:300] if raw else None),
        })
        if isinstance(body, dict):
            fields = body.get('CategoryFields') or body.get('Fields') or []
            extra_outputs['category_fields'] = [{
                'field_no': f.get('FieldNo'),
                'field_id': f.get('FieldID'),
                'caption': f.get('Caption'),
                'index_name': f.get('IndexDataFieldName'),
                'type_no': f.get('TypeNo'),
                'field_type': f.get('FieldType'),
                'mandatory': f.get('Mandatory'),
                'visible': f.get('Visible'),
                'is_multi_keyword': f.get('IsMultipleKeyword'),
                'is_single_keyword': f.get('IsSingleKeyword'),
                'is_date_time': f.get('IsDateTimeFieldType'),
                'is_auto_append': f.get('IsAutoAppendField'),
                'default_val': f.get('DefaultVal'),
                'counter_mode': f.get('CounterMode'),
            } for f in fields]
    else:
        run_test('GetCategoryInfo', 'GetCategoryInfo', {}, 'Missing THEREFORE_SAFE_CATEGORY_ID')

    # Objects tests (category type = 3)
    status, body, raw, elapsed = post_json(
        f"{base_url}/GetObjectsList",
        {'LoadItemsList': [{'Flags': 0, 'Type': 3, 'RoleAccessMask': 18446744073709551615}]},
        headers,
    )
    results.append({
        'operation': 'GetObjectsList(Type=3)',
        'url': f"{base_url}/GetObjectsList",
        'status': status,
        'elapsed_ms': int(elapsed * 1000),
        'json_keys': list(body.keys()) if isinstance(body, dict) else None,
        'has_body': bool(body or raw),
        'raw_snippet': (raw.strip().replace('\\n', ' ')[:300] if raw else None),
    })
    if isinstance(body, dict) and body.get('AllItemsList'):
        extra_outputs['objects_list_type_3'] = body.get('AllItemsList')

    # Document-based tests
    if env.get('safe_doc_id'):
        run_test('GetDocument', 'GetDocument', {
            'DocNo': int(env['safe_doc_id']),
            'IsCheckOutStatusNeeded': False,
            'IsIndexDataValuesNeeded': True,
            'IsStreamsInfoAndDataNeeded': False,
            'IsStreamsInfoNeeded': False,
            'IsAccessMaskNeeded': False,
            'TitleHideCategory': False,
            'IsStreamDataBase64JSONNeeded': False,
            'TitleType': 0,
            'RetrieveReason': '',
        })
        run_test('GetDocumentIndexData', 'GetDocumentIndexData', {
            'DocNo': int(env['safe_doc_id']),
            'IsAccessMaskNeeded': False,
            'TitleHideCategory': False,
            'TitleType': 0,
        })
        run_test('GetDocumentProperties', 'GetDocumentProperties', {
            'DocNo': int(env['safe_doc_id']),
            'VersionNo': 0,
            'IsDocTitleNeeded': False,
        })
        run_test('GetDocumentHistory', 'GetDocumentHistory', {
            'DocNo': int(env['safe_doc_id']),
        })
        run_test('GetDocumentCheckoutStatus', 'GetDocumentCheckoutStatus', {
            'DocNo': int(env['safe_doc_id']),
        })
    else:
        for op in ['GetDocument', 'GetDocumentIndexData', 'GetDocumentProperties', 'GetDocumentHistory', 'GetDocumentCheckoutStatus']:
            run_test(op, op, {}, 'Missing THEREFORE_SAFE_DOC_ID')

    # Write tests (optional)
    if allow_writes and env.get('safe_category_id'):
        # Web-client style: GetCategoryInfo -> PreprocessIndexData -> EvaluateConditionalProperties -> CreateDocument
        preprocess_payload = {
            'CategoryNo': int(env['safe_category_id']),
            'ExcludeReduntantForFillDependentFields': True,
            'FillDependentFields': True,
            'GetAutoAppendIxData': False,
            'ResetToDefaults': True,
            'DoCalculateFields': True,
            'IndexData': {
                'IndexDataItems': []
            },
        }
        status, body, raw, elapsed = post_json(f"{base_url}/PreprocessIndexData", preprocess_payload, headers)
        results.append({
            'operation': 'PreprocessIndexData',
            'url': f"{base_url}/PreprocessIndexData",
            'status': status,
            'elapsed_ms': int(elapsed * 1000),
            'json_keys': list(body.keys()) if isinstance(body, dict) else None,
            'has_body': bool(body or raw),
            'raw_snippet': (raw.strip().replace('\\n', ' ')[:300] if raw else None),
        })
        index_items = []
        if isinstance(body, dict):
            index_items = (body.get('IndexData') or {}).get('IndexDataItems') or []

        eval_payload = {
            'IndexDataItems': index_items,
            'CategoryNo': int(env['safe_category_id']),
            'ChangedFieldNos': [],
        }
        status, body, raw, elapsed = post_json(
            f"{base_url}/EvaluateConditionalProperties",
            eval_payload,
            headers,
        )
        results.append({
            'operation': 'EvaluateConditionalProperties',
            'url': f"{base_url}/EvaluateConditionalProperties",
            'status': status,
            'elapsed_ms': int(elapsed * 1000),
            'json_keys': list(body.keys()) if isinstance(body, dict) else None,
            'has_body': bool(body or raw),
            'raw_snippet': (raw.strip().replace('\\n', ' ')[:300] if raw else None),
        })

        sample_content = f"Codex validation {datetime.now(timezone.utc).isoformat()}".encode('utf-8')
        file_b64 = base64.b64encode(sample_content).decode('ascii')
        create_payload = {
            'CategoryNo': int(env['safe_category_id']),
            'CheckInComments': 'Codex validation create/delete test',
            'IndexDataItems': index_items,
            'Streams': [{
                'FileName': 'codex_validation.txt',
                'FileDataBase64JSON': file_b64,
                'NewStreamInsertMode': 0,
            }],
            'DoFillDependentFields': True,
            'WithAutoAppendMode': 0,
        }
        status, body, raw, elapsed = post_json(f"{base_url}/CreateDocument", create_payload, headers)
        created_doc_no = None
        if isinstance(body, dict):
            created_doc_no = body.get('DocNo')
        results.append({
            'operation': 'CreateDocument',
            'url': f"{base_url}/CreateDocument",
            'status': status,
            'elapsed_ms': int(elapsed * 1000),
            'json_keys': list(body.keys()) if isinstance(body, dict) else None,
            'has_body': bool(body or raw),
            'raw_snippet': (raw.strip().replace('\\n', ' ')[:300] if raw else None),
            'doc_no': created_doc_no,
        })
        if created_doc_no:
            time.sleep(2)
            status, body, raw, elapsed = post_json(
                f"{base_url}/DeleteDocument",
                {'DocNo': int(created_doc_no)},
                headers,
            )
            results.append({
                'operation': 'DeleteDocument',
                'url': f"{base_url}/DeleteDocument",
                'status': status,
                'elapsed_ms': int(elapsed * 1000),
                'json_keys': list(body.keys()) if isinstance(body, dict) else None,
                'has_body': bool(body or raw),
                'raw_snippet': (raw.strip().replace('\\n', ' ')[:300] if raw else None),
                'doc_no': created_doc_no,
            })
    elif allow_writes and not env.get('safe_category_id'):
        results.append({
            'operation': 'CreateDocument',
            'url': f"{base_url}/CreateDocument",
            'status': 'SKIPPED',
            'skip_reason': 'Missing THEREFORE_SAFE_CATEGORY_ID',
        })
        results.append({
            'operation': 'DeleteDocument',
            'url': f"{base_url}/DeleteDocument",
            'status': 'SKIPPED',
            'skip_reason': 'Missing THEREFORE_SAFE_CATEGORY_ID',
        })

    # write report
    lines = []
    lines.append('# Therefore API Validation Report')
    lines.append('')
    lines.append(f'Generated: {datetime.now(timezone.utc).isoformat()}')
    lines.append('')
    lines.append('## Environment')
    if env.get('tenant_key'):
        lines.append(f"- Tenant Key: {env.get('tenant_key')}")
    lines.append(f"- Base URL: {env.get('base_url')}")
    lines.append(f"- Auth method: {env.get('auth_method')}")
    lines.append(f"- Username: {redact(env.get('username',''))}")
    if env.get('tenant_name'):
        lines.append(f"- TenantName: {redact(env.get('tenant_name',''))}")
    lines.append(f"- Safe Doc ID: {env.get('safe_doc_id') or ''}")
    lines.append(f"- Safe Category ID: {env.get('safe_category_id') or ''}")
    lines.append(f"- Allow Writes: {env.get('allow_writes') or ''}")
    lines.append('')
    lines.append('## Results')
    for r in results:
        if r['status'] == 'SKIPPED':
            lines.append(f"- {r['operation']}: SKIPPED ({r['skip_reason']})")
            continue
        lines.append(f"- {r['operation']}: HTTP {r['status']} ({r.get('elapsed_ms', 0)} ms, keys={r.get('json_keys')})")

    # Save JSON report for deeper inspection
    json_path = REPORT_PATH.with_suffix('.json')
    json_payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'environment': {
            'base_url': env.get('base_url'),
            'auth_method': env.get('auth_method'),
            'tenant_name': env.get('tenant_name'),
            'safe_doc_id': env.get('safe_doc_id'),
            'safe_category_id': env.get('safe_category_id'),
            'allow_writes': env.get('allow_writes'),
        },
        'results': results,
    }
    json_path.write_text(json.dumps(json_payload, indent=2), encoding='utf-8')

    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    if extra_outputs.get('categories_index') is not None:
        (NOTES_DIR / 'categories_index.json').write_text(
            json.dumps(extra_outputs['categories_index'], indent=2), encoding='utf-8'
        )
    if extra_outputs.get('category_fields') is not None and env.get('safe_category_id'):
        (NOTES_DIR / f"category_fields_{env['safe_category_id']}.json").write_text(
            json.dumps(extra_outputs['category_fields'], indent=2), encoding='utf-8'
        )
    if extra_outputs.get('objects_list_type_3') is not None:
        (NOTES_DIR / 'objects_list_type_3.json').write_text(
            json.dumps(extra_outputs['objects_list_type_3'], indent=2), encoding='utf-8'
        )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
