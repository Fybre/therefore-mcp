# Therefore Python Client Examples

Complete examples for common Therefore operations using the `ThereforeClient` class.

## Setup

```python
import sys
sys.path.insert(0, 'src')
from therefore_client import ThereforeClient

# Initialize client
client = ThereforeClient(
    base_url="https://demo.thereforeonline.com/theservice/v0001/restun",
    username="your_username",
    password="your_password"
)
```

## Check if Document Exists

```python
def document_exists(client, doc_no):
    """Check if a document exists in Therefore."""
    try:
        doc = client.get_document(doc_no, include_index_data=False)
        return doc is not None
    except Exception as e:
        if "not found" in str(e).lower():
            return False
        raise  # Re-raise if it's a different error

# Usage
if document_exists(client, 12345):
    print("Document exists")
else:
    print("Document not found")
```

## Parse XML and Check Documents

```python
import xml.etree.ElementTree as ET

def extract_doc_ids_from_xml(xml_file_path):
    """Extract document IDs from XML file."""
    tree = ET.parse(xml_file_path)
    root = tree.getroot()

    doc_ids = []
    # Example: Extract from <Document DocNo="12345"> elements
    for doc_elem in root.findall('.//Document'):
        doc_no = doc_elem.get('DocNo')
        if doc_no:
            doc_ids.append(int(doc_no))

    return doc_ids

def check_documents_in_therefore(client, doc_ids):
    """Check which document IDs exist in Therefore."""
    results = {
        'found': [],
        'not_found': [],
        'errors': []
    }

    for doc_id in doc_ids:
        try:
            doc = client.get_document(doc_id, include_index_data=False)
            results['found'].append({
                'doc_no': doc_id,
                'category_no': doc.get('CategoryNo'),
                'title': doc.get('Title')
            })
        except Exception as e:
            if "not found" in str(e).lower():
                results['not_found'].append(doc_id)
            else:
                results['errors'].append({
                    'doc_no': doc_id,
                    'error': str(e)
                })

    return results

# Usage
doc_ids = extract_doc_ids_from_xml('export.xml')
results = check_documents_in_therefore(client, doc_ids)

print(f"Found: {len(results['found'])}")
print(f"Not found: {len(results['not_found'])}")
print(f"Errors: {len(results['errors'])}")
```

## Query Documents with Specific Index Data

```python
def find_documents_by_invoice_number(client, invoice_number, category_no):
    """Find documents by invoice number field."""

    # First, get category info to find the invoice number field
    cat_info = client.get_category_info(category_no)

    # Find the invoice number field (assuming field name is "Invoice Number")
    invoice_field = None
    for field in cat_info.get('IndexDataDef', []):
        if field.get('Name') == 'Invoice Number':
            invoice_field = field
            break

    if not invoice_field:
        raise ValueError("Invoice Number field not found in category")

    # Build query
    query = {
        "CategoryNo": category_no,
        "WhereClause": f"[{invoice_field['ColName']}] = '{invoice_number}'"
    }

    # Execute query
    result = client.execute_single_query(query)

    return result.get('IndexDataRows', [])

# Usage
docs = find_documents_by_invoice_number(client, "INV-2024-001", 123)
for doc in docs:
    print(f"Doc No: {doc['IndexValues'][0]}")  # First value is usually doc number
```

## Check if Index Data Exists

```python
def check_index_data_exists(client, category_no, field_name, field_value):
    """Check if a document with specific index data exists."""

    # Get category structure
    cat_info = client.get_category_info(category_no)

    # Find the field
    target_field = None
    for field in cat_info.get('IndexDataDef', []):
        if field.get('Name') == field_name:
            target_field = field
            break

    if not target_field:
        raise ValueError(f"Field '{field_name}' not found in category")

    # Build query
    query = {
        "CategoryNo": category_no,
        "WhereClause": f"[{target_field['ColName']}] = '{field_value}'",
        "MaxRows": 1  # Only need to know if at least one exists
    }

    # Execute query
    result = client.execute_single_query(query)
    rows = result.get('IndexDataRows', [])

    return len(rows) > 0

# Usage
exists = check_index_data_exists(
    client,
    category_no=123,
    field_name="Customer Name",
    field_value="Acme Corp"
)

if exists:
    print("Document with that index data exists")
```

## Get Document with Index Data

```python
def get_document_with_index_data(client, doc_no):
    """Get document with all index data parsed."""

    # Get document with index data
    doc = client.get_document(doc_no, include_index_data=True)

    # Parse index data into a dict
    index_data = {}
    index_def = doc.get('IndexDataDef', [])
    index_values = doc.get('IndexData', {}).get('IndexDataItems', [])

    for field_def, value_item in zip(index_def, index_values):
        field_name = field_def.get('Name')

        # Extract value based on field type
        field_type = field_def.get('TypeNo')

        if field_type == 0:  # String
            value = value_item.get('Value', {}).get('StringIndexData', {}).get('Value')
        elif field_type == 1:  # Integer
            value = value_item.get('Value', {}).get('IntIndexData', {}).get('Value')
        elif field_type == 2:  # Date
            value = value_item.get('Value', {}).get('DateIndexData', {}).get('Value')
        elif field_type == 6:  # Keyword
            value = value_item.get('Value', {}).get('KeywordIndexData', {}).get('KeywordName')
        elif field_type == 9:  # Table
            value = value_item.get('Value', {}).get('TableData', {}).get('Rows', [])
        else:
            value = value_item.get('Value')

        index_data[field_name] = value

    return {
        'doc_no': doc.get('DocNo'),
        'category_no': doc.get('CategoryNo'),
        'title': doc.get('Title'),
        'index_data': index_data
    }

# Usage
doc_data = get_document_with_index_data(client, 12345)
print(f"Invoice Number: {doc_data['index_data'].get('Invoice Number')}")
print(f"Amount: {doc_data['index_data'].get('Amount')}")
```

## Create Document from Data

```python
def create_invoice_document(client, category_no, invoice_data, pdf_path=None):
    """Create an invoice document with index data."""

    # Build index data items
    index_data_items = [
        {
            "Name": "Invoice Number",
            "Value": {"StringIndexData": {"Value": invoice_data['invoice_number']}}
        },
        {
            "Name": "Invoice Date",
            "Value": {"DateIndexData": {"Value": invoice_data['invoice_date']}}
        },
        {
            "Name": "Amount",
            "Value": {"MoneyIndexData": {"Value": invoice_data['amount']}}
        },
        {
            "Name": "Customer Name",
            "Value": {"StringIndexData": {"Value": invoice_data['customer_name']}}
        }
    ]

    # Prepare streams
    streams = []
    if pdf_path:
        import base64
        with open(pdf_path, 'rb') as f:
            file_data = base64.b64encode(f.read()).decode('ascii')

        streams.append({
            "FileName": invoice_data['invoice_number'] + ".pdf",
            "FileDataBase64JSON": file_data,
            "NewStreamInsertMode": 0
        })
    else:
        # Create text document
        content = f"Invoice: {invoice_data['invoice_number']}\n"
        content += f"Date: {invoice_data['invoice_date']}\n"
        content += f"Amount: ${invoice_data['amount']}\n"
        content += f"Customer: {invoice_data['customer_name']}"

        streams.append(
            ThereforeClient.make_stream_from_text(
                f"{invoice_data['invoice_number']}.txt",
                content
            )
        )

    # Create document
    result = client.create_document(
        category_no=category_no,
        streams=streams,
        index_data_items=index_data_items,
        check_in_comments=f"Created from import - {invoice_data['invoice_number']}"
    )

    return result

# Usage
invoice_data = {
    'invoice_number': 'INV-2024-001',
    'invoice_date': '2024-02-17',
    'amount': 1500.00,
    'customer_name': 'Acme Corp'
}

result = create_invoice_document(client, 123, invoice_data, 'invoice.pdf')
print(f"Created document: {result.get('DocNo')}")
```

## Batch Process XML Documents

```python
def batch_process_xml_documents(client, xml_file, category_no):
    """
    Complete example: Parse XML, check if docs exist, create missing ones.
    """
    import xml.etree.ElementTree as ET

    # Parse XML
    tree = ET.parse(xml_file)
    root = tree.getroot()

    results = {
        'processed': 0,
        'already_exist': [],
        'created': [],
        'errors': []
    }

    for invoice_elem in root.findall('.//Invoice'):
        invoice_no = invoice_elem.get('Number')
        results['processed'] += 1

        try:
            # Check if document with this invoice number exists
            query = {
                "CategoryNo": category_no,
                "WhereClause": f"[InvoiceNumber] = '{invoice_no}'",
                "MaxRows": 1
            }

            existing = client.execute_single_query(query)

            if existing.get('IndexDataRows'):
                results['already_exist'].append(invoice_no)
                continue

            # Extract data from XML
            invoice_data = {
                'invoice_number': invoice_no,
                'invoice_date': invoice_elem.get('Date'),
                'amount': float(invoice_elem.get('Amount', 0)),
                'customer_name': invoice_elem.find('Customer').text
            }

            # Create document
            doc_result = create_invoice_document(client, category_no, invoice_data)
            results['created'].append({
                'invoice_no': invoice_no,
                'doc_no': doc_result.get('DocNo')
            })

        except Exception as e:
            results['errors'].append({
                'invoice_no': invoice_no,
                'error': str(e)
            })

    return results

# Usage
results = batch_process_xml_documents(client, 'invoices.xml', 123)
print(f"Processed: {results['processed']}")
print(f"Already exist: {len(results['already_exist'])}")
print(f"Created: {len(results['created'])}")
print(f"Errors: {len(results['errors'])}")
```

## Error Handling Best Practices

```python
from therefore_client import ThereforeClient
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def safe_get_document(client, doc_no):
    """Get document with comprehensive error handling."""
    try:
        doc = client.get_document(doc_no)
        return {'success': True, 'data': doc}

    except Exception as e:
        error_msg = str(e).lower()

        if "not found" in error_msg or "404" in error_msg:
            logger.warning(f"Document {doc_no} not found")
            return {'success': False, 'error': 'not_found'}

        elif "unauthorized" in error_msg or "403" in error_msg:
            logger.error(f"Access denied for document {doc_no}")
            return {'success': False, 'error': 'access_denied'}

        elif "timeout" in error_msg:
            logger.error(f"Timeout getting document {doc_no}")
            return {'success': False, 'error': 'timeout'}

        else:
            logger.exception(f"Unexpected error getting document {doc_no}")
            return {'success': False, 'error': 'unknown', 'details': str(e)}

# Usage
result = safe_get_document(client, 12345)
if result['success']:
    print(f"Got document: {result['data']}")
else:
    print(f"Error: {result['error']}")
```

## Query Optimization

```python
def efficient_document_lookup(client, doc_nos):
    """
    Efficiently check multiple documents.
    Better than calling get_document for each one.
    """
    if not doc_nos:
        return []

    # Build query for multiple doc numbers
    doc_nos_str = ','.join(str(n) for n in doc_nos)

    query = {
        "WhereClause": f"[DocNo] IN ({doc_nos_str})",
        "MaxRows": len(doc_nos)
    }

    result = client.execute_single_query(query)
    rows = result.get('IndexDataRows', [])

    # Map results back to doc numbers
    found_docs = {}
    for row in rows:
        doc_no = row['IndexValues'][0]  # First value is usually DocNo
        found_docs[doc_no] = row

    return found_docs

# Usage - Check 1000 documents in one query instead of 1000 API calls
doc_ids = list(range(10000, 11000))
found = efficient_document_lookup(client, doc_ids)
print(f"Found {len(found)} out of {len(doc_ids)} documents")
```

## See Also

- `src/therefore_client.py` - Full ThereforeClient API
- `docs/therefore-api-complete-guide.md` - API concepts and patterns
- `docs/knowledge-base.json` - Structured API knowledge
