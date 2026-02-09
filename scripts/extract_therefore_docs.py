#!/usr/bin/env python3
import csv
import json
import re
import urllib.request
import ssl
import html as html_lib
from pathlib import Path

try:
    from pdfminer.high_level import extract_text
except Exception:
    extract_text = None

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / 'docs' / 'export'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

CTX = ssl.create_default_context()
TIMEOUT = 15


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, context=CTX, timeout=TIMEOUT) as r:
        data = r.read()
    return data.decode('utf-8', errors='replace')


def clean_html(text: str) -> str:
    text = re.sub(r'<[^<]+?>', ' ', text)
    text = html_lib.unescape(text)
    return ' '.join(text.split())


def parse_tables(html: str):
    tables = []
    for table_html in re.findall(r'<table[^>]*>([\s\S]*?)</table>', html, re.S):
        rows = []
        header_rows = []
        for row_html in re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', table_html, re.S):
            cells = []
            is_header = '<th' in row_html.lower()
            for cell_html in re.findall(r'<t[dh][^>]*>([\s\S]*?)</t[dh]>', row_html, re.S):
                cells.append(clean_html(cell_html))
            if cells:
                rows.append(cells)
                if is_header:
                    header_rows.append(cells)
        if rows:
            tables.append({
                'rows': rows,
                'header_rows': header_rows,
                'raw': table_html,
            })
    return tables


def is_numeric_like(s: str) -> bool:
    s = s.strip().replace(',', '')
    if re.fullmatch(r'-?\d+', s):
        return True
    if re.fullmatch(r'\d+\s*-\s*\d+', s):
        return True
    return False


def extract_tenant_operations():
    base_help = 'https://craigdemo.thereforeonline.com/theservice/v0001/restun/help'
    base_root = base_help.rsplit('/help', 1)[0]
    html = fetch(base_help)

    rows = re.findall(r'<tr>\s*<td>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>', html, re.S)
    ops = []
    for td1, td2, td3 in rows:
        if 'rel="operation"' not in td2:
            continue
        path = clean_html(td1)
        m = re.search(r'>\s*([A-Z]+)\s*<', td2)
        method = m.group(1) if m else None
        m = re.search(r'href="([^"]+)"', td2)
        href = m.group(1) if m else None
        desc = clean_html(td3)
        ops.append({
            'path': path,
            'method': method,
            'help_href': href,
            'description': desc,
        })

    for op in ops:
        if not op['help_href']:
            continue
        help_url = base_root + '/' + op['help_href'].lstrip('/')
        try:
            detail = fetch(help_url)
        except Exception as e:
            op['detail_error'] = str(e)
            continue
        # URL and method
        m = re.search(r'<span class="uri-template">([^<]+)</span>', detail)
        op['url'] = html_lib.unescape(m.group(1).strip()) if m else None
        m = re.search(r'<span class="method">([^<]+)</span>', detail)
        op['method'] = (m.group(1).strip() if m else op.get('method'))
        # title
        m = re.search(r'<p class="heading1">\s*Reference for\s*([^<]+)</p>', detail)
        op['reference'] = html_lib.unescape(m.group(1).strip()) if m else None
        if op.get('reference') and not op.get('operation'):
            op['operation'] = op['reference'].rstrip('/').split('/')[-1]
        # pre blocks
        pre_blocks = {}
        for m in re.finditer(r'<pre class="([^"]+)">([\s\S]*?)</pre>', detail):
            cls = m.group(1).strip()
            text = html_lib.unescape(m.group(2)).strip()
            pre_blocks[cls] = text
        op['examples'] = {
            'request_xml': pre_blocks.get('request-xml'),
            'request_json': pre_blocks.get('request-json'),
            'response_xml': pre_blocks.get('response-xml'),
            'response_json': pre_blocks.get('response-json'),
        }
        op['schemas'] = {
            'request': pre_blocks.get('request-schema'),
            'response': pre_blocks.get('response-schema'),
        }
        # keep all blocks for completeness
        op['pre_blocks'] = pre_blocks
        op['help_url'] = help_url

    # write outputs
    (EXPORT_DIR / 'tenant_operations.json').write_text(json.dumps(ops, indent=2), encoding='utf-8')

    # short catalog CSV
    with (EXPORT_DIR / 'tenant_operations_catalog.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['operation', 'path', 'method', 'url', 'help_url'])
        for op in ops:
            writer.writerow([
                op.get('operation') or '',
                op.get('path') or '',
                op.get('method') or '',
                op.get('url') or '',
                op.get('help_url') or '',
            ])

    return ops


def extract_thereforenet_constants():
    base = 'https://therefore.net/help/Online/en-us/AR/SDK/WebAPI/'
    hmcontent_js = fetch(base + 'js/hmcontent.js')
    pairs = re.findall(r'cp:"([^"]+)"[^}]*?hf:"([^"]+)"', hmcontent_js)

    pages = []
    for cp, hf in pairs:
        if not hf:
            continue
        pages.append({
            'title': cp,
            'href': hf,
            'url': base + hf,
        })

    code_tables = []
    total_pages = len(pages)
    for idx_page, page in enumerate(pages, start=1):
        try:
            html = fetch(page['url'])
        except Exception as e:
            page['error'] = str(e)
            continue
        if idx_page % 25 == 0:
            print(f'Fetched {idx_page}/{total_pages} therefore.net pages', flush=True)
        title_m = re.search(r'<title>([^<]+)</title>', html)
        page_title = clean_html(title_m.group(1)) if title_m else page['title']
        tables = parse_tables(html)
        for idx, table in enumerate(tables):
            rows = table['rows']
            if not rows:
                continue
            # determine headers
            headers = table['header_rows'][0] if table['header_rows'] else None
            data_rows = rows[1:] if headers else rows
            if not data_rows:
                continue
            # numeric first column heuristic
            numeric_first = sum(1 for r in data_rows if r and is_numeric_like(r[0]))
            numeric_second = sum(1 for r in data_rows if len(r) > 1 and is_numeric_like(r[1]))
            header_text = ' '.join(headers).lower() if headers else ''
            header_hint = any(k in header_text for k in ['type', 'code', 'value', 'id', 'number'])
            if numeric_first >= 2 or numeric_second >= 2 or header_hint:
                code_tables.append({
                    'page_title': page_title,
                    'page_href': page['href'],
                    'page_url': page['url'],
                    'table_index': idx,
                    'headers': headers,
                    'rows': data_rows,
                    'header_hint': header_hint,
                    'numeric_first_count': numeric_first,
                    'numeric_second_count': numeric_second,
                })

    (EXPORT_DIR / 'thereforenet_code_tables.json').write_text(json.dumps(code_tables, indent=2), encoding='utf-8')
    # also write page index
    (EXPORT_DIR / 'thereforenet_pages_index.json').write_text(json.dumps(pages, indent=2), encoding='utf-8')

    return code_tables


def extract_db_codes_pdf():
    if extract_text is None:
        return None
    pdf_path = ROOT / 'docs' / 'reference' / 'user' / 'Therefore 2020 DB Codes.pdf'
    if not pdf_path.exists():
        return None

    text = extract_text(str(pdf_path))
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    sections = []
    current = None
    last_entry = None

    header_re = re.compile(r'^(The[A-Za-z0-9_\*]+\.[A-Za-z0-9_]+)')
    code_re = re.compile(r'^(-?\d+)\s+(.+)$')

    for ln in lines:
        m = header_re.match(ln)
        if m:
            if current:
                sections.append(current)
            current = {'section': m.group(1), 'entries': []}
            last_entry = None
            continue
        m = code_re.match(ln)
        if m and current:
            code = m.group(1)
            desc = m.group(2).strip()
            entry = {'code': code, 'description': desc}
            current['entries'].append(entry)
            last_entry = entry
            continue
        # continuation line
        if current and last_entry and not header_re.match(ln):
            last_entry['description'] = (last_entry['description'] + ' ' + ln).strip()

    if current:
        sections.append(current)

    (EXPORT_DIR / 'therefore_db_codes.json').write_text(json.dumps(sections, indent=2), encoding='utf-8')
    return sections


def main():
    ops = extract_tenant_operations()
    print(f'Tenant operations: {len(ops)}', flush=True)
    code_tables = extract_thereforenet_constants()
    print(f'Therefore.net code-like tables: {len(code_tables)}', flush=True)
    db_codes = extract_db_codes_pdf()
    if db_codes is not None:
        print(f'DB code sections: {len(db_codes)}', flush=True)
    else:
        print('DB code sections: not extracted', flush=True)


if __name__ == '__main__':
    main()
