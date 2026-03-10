/**
 * Therefore WebAPI - Query Referenced Table Rows
 * Looks up a referenced table (Type=5 object) by name, then queries its rows
 * with optional filter conditions.
 *
 * Run in browser console or as a Node.js script (requires node-fetch / global fetch).
 *
 * Key API notes:
 *  - Referenced tables are queried via ExecuteAsyncSingleQuery with CategoryNo = DataTypeNo
 *  - ExecuteAsyncSingleQuery returns QueryId (lowercase d) AND first page in QueryResult
 *  - GetNextSingleQueryRows / ReleaseSingleQuery use QueryID (uppercase D)
 *  - Conditions: { FieldNoOrName, Condition } where Condition is a bare value or
 *    operator-prefixed string e.g. "LIKE Acme%" — NOT "= value"
 *  - TenantName header is mandatory for Therefore Online
 */

// ---------------------------------------------------------------------------
// Configuration — edit these before running
// ---------------------------------------------------------------------------
const config = {
  baseUrl: 'https://YOUR_TENANT.thereforeonline.com/theservice/v0001/restun',
  tenantName: 'YOUR_TENANT',
  authType: 'basic', // 'basic' or 'bearer'
  username: 'your-username',
  password: 'your-password',
  // bearerToken: 'your-bearer-token',

  // Referenced table to query
  tableName: 'Sumitomo_Approver_Entity',

  // Optional filter conditions — leave empty array [] for all rows
  // Exact match:   { FieldNoOrName: 'Entity', Condition: 'Acme Corp' }
  // Wildcard:      { FieldNoOrName: 'Entity', Condition: 'LIKE Acme%' }
  // All rows:      { FieldNoOrName: 'Entity', Condition: 'LIKE %' }
  conditions: [],

  maxRows: 5000,
  rowBlockSize: 1000,
};

// ---------------------------------------------------------------------------
// Auth helper
// ---------------------------------------------------------------------------
function getAuthHeader() {
  if (config.authType === 'basic') {
    return `Basic ${btoa(`${config.username}:${config.password}`)}`;
  }
  return `Bearer ${config.bearerToken}`;
}

// ---------------------------------------------------------------------------
// Generic API call
// ---------------------------------------------------------------------------
async function callAPI(endpoint, payload = {}) {
  const url = `${config.baseUrl}/${endpoint}`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': getAuthHeader(),
      'TenantName': config.tenantName,
    },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${endpoint} failed (${resp.status}): ${text}`);
  }
  return resp.json();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function queryReferencedTable() {
  // Step 1: Find the table's DataTypeNo
  console.log(`Looking up referenced table: ${config.tableName}`);
  const objectsResp = await callAPI('GetObjects', { Flags: 0, Type: 5 });
  const items = objectsResp.Items || [];
  const tableItem = items.find(
    (i) => (i.Name || '').toLowerCase() === config.tableName.toLowerCase()
  );
  if (!tableItem) {
    throw new Error(
      `Referenced table '${config.tableName}' not found. ` +
      `Available: ${items.map((i) => i.Name).join(', ')}`
    );
  }
  const dataTypeNo = tableItem.ID;
  console.log(`Found: ${tableItem.Name} (DataTypeNo=${dataTypeNo})`);

  // Step 2: Get column schema
  const tableInfo = await callAPI('GetReferencedTableInfo', { DataTypeNo: dataTypeNo });
  const columns = tableInfo.Columns || [];
  console.log(`Columns: ${columns.map((c) => c.ColName).join(', ')}`);

  // Step 3: Execute query
  const queryPayload = {
    CategoryNo: dataTypeNo,
    Conditions: config.conditions,
    MaxRows: config.maxRows,
    RowBlockSize: config.rowBlockSize,
  };
  console.log('Executing query...');
  const firstResp = await callAPI('ExecuteAsyncSingleQuery', queryPayload);

  // Note: initial response uses QueryId (lowercase d)
  const queryId = firstResp.QueryId ?? firstResp.QueryID;
  let hasRemaining = Boolean(firstResp.HasRemainingRows);
  const firstResult = firstResp.QueryResult || {};
  const allRows = [...(firstResult.ResultRows || [])];

  console.log(
    `First batch: ${allRows.length} rows, hasRemaining=${hasRemaining}`
  );

  try {
    // Step 4: Paginate — GetNextSingleQueryRows uses QueryID (uppercase D)
    while (hasRemaining && queryId != null) {
      const nextResp = await callAPI('GetNextSingleQueryRows', {
        QueryID: queryId,
        RowBlockSize: config.rowBlockSize,
      });
      hasRemaining = Boolean(nextResp.HasRemainingRows);
      const nextRows = (nextResp.QueryResult || {}).ResultRows || [];
      allRows.push(...nextRows);
      console.log(
        `Next batch: +${nextRows.length} rows, total=${allRows.length}, hasRemaining=${hasRemaining}`
      );
    }
  } finally {
    // Step 5: Always release the query session
    if (queryId != null) {
      await callAPI('ReleaseSingleQuery', { QueryID: queryId }).catch((e) =>
        console.warn('ReleaseSingleQuery failed (non-fatal):', e.message)
      );
    }
  }

  // Step 6: Return results
  const result = {
    dataTypeNo,
    name: tableInfo.Name || config.tableName,
    columns,
    rowCount: allRows.length,
    rows: allRows,
  };

  console.log(`\nResult: ${result.rowCount} rows from '${result.name}'`);
  console.table(
    allRows.slice(0, 20).map((row) => {
      const obj = {};
      (row.IndexValues || []).forEach((val, i) => {
        obj[columns[i]?.ColName ?? `col${i}`] = val;
      });
      return obj;
    })
  );
  if (allRows.length > 20) {
    console.log(`(showing first 20 of ${allRows.length} rows)`);
  }

  return result;
}

// Run
queryReferencedTable().catch(console.error);
