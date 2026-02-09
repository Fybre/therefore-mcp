# Therefore API Validation Report

Generated: 2026-02-09T01:35:59.093699+00:00

## Environment
- Base URL: https://craigdemo.thereforeonline.com/theservice/v0001/restun
- Auth method: Basic
- Username: cr********tt
- TenantName: cr*****mo
- Safe Doc ID: 18537
- Safe Category ID: 262
- Allow Writes: true

## Results
- GetWebAPIServerVersion: HTTP 200 (235 ms, keys=['VersionDescription', 'VersionString', 'ServiceVersion'])
- GetConnectionToken: HTTP 200 (139 ms, keys=['Token'])
- GetDomainInfo: HTTP 200 (126 ms, keys=['DefaultDomain', 'DomainNames'])
- GetClientDiscoveryInfo: HTTP 200 (70 ms, keys=['WebApiUrl', 'ClientSettings'])
- GetConnectedUser: HTTP 200 (114 ms, keys=['User'])
- GetPermissionConstants: HTTP 200 (135 ms, keys=['PermissionConstants'])
- GetRolePermissionConstants: HTTP 200 (129 ms, keys=['PermissionConstants'])
- GetCategoriesTree: HTTP 200 (155 ms, keys=['TreeItems'])
- GetCategoryInfo: HTTP 200 (429 ms, keys=['AutoAppendMode', 'BelongsToCaseDefinition', 'CategoryFields', 'CategoryNo', 'CheckInCommentsMode', 'Description', 'DocumentTitleLength', 'FieldCount', 'FolderNo', 'Guid', 'Height', 'IsFulltextEnabled', 'Name', 'NewVersionOnIndexDataChange', 'QueryTemplateNo', 'SubCtgryFieldIx', 'TableName', 'Title', 'Version', 'WatermarkDocNo', 'WatermarkHPos', 'WatermarkMode', 'WatermarkResolution', 'WatermarkVPos', 'Width', 'WorkflowFolder', 'WorkflowForm', 'WorkflowProcessNo', 'WorkflowProcessNoUpdate', 'BackgroundColor', 'CoverMode', 'DocumentPreview', 'EmptyDocMode', 'FullTextDate', 'FullTextMode', 'AccessMask', 'FieldNoSearchOrder', 'RoleAccessMask', 'AvailableLCIDs', 'CurrentLCID', 'SharedLinkDefaultExpiryPeriod', 'SharedLinkEditRole', 'SharedLinkMandatoryPassword', 'SharedLinkReadOnlyRole', 'SharedLinkShareOption', 'DocTitleConfig', 'ReasonForRetrieval', 'CategoryID'])
- GetObjectsList(Type=3): HTTP 200 (125 ms, keys=['AllItemsList'])
- GetDocument: HTTP 200 (458 ms, keys=['CheckOutStatus', 'DocNo', 'IndexData', 'StreamsInfo', 'AccessMask', 'RoleAccessMask'])
- GetDocumentIndexData: HTTP 200 (201 ms, keys=['DocNo', 'IndexData', 'AccessMask', 'RoleAccessMask'])
- GetDocumentProperties: HTTP 200 (135 ms, keys=['DocumentProperties'])
- GetDocumentHistory: HTTP 200 (180 ms, keys=['DocumentHistory'])
- GetDocumentCheckoutStatus: HTTP 200 (219 ms, keys=['CheckOutStatus'])
- PreprocessIndexData: HTTP 200 (145 ms, keys=['IndexData', 'CalculationResult'])
- EvaluateConditionalProperties: HTTP 200 (116 ms, keys=['FieldProperties', 'TabCtrlProperties', 'TableProperties'])
- CreateDocument: HTTP 200 (1732 ms, keys=['DocNo', 'LastChangeTime', 'VersionNo', 'LastChangeTimeISO8601'])
- DeleteDocument: HTTP 200 (432 ms, keys=[])