#!/usr/bin/env python3
"""Test script to query referenced table details."""

import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from therefore_client import ThereforeClient, ThereforeConfig

# Load environment from .env.local
env_path = Path(__file__).parent.parent / ".env.local"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key] = value

def main():
    # Initialize client for craigdemo tenant
    config = ThereforeConfig(
        base_url=os.environ.get("THEREFORE_CRAIGDEMO_BASE_URL"),
        auth_method=os.environ.get("THEREFORE_CRAIGDEMO_AUTH_METHOD", "Basic"),
        username=os.environ.get("THEREFORE_CRAIGDEMO_USERNAME"),
        password=os.environ.get("THEREFORE_CRAIGDEMO_PASSWORD"),
        tenant_name=os.environ.get("THEREFORE_CRAIGDEMO_TENANTNAME"),
    )

    if not config.base_url:
        print("Error: THEREFORE_CRAIGDEMO_BASE_URL not set in environment or .env.local")
        print(f"Looked for .env.local at: {env_path}")
        return

    client = ThereforeClient(config)

    print("Querying for referenced tables (Type=5)...")
    resp = client.get_objects(flags=0, obj_type=5)

    items = resp.get("ItemList", [])
    print(f"\nFound {len(items)} referenced tables:")
    print("-" * 80)

    staff_ids = []
    for item in items:
        item_id = item.get("ID")
        name = item.get("Name", "")
        print(f"  ID: {item_id:4d}  Name: {name}")
        if "staff" in name.lower():
            staff_ids.append((item_id, name))
            print(f"         ^ Found Staff table!")

    if staff_ids:
        for staff_id, staff_name in staff_ids:
            print(f"\nQuerying details for '{staff_name}' (ID {staff_id})...")
            print("-" * 80)
            info = client.get_referenced_table_info(staff_id)

            print(f"\nReferenced Table Info:")
            print(f"  DataTypeNo: {info.get('DataTypeNo')}")
            print(f"  Name: {info.get('Name')}")

            columns = info.get("Columns", [])
            print(f"\nColumns ({len(columns)}):")
            for col in columns:
                col_name = col.get("ColumnName", "")
                type_no = col.get("TypeNo", "")
                length = col.get("Length", "")
                visible = col.get("Visible", "")
                print(f"  - {col_name:30s} TypeNo: {str(type_no):3s}  Length: {str(length):4s}  Visible: {visible}")
            print()
    else:
        print("\nNo 'Staff' referenced table found.")
        print("Available tables listed above.")

if __name__ == "__main__":
    main()
