#!/usr/bin/env python3
import json
import re
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / 'docs' / 'export'
SPECS_DIR = ROOT / 'docs' / 'specs'
SPECS_DIR.mkdir(parents=True, exist_ok=True)


KEY_TERMS = ['type', 'name', 'description', 'mask', 'fieldno', 'code', 'value', 'number', 'permission']


def is_numeric_like(s: str) -> bool:
    s = s.strip().replace(',', '')
    if re.fullmatch(r'-?\d+', s):
        return True
    if re.fullmatch(r'\d+\s*-\s*\d+', s):
        return True
    return False


def infer_header(rows):
    if not rows:
        return None, rows
    first = rows[0]
    if not first:
        return None, rows
    # if any header keyword and first cell not numeric -> treat as header
    headerish = any(term in cell.lower() for cell in first for term in KEY_TERMS)
    if headerish and not is_numeric_like(first[0]):
        return first, rows[1:]
    return None, rows


def to_snake(name: str) -> str:
    if not name:
        return name
    # handle non-alnum
    name = re.sub(r'[^A-Za-z0-9]+', '_', name)
    # camel to snake
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.lower().strip('_')


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def build_constants():
    code_tables = load_json(EXPORT_DIR / 'thereforenet_code_tables.json')
    db_codes = load_json(EXPORT_DIR / 'therefore_db_codes.json')

    constants = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'sources': {
            'thereforenet_code_tables': str(EXPORT_DIR / 'thereforenet_code_tables.json'),
            'therefore_db_codes': str(EXPORT_DIR / 'therefore_db_codes.json'),
        },
        'db_codes': {section['section']: section['entries'] for section in db_codes},
        'webapi_constants': {},
        'unclassified_tables': [],
    }

    for table in code_tables:
        rows = table.get('rows') or []
        headers = table.get('headers')
        if not headers:
            headers, rows = infer_header(rows)
        if not headers:
            # likely step-by-step tables
            constants['unclassified_tables'].append({
                'page_title': table.get('page_title'),
                'page_url': table.get('page_url'),
                'rows': rows,
            })
            continue

        header_key = ' | '.join(h.lower() for h in headers)
        # normalize rows to list of dicts
        def row_to_dict(row):
            item = {}
            for idx, h in enumerate(headers):
                if idx < len(row):
                    item[h] = row[idx]
                else:
                    item[h] = ''
            return item
        items = [row_to_dict(r) for r in rows if r]

        if 'client type' in header_key:
            constants['webapi_constants']['client_types'] = {item['Client Type']: item.get('Description', '') for item in items if 'Client Type' in item}
            continue
        if 'permission name' in header_key and 'permission mask' in header_key:
            constants['webapi_constants']['webapi_permission_masks'] = {item['Permission Name']: item.get('Permission Mask', '') for item in items}
            continue
        if 'field name' in header_key and 'fieldno' in header_key:
            constants['webapi_constants']['internal_query_fields'] = {
                item['Field Name']: {
                    'field_no': item.get('FieldNo', ''),
                    'description': item.get('Description', ''),
                } for item in items if 'Field Name' in item
            }
            continue
        if 'code page number' in header_key and 'display name' in header_key:
            constants['webapi_constants']['code_pages'] = {
                item['Code page number']: {
                    'name': item.get('Name', ''),
                    'display_name': item.get('Display name', ''),
                    'dotnet_support': item.get('.NET Framework support', ''),
                } for item in items if 'Code page number' in item
            }
            continue

        # fall back to general constants list
        constants['webapi_constants'].setdefault('misc_tables', []).append({
            'page_title': table.get('page_title'),
            'page_url': table.get('page_url'),
            'headers': headers,
            'rows': rows,
        })

    (SPECS_DIR / 'therefore_constants.json').write_text(json.dumps(constants, indent=2), encoding='utf-8')
    return constants


def build_tool_definitions():
    ops = load_json(EXPORT_DIR / 'tenant_operations.json')
    tools = []
    for op in ops:
        operation = op.get('operation') or (op.get('path') or '').strip('/').split('/')[-1]
        name = to_snake(operation)
        tool = {
            'name': name,
            'title': operation,
            'description': op.get('description') or f'Therefore operation {operation}',
            'method': op.get('method'),
            'path': op.get('path'),
            'url': op.get('url'),
            'help_url': op.get('help_url'),
            'input_schema': {
                'type': 'object',
                'additionalProperties': True,
            },
            'examples': op.get('examples', {}),
            'schemas': op.get('schemas', {}),
        }
        tools.append(tool)

    out = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': str(EXPORT_DIR / 'tenant_operations.json'),
        'tools': tools,
    }
    (SPECS_DIR / 'therefore_mcp_tools.json').write_text(json.dumps(out, indent=2), encoding='utf-8')
    return out


def build_api_spec_md(constants):
    ops = load_json(EXPORT_DIR / 'tenant_operations.json')
    total = len(ops)
    with_request_json = sum(1 for o in ops if o.get('examples', {}).get('request_json'))
    with_response_json = sum(1 for o in ops if o.get('examples', {}).get('response_json'))

    base_urls = sorted({
        o.get('url').rsplit('/', 1)[0]
        for o in ops if o.get('url')
    })

    lines = []
    lines.append('# Therefore Web API Spec (Derived)')
    lines.append('')
    lines.append(f'Generated: {datetime.now(timezone.utc).isoformat()}')
    lines.append('')
    lines.append('## Summary')
    lines.append(f'- Operations: {total}')
    lines.append(f'- Ops with JSON request examples: {with_request_json}')
    lines.append(f'- Ops with JSON response examples: {with_response_json}')
    lines.append('')
    lines.append('## Base URLs')
    for url in base_urls:
        lines.append(f'- {url}')
    lines.append('')
    lines.append('## Constants & Codes')
    lines.append('- See `therefore_constants.json` for normalized constants and DB codes.')
    lines.append('')
    lines.append('## Operations Catalog')
    lines.append('')
    lines.append('| Operation | Method | Path | URL |')
    lines.append('| --- | --- | --- | --- |')
    for op in ops:
        operation = op.get('operation') or (op.get('path') or '').strip('/').split('/')[-1]
        lines.append(f'| {operation} | {op.get("method") or ""} | {op.get("path") or ""} | {op.get("url") or ""} |')

    (SPECS_DIR / 'therefore_api_spec.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    constants = build_constants()
    build_tool_definitions()
    build_api_spec_md(constants)


if __name__ == '__main__':
    main()
