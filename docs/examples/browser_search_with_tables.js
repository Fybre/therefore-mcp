/**
 * Therefore WebAPI - Search and Retrieve Index Data with Tables
 * Run this in browser console to search category 270 for an order number
 * and retrieve complete index data including table rows.
 */

// Configuration
const config = {
  baseUrl: 'https://craigdemo.thereforeonline.com/theservice/v0001/restun',
  // Use Basic Auth or Bearer token
  authType: 'basic', // 'basic' or 'bearer'
  username: 'your-username',
  password: 'your-password',
  // OR for bearer:
  // bearerToken: 'your-bearer-token'
};

// Helper: Create auth header
function getAuthHeader() {
  if (config.authType === 'basic') {
    const credentials = btoa(`${config.username}:${config.password}`);
    return `Basic ${credentials}`;
  } else {
    return `Bearer ${config.bearerToken}`;
  }
}

// Helper: Make Therefore API call
async function callAPI(endpoint, payload = null) {
  const url = `${config.baseUrl}/${endpoint}`;
  const options = {
    method: payload ? 'POST' : 'GET',
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Authorization': getAuthHeader()
    }
  };

  if (payload) {
    options.body = JSON.stringify(payload);
  }

  console.log(`→ ${options.method} ${endpoint}`, payload || '');

  const response = await fetch(url, options);

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  const data = await response.json();
  console.log(`← ${endpoint}:`, data);
  return data;
}

// Main function: Search and retrieve index data
async function searchAndRetrieveIndexData(categoryNo, orderNo) {
  console.log('='.repeat(70));
  console.log(`Searching category ${categoryNo} for Order No: ${orderNo}`);
  console.log('='.repeat(70));

  let queryGuid = null;

  try {
    // Step 1: Execute query
    console.log('\n📝 Step 1: Execute query...');
    const queryResult = await callAPI('ExecuteAsyncSingleQuery', {
      Query: {
        CategoryNo: categoryNo,
        ReturnFields: ['DocNo', 'Order_No', 'Customer_Name'], // Adjust field names as needed
        Conditions: [
          {
            FieldNoOrName: 'Order_No', // Adjust to your field name
            Condition: `Order_No = '${orderNo}'`
          }
        ]
      }
    });

    queryGuid = queryResult.QueryGuid;
    console.log(`✓ Query created: ${queryGuid}`);

    // Step 2: Get query results
    console.log('\n📋 Step 2: Fetch query results...');
    const results = await callAPI('GetNextSingleQueryRows', {
      QueryGuid: queryGuid,
      RowBlockSize: 100
    });

    console.log(`✓ Found ${results.Rows?.length || 0} documents`);

    if (!results.Rows || results.Rows.length === 0) {
      console.log('⚠️ No documents found');
      return;
    }

    // Extract DocNos from results
    const docNos = results.Rows.map(row => {
      // IndexValues[0] is typically DocNo (first column)
      return row.IndexValues[0];
    });

    console.log(`📄 Document numbers: ${docNos.join(', ')}`);

    // Step 3: Get detailed index data including tables
    console.log('\n📊 Step 3: Get index data with tables...');
    const indexData = await callAPI('GetDocumentIndexData', {
      DocNos: docNos,
      VersionNos: docNos.map(() => 0), // 0 = current version
      CategoryNo: categoryNo
    });

    console.log('✓ Retrieved index data');

    // Step 4: Display results
    console.log('\n' + '='.repeat(70));
    console.log('RESULTS:');
    console.log('='.repeat(70));

    indexData.IndexDataResult?.forEach((doc, docIndex) => {
      console.log(`\n📄 Document #${docIndex + 1} (DocNo: ${docNos[docIndex]})`);
      console.log('-'.repeat(70));

      doc.IndexData?.forEach(field => {
        if (field.TableData) {
          // Table field
          console.log(`\n  📋 ${field.FieldID || `Field ${field.FieldNo}`} (Table):`);
          console.log(`     Rows: ${field.TableData.Rows?.length || 0}`);

          field.TableData.Rows?.forEach((row, rowIndex) => {
            console.log(`\n     Row ${rowIndex + 1}:`);
            row.Columns?.forEach(col => {
              console.log(`       - Field ${col.FieldNo}: ${col.Value}`);
            });
          });
        } else {
          // Regular field
          console.log(`  ${field.FieldID || `Field ${field.FieldNo}`}: ${field.Value}`);
        }
      });
    });

    return indexData;

  } catch (error) {
    console.error('❌ Error:', error);
    throw error;
  } finally {
    // Step 5: Always release the query
    if (queryGuid) {
      console.log('\n🧹 Step 4: Release query...');
      try {
        await callAPI('ReleaseSingleQuery', { QueryGuid: queryGuid });
        console.log('✓ Query released');
      } catch (err) {
        console.warn('⚠️ Failed to release query:', err);
      }
    }
  }
}

// Example usage
console.log('Therefore WebAPI Search Example');
console.log('================================\n');
console.log('To use:');
console.log('1. Update config object with your credentials');
console.log('2. Run: searchAndRetrieveIndexData(270, "ORD-12345")');
console.log('\nOr just run the example below:\n');

// Run example (update the order number)
searchAndRetrieveIndexData(270, '12345')
  .then(data => {
    console.log('\n✅ Complete! Full data object:', data);
  })
  .catch(err => {
    console.error('\n❌ Failed:', err);
  });
