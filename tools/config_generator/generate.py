#!/usr/bin/env python3
"""Generate a Therefore configuration delta XML for a new category.

MVP scope:
- Category + folder creation
- Fields: text, number, decimal
- Keyword dictionaries: existing + new (single-select)
- Auto layout when not specified
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
import os
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
import xml.etree.ElementTree as ET

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

LCID_DEFAULT = "1033"

# Therefore configuration XML format version.  Taken from a known-good export;
# the importing server uses this to verify structural compatibility.
CONFIG_XML_VERSION = "570425345"
CONFIG_XML_NEW_IMPORT_EXPORT = "1"

# Therefore field size limits
TEXT_FIELD_MAX_LENGTH = 4000

# Therefore field type numbers
FIELD_TYPE_MULTI_KEYWORD = 193  # Multi-select keyword field TypeNo

# Therefore field index types
INDEX_TYPE_NONE = None  # No index (default) - element omitted
INDEX_TYPE_NORMAL = "1"  # Normal index - speeds up searching
INDEX_TYPE_UNIQUE = "2"  # Unique index - enforces uniqueness, makes field mandatory

# Therefore dependency modes for referenced table dependent fields
DEPENDENCY_MODE_REFERENCED = "0"  # Default - data pulled from source, not stored locally
DEPENDENCY_MODE_SYNCHRONIZED = "1"  # Synchronized redundant - copied locally, kept in sync (read-only)
DEPENDENCY_MODE_EDITABLE = "2"  # Editable redundant - copied locally, can be edited independently

# Therefore dynamic default values (run-time variables)
# Text field defaults
DEFAULT_USER = "<User>"
DEFAULT_USER_DISPLAY_NAME = "<User Display Name>"
DEFAULT_DOMAIN_USER = "<Domain\\User>"
DEFAULT_USER_EMAIL = "<User E-mail Address>"
DEFAULT_FILE_NAME = "<File Name>"
DEFAULT_FILE_TITLE = "<File Title>"
DEFAULT_FILE_EXTENSION = "<File Extension>"
DEFAULT_FILE_PATH = "<File Path>"
DEFAULT_FOLDER_PATH = "<Folder Path>"
DEFAULT_GUID = "<Guid>"

# Date field defaults
DEFAULT_DATE = "<Date>"
DEFAULT_FILE_CREATED = "<File Created>"
DEFAULT_FILE_MODIFIED = "<File Modified>"

# Date and Time field defaults (same as date plus timestamp)
DEFAULT_TIMESTAMP = "<Timestamp>"

# Checkbox defaults
DEFAULT_CHECKBOX_FALSE = "0"
DEFAULT_CHECKBOX_TRUE = "1"
# Empty/omitted = undefined


@dataclass
class DictionarySpec:
    mode: str  # "create" | "existing"
    name: str
    keywords: List[str] = field(default_factory=list)


@dataclass
class TableColumnSpec:
    name: str
    type: str  # text | number | decimal
    length: Optional[int] = None
    scale: Optional[int] = None


@dataclass
class ReferenceTableSpec:
    category_name: Optional[str] = None  # Referenced category name (resolved via API)
    category_no: Optional[int] = None     # Or explicit category number
    dependent_fields: List[str] = field(default_factory=list)  # Column names to include
    dependency_mode: str = DEPENDENCY_MODE_REFERENCED  # "0" (referenced), "1" (synchronized), "2" (editable)


@dataclass
class FieldSpec:
    name: str
    type: str  # text | number | decimal | date | keyword_single | keyword_multiple | table | reference_table
    length: Optional[int] = None
    scale: Optional[int] = None
    dictionary: Optional[DictionarySpec] = None
    columns: List[TableColumnSpec] = field(default_factory=list)
    index_type: Optional[str] = None  # None (no index), "1" (normal), "2" (unique)
    default_value: Optional[str] = None  # Static value or dynamic variable like "<User>". For keyword_multiple: comma-separated names
    mandatory: bool = False  # Required field (auto-set to True when index_type="2")
    reference_table: Optional[ReferenceTableSpec] = None  # For reference_table type


@dataclass
class CategorySpec:
    name: str
    folder: Optional[str] = None
    description: str = ""
    fields: List[FieldSpec] = field(default_factory=list)
    force_new_folder: bool = False
    folder_conflict_policy: str = "use-existing"  # error | use-existing | unique
    dictionary_conflict_policy: str = "use-existing"  # error | use-existing | unique


class NegativeIdPool:
    def __init__(self, start: int = -1) -> None:
        self.next_val = start

    def take(self) -> int:
        val = self.next_val
        self.next_val -= 1
        return val


class Layout:
    def __init__(self) -> None:
        self.field_x = 100
        self.label_x = 35
        self.field_h = 12
        self.label_h = 10
        self.label_offset_y = 2
        self.row_height = 22
        self.start_y = 12
        self.widths = {
            "text": 200,
            "number": 80,
            "decimal": 80,
            "date": 120,
            "keyword_single": 200,
            "keyword_multiple": 200,
            "table": 240,
            "reference_table": 200,
        }
        self.table_height = 43
        self.half_width = 120
        self.full_width = 220


# ------------------------- Parsing -------------------------


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if (text.startswith("\"") and text.endswith("\"")) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def _first_quoted(text: str) -> Optional[str]:
    m = re.search(r"\"([^\"]+)\"|'([^']+)'", text)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _quoted_after(text: str, keyword: str) -> Optional[str]:
    idx = text.lower().find(keyword.lower())
    if idx < 0:
        return None
    sub = text[idx + len(keyword):]
    return _first_quoted(sub)


def parse_natural_language(text: str) -> CategorySpec:
    # Normalize whitespace
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    joined = " ".join(lines)

    # Category name
    cat_name = None
    m = re.search(r"\bcreate\s+(?:an|a)\s+([^.]+?)\s+category\b", joined, re.IGNORECASE)
    if m:
        cat_name = m.group(1).strip()
        if cat_name:
            cat_name = cat_name[:1].upper() + cat_name[1:]
    # Only check for quoted category names if we haven't found one yet
    if not cat_name:
        m = re.search(r"\bcategory\b\s+\"([^\"]+)\"", joined, re.IGNORECASE)
        if m:
            cat_name = m.group(1)
        else:
            m = re.search(r"\bcategory\b\s+'([^']+)'", joined, re.IGNORECASE)
            if m:
                cat_name = m.group(1)
    if not cat_name:
        m = re.search(r"\bcategory\b\s+([^\.\n]+)", joined, re.IGNORECASE)
        if m:
            cat_name = m.group(1)
            if " in folder " in cat_name.lower():
                cat_name = cat_name.split(" in folder ", 1)[0]
            cat_name = cat_name.strip()
    if not cat_name:
        raise ValueError("Could not determine category name from description.")

    # Category description (optional)
    cat_description = ""
    m = re.search(r"\bdescription\s*[:\-]\s*\"([^\"]+)\"", joined, re.IGNORECASE)
    if m:
        cat_description = m.group(1)
    else:
        m = re.search(r"\bdescription\s*[:\-]\s*'([^']+)'", joined, re.IGNORECASE)
        if m:
            cat_description = m.group(1)

    # Folder name (optional)
    folder = None
    m = re.search(r"\bfolder\b[^\"]*\"([^\"]+)\"", joined, re.IGNORECASE)
    if m:
        folder = m.group(1)
    else:
        m = re.search(r"\bfolder\b[^']*'([^']+)'", joined, re.IGNORECASE)
        if m:
            folder = m.group(1)
    if not folder:
        m = re.search(r"\bin folder\b\s+([^\.\n]+)", joined, re.IGNORECASE)
        if m:
            folder = m.group(1).strip()
    if not folder:
        for ln in lines:
            if "folder" in ln.lower():
                q = _first_quoted(ln)
                if q:
                    folder = q
                    break

    force_new_folder = False
    folder_conflict_policy = "use-existing"
    dict_conflict_policy = "use-existing"
    if re.search(r"\bnew folder\b", joined, re.IGNORECASE):
        force_new_folder = True
    if re.search(r"\broot\b", joined, re.IGNORECASE):
        force_new_folder = True
    if re.search(r"\buse existing folder\b", joined, re.IGNORECASE):
        folder_conflict_policy = "use-existing"
        force_new_folder = False
    if re.search(r"\bunique folder\b", joined, re.IGNORECASE) or re.search(r"\bcreate unique\b", joined, re.IGNORECASE):
        folder_conflict_policy = "unique"
    if re.search(r"\buse existing dictionary\b", joined, re.IGNORECASE):
        dict_conflict_policy = "use-existing"
    if re.search(r"\bcreate unique dictionary\b", joined, re.IGNORECASE) or re.search(r"\bunique dictionary\b", joined, re.IGNORECASE):
        dict_conflict_policy = "unique"

    fields: List[FieldSpec] = []

    # Field lines: prefer bullet lines or any line containing "field"
    field_lines: List[str] = []
    for ln in lines:
        if re.search(r"\bfield\b", ln, re.IGNORECASE) or re.search(r"\bkeyword dictionary\b", ln, re.IGNORECASE) or re.search(r"\btable\b", ln, re.IGNORECASE):
            # Skip the main category line if it contains "field" by coincidence
            field_lines.append(ln)

    for ln in field_lines:
        ln_clean = ln.lstrip("-• ").strip()
        lower = ln_clean.lower()

        ftype = None
        if "reference table" in lower or "referenced table" in lower or "lookup field" in lower or "lookup table" in lower:
            ftype = "reference_table"
        elif "multiple keyword" in lower or "multi-select keyword" in lower or "multi keyword" in lower:
            ftype = "keyword_multiple"
        elif "keyword dictionary" in lower or "single keyword" in lower or "keyword" in lower:
            ftype = "keyword_single"
        elif "decimal" in lower:
            ftype = "decimal"
        elif "date" in lower:
            ftype = "date"
        elif "number" in lower:
            ftype = "number"
        elif "text" in lower:
            ftype = "text"
        elif "table" in lower and ("rows" in lower or "columns" in lower):
            ftype = "table"

        if not ftype:
            continue

        # Field name
        name = _first_quoted(ln_clean)
        if not name:
            # remove leading type phrase
            name = re.sub(r"^(text|number|decimal|keyword)\s+field\s+", "", ln_clean, flags=re.IGNORECASE).strip()
            name = name.split(" with ", 1)[0].split(" using ", 1)[0].strip()
            name = _strip_quotes(name)

        length = None
        scale = None
        m = re.search(r"\blength\b\s*(\d+)", lower)
        if m:
            length = int(m.group(1))
        m = re.search(r"\bscale\b\s*(\d+)", lower)
        if m:
            scale = int(m.group(1))

        dict_spec = None
        if ftype in ("keyword_single", "keyword_multiple"):
            mode = "existing"
            if "keyword dictionary" in lower:
                mode = "create"
            if "new dictionary" in lower or "create dictionary" in lower:
                mode = "create"
                dict_name = _quoted_after(ln_clean, "new dictionary") or _quoted_after(ln_clean, "create dictionary")
            else:
                dict_name = _quoted_after(ln_clean, "existing dictionary") or _quoted_after(ln_clean, "use dictionary")
            if not dict_name and "keyword dictionary" not in lower:
                # fallback: second quoted string for "field \"X\" with dictionary \"Y\""
                quotes = re.findall(r"\"([^\"]+)\"|'([^']+)'", ln_clean)
                if len(quotes) >= 2:
                    dict_name = quotes[1][0] or quotes[1][1]
            if not dict_name:
                dict_name = name

            keywords: List[str] = []
            m = re.search(r"keywords?\s*:\s*(.+)", ln_clean, re.IGNORECASE)
            if not m:
                m = re.search(r"values?\s*(?:of)?\s*:?\s*(.+)", ln_clean, re.IGNORECASE)
            if m:
                kw_text = m.group(1)
                parts = [k.strip() for k in kw_text.split(",") if k.strip()]
                keywords = []
                for part in parts:
                    if " and " in part:
                        subparts = [s.strip() for s in part.split(" and ") if s.strip()]
                        keywords.extend(subparts)
                    else:
                        keywords.append(part)
                # strip quotes
                keywords = [_strip_quotes(k) for k in keywords]

            dict_spec = DictionarySpec(mode=mode, name=dict_name, keywords=keywords)

        # Parse index type
        index_type = None
        if "unique index" in lower or "uniquely indexed" in lower:
            index_type = INDEX_TYPE_UNIQUE
        elif "normal index" in lower or " indexed" in lower or "with index" in lower:
            index_type = INDEX_TYPE_NORMAL

        # Parse mandatory
        mandatory = "mandatory" in lower or "required" in lower

        # Parse default value
        default_value = None
        # Check for dynamic variables
        if "default" in lower or "default value" in lower:
            if "user display name" in lower or "display name" in lower:
                default_value = DEFAULT_USER_DISPLAY_NAME
            elif "domain\\user" in lower or "domain user" in lower:
                default_value = DEFAULT_DOMAIN_USER
            elif "user email" in lower or "user e-mail" in lower or "email address" in lower:
                default_value = DEFAULT_USER_EMAIL
            elif "file name" in lower or "filename" in lower:
                default_value = DEFAULT_FILE_NAME
            elif "file title" in lower:
                default_value = DEFAULT_FILE_TITLE
            elif "file extension" in lower:
                default_value = DEFAULT_FILE_EXTENSION
            elif "file path" in lower:
                default_value = DEFAULT_FILE_PATH
            elif "folder path" in lower:
                default_value = DEFAULT_FOLDER_PATH
            elif "guid" in lower:
                default_value = DEFAULT_GUID
            elif "timestamp" in lower:
                default_value = DEFAULT_TIMESTAMP
            elif "file created" in lower:
                default_value = DEFAULT_FILE_CREATED
            elif "file modified" in lower:
                default_value = DEFAULT_FILE_MODIFIED
            elif ftype == "date" and ("date" in lower or "today" in lower):
                default_value = DEFAULT_DATE
            elif "user" in lower and "default" in lower:
                default_value = DEFAULT_USER
            else:
                # Try to extract a quoted default value
                m = re.search(r"default\s+(?:value\s+)?['\"]([^'\"]+)['\"]", lower)
                if m:
                    default_value = m.group(1)

        columns: List[TableColumnSpec] = []
        if ftype == "table":
            # Extract column names from quoted strings after 'rows'/'columns'
            # If a table name is provided, use it; otherwise default.
            table_name = _quoted_after(ln_clean, "table called") or _quoted_after(ln_clean, "table named")
            if not table_name:
                table_name = "Line Items"
            name = table_name
            quoted = re.findall(r"\"([^\"]+)\"|'([^']+)'", ln_clean)
            col_names = [q[0] or q[1] for q in quoted]
            # If table name was included in quotes, remove it from column list
            if table_name in col_names and len(col_names) > 1:
                col_names = [c for c in col_names if c != table_name]
            if not col_names:
                raise ValueError("Table field requires column names in quotes.")
            for col in col_names:
                col_lower = col.lower()
                if "qty" in col_lower or "quantity" in col_lower:
                    col_type = "number"
                elif "total" in col_lower or "amount" in col_lower or "value" in col_lower or "price" in col_lower:
                    col_type = "decimal"
                else:
                    col_type = "text"
                columns.append(TableColumnSpec(name=col, type=col_type))

        # Parse reference_table
        ref_table_spec = None
        if ftype == "reference_table":
            # Extract referenced category name: "reference table 'User' from category 'Users' with columns 'Name', 'Email'"
            # Or: "lookup field 'Supplier' from 'Suppliers' with 'Name', 'ABN'"
            ref_category_name = None
            dependent_fields = []

            # Try to find "from category 'X'" or "from 'X'"
            m = re.search(r"from\s+category\s+['\"]([^'\"]+)['\"]", ln_clean, re.IGNORECASE)
            if m:
                ref_category_name = m.group(1)
            else:
                m = re.search(r"from\s+['\"]([^'\"]+)['\"]", ln_clean, re.IGNORECASE)
                if m:
                    ref_category_name = m.group(1)

            # Try to find dependent fields in quotes after "with columns" or "columns"
            # Extract all quoted strings after "with" or "columns"
            after_with = None
            m = re.search(r"with\s+(?:columns?\s+)?(.+)", ln_clean, re.IGNORECASE)
            if m:
                after_with = m.group(1)
            elif "columns" in lower:
                m = re.search(r"columns?\s+(.+)", ln_clean, re.IGNORECASE)
                if m:
                    after_with = m.group(1)

            if after_with:
                quoted = re.findall(r"['\"]([^'\"]+)['\"]", after_with)
                if quoted:
                    dependent_fields = quoted

            # Parse dependency mode
            dependency_mode = DEPENDENCY_MODE_REFERENCED  # Default
            if "synchronized redundant" in lower or "synchronized" in lower:
                dependency_mode = DEPENDENCY_MODE_SYNCHRONIZED
            elif "editable redundant" in lower or "editable" in lower:
                dependency_mode = DEPENDENCY_MODE_EDITABLE

            if ref_category_name:
                ref_table_spec = ReferenceTableSpec(
                    category_name=ref_category_name,
                    dependent_fields=dependent_fields,
                    dependency_mode=dependency_mode
                )

        fields.append(FieldSpec(name=name, type=ftype, length=length, scale=scale, dictionary=dict_spec, columns=columns, index_type=index_type, default_value=default_value, mandatory=mandatory, reference_table=ref_table_spec))

    if not fields:
        raise ValueError("No fields detected in description. Include lines with 'text field', 'number field', etc.")

    return CategorySpec(
        name=cat_name,
        folder=folder,
        description=cat_description,
        fields=fields,
        force_new_folder=force_new_folder,
        folder_conflict_policy=folder_conflict_policy,
        dictionary_conflict_policy=dict_conflict_policy,
    )


def parse_description(path_or_text: str) -> CategorySpec:
    text = path_or_text
    p = Path(path_or_text)
    if p.exists():
        text = p.read_text()

    stripped = text.strip()
    if stripped.startswith("{"):
        data = json.loads(text)
        return spec_from_mapping(data)

    # YAML if looks like YAML and PyYAML is present
    if ("category:" in stripped.splitlines()[0].lower() or stripped.startswith("category:")) and yaml:
        data = yaml.safe_load(text)
        return spec_from_mapping(data)

    return parse_natural_language(text)


def spec_from_mapping(data: Dict[str, Any]) -> CategorySpec:
    if not isinstance(data, dict):
        raise ValueError("Spec must be a mapping (dict).")
    cat = data.get("category", {})
    name = cat.get("name") or data.get("name")
    if not name:
        raise ValueError("Spec missing category.name")
    folder = cat.get("folder")
    description = cat.get("description", "")
    force_new_folder = bool(cat.get("force_new_folder") or cat.get("new_folder") or cat.get("create_new_folder"))
    folder_conflict_policy = cat.get("folder_conflict_policy") or cat.get("folder_conflict") or "use-existing"
    dict_conflict_policy = cat.get("dictionary_conflict_policy") or cat.get("dictionary_conflict") or "use-existing"

    fields_in = data.get("fields", [])
    fields: List[FieldSpec] = []
    for f in fields_in:
        ftype = f.get("type")
        if not ftype:
            raise ValueError("Field missing type")
        dict_spec = None
        if ftype in ("keyword_single", "keyword", "keyword_multiple", "multi_keyword"):
            d = f.get("dictionary") or {}
            mode = d.get("mode", "existing")
            dname = d.get("name") or f.get("name")
            keywords = d.get("keywords", []) or []
            dict_spec = DictionarySpec(mode=mode, name=dname, keywords=keywords)
            if ftype in ("keyword_multiple", "multi_keyword"):
                ftype = "keyword_multiple"
            else:
                ftype = "keyword_single"
        columns = []
        if ftype == "table":
            cols_in = f.get("columns", []) or []
            for c in cols_in:
                columns.append(TableColumnSpec(
                    name=c.get("name"),
                    type=c.get("type", "text"),
                    length=c.get("length"),
                    scale=c.get("scale"),
                ))

        ref_table_spec = None
        if ftype == "reference_table":
            ref_in = f.get("reference_table") or {}
            # Parse dependency_mode - accept string name or numeric value
            dep_mode_raw = ref_in.get("dependency_mode") or ref_in.get("mode")
            dependency_mode = DEPENDENCY_MODE_REFERENCED  # Default
            if dep_mode_raw:
                if str(dep_mode_raw).lower() in ("synchronized", "synchronized redundant", "sync", "1"):
                    dependency_mode = DEPENDENCY_MODE_SYNCHRONIZED
                elif str(dep_mode_raw).lower() in ("editable", "editable redundant", "edit", "2"):
                    dependency_mode = DEPENDENCY_MODE_EDITABLE
                elif str(dep_mode_raw) in ("0", "referenced"):
                    dependency_mode = DEPENDENCY_MODE_REFERENCED
            ref_table_spec = ReferenceTableSpec(
                category_name=ref_in.get("category_name"),
                category_no=ref_in.get("category_no") or ref_in.get("category_id"),
                dependent_fields=ref_in.get("dependent_fields") or ref_in.get("columns") or [],
                dependency_mode=dependency_mode
            )

        fields.append(FieldSpec(
            name=f.get("name"),
            type=ftype,
            length=f.get("length"),
            scale=f.get("scale"),
            dictionary=dict_spec,
            columns=columns,
            reference_table=ref_table_spec,
            index_type=f.get("index_type") or f.get("index"),
            default_value=f.get("default_value") or f.get("default"),
            mandatory=bool(f.get("mandatory") or f.get("required")),
        ))

    return CategorySpec(
        name=name,
        folder=folder,
        description=description,
        fields=fields,
        force_new_folder=force_new_folder,
        folder_conflict_policy=folder_conflict_policy,
        dictionary_conflict_policy=dict_conflict_policy,
    )


# ------------------------- XML helpers -------------------------


def _tstr_element(tag: str, text: str) -> ET.Element:
    el = ET.Element(tag, {"UPT": "1"})
    tstr = ET.SubElement(el, "TStr")
    if text:
        t = ET.SubElement(tstr, "T")
        l = ET.SubElement(t, "L")
        l.text = LCID_DEFAULT
        s = ET.SubElement(t, "S")
        s.text = text
    return el


def _empty_tstr(tag: str) -> ET.Element:
    el = ET.Element(tag, {"UPT": "1"})
    ET.SubElement(el, "TStr")
    return el


def _slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "Field"


def _guid() -> str:
    return str(uuid.uuid4()).upper()


def _is_half_width_text(name: str) -> bool:
    n = name.lower()
    if re.search(r"\b(no|number|id|code|po|po#)\b", n):
        return True
    if "invoice" in n and ("no" in n or "number" in n):
        return True
    if "purchase" in n and ("no" in n or "number" in n):
        return True
    return False


def _field_width_for(name: str, ftype: str, layout: Layout) -> int:
    if ftype in ("number", "decimal", "date"):
        return layout.half_width
    if ftype in ("keyword_single", "keyword_multiple", "reference_table"):
        if "status" in name.lower() or "state" in name.lower():
            return layout.half_width + 10
        return layout.full_width
    if ftype == "text":
        return layout.half_width if _is_half_width_text(name) else layout.full_width
    return layout.widths.get(ftype, layout.full_width)


def _column_width_for(name: str, col_type: str) -> int:
    n = name.lower()
    if "description" in n:
        return 240
    if "item" in n and "code" in n:
        return 120
    if "qty" in n or "quantity" in n:
        return 70
    if "total" in n or "amount" in n or "value" in n or "price" in n:
        return 100
    if col_type == "number":
        return 80
    if col_type == "decimal":
        return 100
    return 140


def _add_common_tail(parent: ET.Element) -> None:
    # RegExHelp, Links, Id, DisplayProp, TabInfo, FieldID, DisplayPropCond, Filter
    parent.append(_empty_tstr("RegExHelp"))
    ET.SubElement(parent, "Links")
    ET.SubElement(parent, "Id").text = _guid()
    ET.SubElement(parent, "DisplayProp")
    ET.SubElement(parent, "TabInfo", {"FactoryType": "0"})
    # FieldID must be set by caller after this helper
    # DisplayPropCond + Filter
    ET.SubElement(parent, "DisplayPropCond")
    ET.SubElement(parent, "Filter")


def _insert_field_id(parent: ET.Element, field_id: str) -> None:
    # Insert FieldID before DisplayPropCond if present, else append
    # Find DisplayPropCond index
    children = list(parent)
    idx = None
    for i, ch in enumerate(children):
        if ch.tag == "DisplayPropCond":
            idx = i
            break
    field_id_el = ET.Element("FieldID")
    field_id_el.text = field_id
    if idx is None:
        parent.append(field_id_el)
    else:
        parent.insert(idx, field_id_el)


def _make_label_field(field_no: int, caption: str, field_id: str, pos_x: int, pos_y: int, width: int, height: int) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    f.append(_tstr_element("Caption", caption))
    ET.SubElement(f, "TypeNo").text = "4"
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    f.append(_empty_tstr("RegExHelp"))
    ET.SubElement(f, "Links")
    ET.SubElement(f, "Id").text = _guid()
    ET.SubElement(f, "DisplayProp")
    ET.SubElement(f, "TabInfo", {"FactoryType": "0"})
    ET.SubElement(f, "FieldID").text = field_id
    ET.SubElement(f, "DisplayPropCond")
    ET.SubElement(f, "Filter")
    ET.SubElement(f, "FullTextSearch").text = "0"
    return f


def _make_text_field(field_no: int, name: str, length: int, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int, default_val: Optional[str] = None, index_type: Optional[str] = None, mandatory: bool = False) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = "1"
    if index_type:
        ET.SubElement(f, "IndexType").text = index_type
    ET.SubElement(f, "Length").text = str(length)
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    if mandatory or index_type == INDEX_TYPE_UNIQUE:
        ET.SubElement(f, "Mandatory").text = "1"
    ET.SubElement(f, "DontLoadValues").text = "1"
    if default_val:
        ET.SubElement(f, "DefaultVal").text = default_val
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    _add_common_tail(f)
    _insert_field_id(f, _slugify(name))
    return f


def _make_number_field(field_no: int, name: str, length: int, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int, default_val: Optional[str] = None, index_type: Optional[str] = None, mandatory: bool = False) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = "2"
    if index_type:
        ET.SubElement(f, "IndexType").text = index_type
    ET.SubElement(f, "Length").text = str(length)
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    if mandatory or index_type == INDEX_TYPE_UNIQUE:
        ET.SubElement(f, "Mandatory").text = "1"
    ET.SubElement(f, "DontLoadValues").text = "1"
    if default_val:
        ET.SubElement(f, "DefaultVal").text = default_val
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    _add_common_tail(f)
    _insert_field_id(f, _slugify(name))
    return f


def _make_decimal_field(field_no: int, name: str, length: int, scale: int, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int, default_val: Optional[str] = None, index_type: Optional[str] = None, mandatory: bool = False) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = "5"
    if index_type:
        ET.SubElement(f, "IndexType").text = index_type
    ET.SubElement(f, "Length").text = str(length)
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    if mandatory or index_type == INDEX_TYPE_UNIQUE:
        ET.SubElement(f, "Mandatory").text = "1"
    ET.SubElement(f, "DontLoadValues").text = "1"
    if default_val:
        ET.SubElement(f, "DefaultVal").text = default_val
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    _add_common_tail(f)
    ET.SubElement(f, "Scale").text = str(scale)
    _insert_field_id(f, _slugify(name))
    return f


def _make_date_field(field_no: int, name: str, length: int, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int, default_val: Optional[str] = None, index_type: Optional[str] = None, mandatory: bool = False) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = "3"
    if index_type:
        ET.SubElement(f, "IndexType").text = index_type
    ET.SubElement(f, "Length").text = str(length)
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    if mandatory or index_type == INDEX_TYPE_UNIQUE:
        ET.SubElement(f, "Mandatory").text = "1"
    ET.SubElement(f, "DontLoadValues").text = "1"
    if default_val:
        ET.SubElement(f, "DefaultVal").text = default_val
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    _add_common_tail(f)
    _insert_field_id(f, _slugify(name))
    return f


def _make_keyword_hidden_field(field_no: int, name: str, type_no: int) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name)
    f.append(_tstr_element("Caption", f"{name}No"))
    ET.SubElement(f, "TypeNo").text = str(type_no)
    ET.SubElement(f, "Length").text = "250"
    ET.SubElement(f, "Width").text = "0"
    ET.SubElement(f, "Height").text = "0"
    ET.SubElement(f, "PosX").text = "0"
    ET.SubElement(f, "PosY").text = "0"
    ET.SubElement(f, "Visible").text = "0"
    f.append(_empty_tstr("RegExHelp"))
    ET.SubElement(f, "Links")
    ET.SubElement(f, "Id").text = _guid()
    ET.SubElement(f, "DisplayProp")
    ET.SubElement(f, "SelFromDropDownBox").text = "1"
    ET.SubElement(f, "TabInfo", {"FactoryType": "0"})
    ET.SubElement(f, "FieldID").text = _slugify(name)
    ET.SubElement(f, "DisplayPropCond")
    ET.SubElement(f, "Filter")
    return f


def _make_keyword_text_field(field_no: int, name: str, belongs_to: int, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = "1"
    ET.SubElement(f, "Length").text = "250"
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "BelongsTo").text = str(belongs_to)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    ET.SubElement(f, "ForeignCol").text = "Keyword"
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    f.append(_empty_tstr("RegExHelp"))
    ET.SubElement(f, "Links")
    ET.SubElement(f, "Id").text = _guid()
    ET.SubElement(f, "DisplayProp")
    ET.SubElement(f, "SelFromDropDownBox").text = "1"
    ET.SubElement(f, "TabInfo", {"FactoryType": "0"})
    ET.SubElement(f, "FieldID").text = _slugify(name) + "_Text"
    ET.SubElement(f, "DisplayPropCond")
    ET.SubElement(f, "Filter")
    return f


def _make_multi_keyword_field(field_no: int, name: str, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int, default_val: Optional[str] = None, index_type: Optional[str] = None, mandatory: bool = False) -> ET.Element:
    """Create a multi-select keyword field (TypeNo 193)."""
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = str(FIELD_TYPE_MULTI_KEYWORD)
    if index_type:
        ET.SubElement(f, "IndexType").text = index_type
    ET.SubElement(f, "Length").text = "250"
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    if mandatory or index_type == INDEX_TYPE_UNIQUE:
        ET.SubElement(f, "Mandatory").text = "1"
    ET.SubElement(f, "DontLoadValues").text = "1"
    if default_val:
        ET.SubElement(f, "DefaultVal").text = default_val
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    _add_common_tail(f)
    _insert_field_id(f, _slugify(name))
    return f


def _make_table_field(field_no: int, name: str, foreign_table: str, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = "10"
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    ET.SubElement(f, "DontLoadValues").text = "1"
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    ET.SubElement(f, "ForeignTable").text = foreign_table
    _add_common_tail(f)
    _insert_field_id(f, _slugify(name))
    return f


def _make_primary_reference_field(field_no: int, name: str, ref_category_id: int, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int, index_type: Optional[str] = None, mandatory: bool = False) -> ET.Element:
    """Create a primary reference field (TypeNo = referenced category ID)."""
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name) + "No"
    f.append(_tstr_element("Caption", name + "No"))
    ET.SubElement(f, "TypeNo").text = str(ref_category_id)
    if index_type:
        ET.SubElement(f, "IndexType").text = index_type
    ET.SubElement(f, "Length").text = "250"
    ET.SubElement(f, "Width").text = "0"
    ET.SubElement(f, "Height").text = "0"
    ET.SubElement(f, "PosX").text = "0"
    ET.SubElement(f, "PosY").text = "0"
    ET.SubElement(f, "Visible").text = "0"  # Hidden field
    if mandatory or index_type == INDEX_TYPE_UNIQUE:
        ET.SubElement(f, "Mandatory").text = "1"
    f.append(_empty_tstr("RegExHelp"))
    ET.SubElement(f, "Links")
    ET.SubElement(f, "Id").text = _guid()
    ET.SubElement(f, "DisplayProp")
    ET.SubElement(f, "TabInfo", {"FactoryType": "0"})
    ET.SubElement(f, "FieldID").text = _slugify(name) + "No"
    ET.SubElement(f, "DisplayPropCond")
    ET.SubElement(f, "Filter")
    return f


def _make_dependent_reference_field(field_no: int, name: str, column_name: str, belongs_to: int, pos_x: int, pos_y: int, tab_order: int, disp_order: int, width: int, height: int, dependency_mode: str = DEPENDENCY_MODE_REFERENCED) -> ET.Element:
    """Create a dependent field that pulls data from the referenced table."""
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    f.append(_tstr_element("Caption", name))
    ET.SubElement(f, "TypeNo").text = "1"  # Text field
    ET.SubElement(f, "Length").text = "250"
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = str(height)
    ET.SubElement(f, "PosX").text = str(pos_x)
    ET.SubElement(f, "PosY").text = str(pos_y)
    ET.SubElement(f, "BelongsTo").text = str(belongs_to)
    ET.SubElement(f, "TabOrderPos").text = str(tab_order)
    ET.SubElement(f, "ForeignCol").text = column_name
    # Add DependencyMode if not default (0 = Referenced)
    if dependency_mode != DEPENDENCY_MODE_REFERENCED:
        ET.SubElement(f, "DependencyMode").text = dependency_mode
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    f.append(_empty_tstr("RegExHelp"))
    ET.SubElement(f, "Links")
    ET.SubElement(f, "Id").text = _guid()
    ET.SubElement(f, "DisplayProp")
    ET.SubElement(f, "TabInfo", {"FactoryType": "0"})
    ET.SubElement(f, "FieldID").text = _slugify(name)
    ET.SubElement(f, "DisplayPropCond")
    ET.SubElement(f, "Filter")
    return f


def _make_table_column_field(
    field_no: int,
    name: str,
    col_type: str,
    belongs_to: int,
    disp_order: int,
    length: Optional[int],
    scale: Optional[int],
    width: int,
) -> ET.Element:
    f = ET.Element("Field")
    ET.SubElement(f, "FieldNo").text = str(field_no)
    ET.SubElement(f, "ColName").text = _slugify(name)
    f.append(_tstr_element("Caption", name))
    if col_type == "number":
        type_no = "2"
    elif col_type == "decimal":
        type_no = "5"
    else:
        type_no = "1"
    ET.SubElement(f, "TypeNo").text = type_no
    if type_no in ("1", "2", "5"):
        if type_no == "1":
            default_len = 50
        else:
            default_len = 10
        final_len = length or default_len
        if type_no == "1" and final_len > TEXT_FIELD_MAX_LENGTH:
            raise ValueError(
                f"Table column '{name}' length {final_len} exceeds Therefore maximum of {TEXT_FIELD_MAX_LENGTH}"
            )
        ET.SubElement(f, "Length").text = str(final_len)
    ET.SubElement(f, "Width").text = str(width)
    ET.SubElement(f, "Height").text = "0"
    ET.SubElement(f, "PosX").text = "0"
    ET.SubElement(f, "PosY").text = "0"
    ET.SubElement(f, "DontLoadValues").text = "1"
    ET.SubElement(f, "DispOrderPos").text = str(disp_order)
    ET.SubElement(f, "BelongsToTable").text = str(belongs_to)
    _add_common_tail(f)
    ET.SubElement(f, "ParentFieldType").text = "2"
    if type_no == "5" and scale is not None:
        ET.SubElement(f, "Scale").text = str(scale)
    _insert_field_id(f, _slugify(name))
    return f


# ------------------------- XML generation -------------------------


def _find_baseline_version(root: ET.Element) -> Dict[str, str]:
    return {
        "Version": root.findtext("Version") or "",
        "NewImportExport": root.findtext("NewImportExport") or "1",
    }


def _find_folder_by_name(root: ET.Element, name: str) -> Optional[str]:
    def _tstr_value(elem: ET.Element) -> str:
        tstr = elem.find("TStr")
        if tstr is not None:
            for t in tstr.findall("T"):
                s = t.findtext("S")
                if s:
                    return s
        return elem.text or ""

    folders = root.find("Folders")
    if folders is None:
        return None

    matches = []
    for folder in folders.findall("Folder"):
        name_el = folder.find("Name")
        if name_el is None:
            continue
        fname = _tstr_value(name_el)
        if fname.lower() == name.lower():
            fno = folder.findtext("FolderNo") or folder.findtext("Folder")
            parent = folder.findtext("Parent") or folder.findtext("ParentNo")
            matches.append((fno, parent))

    if not matches:
        return None

    # prefer root-level
    for fno, parent in matches:
        if not parent or parent == "0":
            return fno
    return matches[0][0]


def _folder_name_exists(root: ET.Element, name: str) -> bool:
    return _find_folder_by_name(root, name) is not None


def _unique_folder_name(root: ET.Element, base_name: str) -> str:
    if not _folder_name_exists(root, base_name):
        return base_name
    # Try numeric suffixes
    i = 2
    while True:
        candidate = f"{base_name} ({i})"
        if not _folder_name_exists(root, candidate):
            return candidate
        i += 1


def _load_env_file(path: Optional[str]) -> Dict[str, str]:
    env = dict(os.environ)
    if not path:
        return env
    p = Path(path)
    if not p.exists():
        return env
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("\"").strip("'")
        env[key] = val
    return env


def _build_api_client(env_path: Optional[str], tenant: Optional[str]) -> Optional[Any]:
    # Lazy import to avoid hard dependency when not using API checks.
    try:
        repo_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(repo_root / "src"))
        from therefore_client import (  # type: ignore
            build_tenant_configs_from_env,
            normalize_tenant_key,
            ThereforeClient,
        )
    except Exception:
        return None

    env = _load_env_file(env_path)
    configs, default_tenant, _ = build_tenant_configs_from_env(env)
    tenant_key = normalize_tenant_key(tenant) if tenant else default_tenant
    if not tenant_key or tenant_key not in configs:
        return None
    return ThereforeClient(configs[tenant_key])


def _find_folder_by_name_api(client: Any, name: str) -> Optional[str]:
    try:
        resp = client.get_objects_list([
            {
                "Flags": 0,
                "Type": 0,
                "RoleAccessMask": 18446744073709551615,
            }
        ])
    except Exception:
        return None
    all_items = resp.get("AllItemsList") or []
    for entry in all_items:
        folder_list = entry.get("FolderList") or []
        for folder in folder_list:
            fname = folder.get("Name") or ""
            if fname.lower() == name.lower():
                return str(folder.get("FolderNo")) if folder.get("FolderNo") is not None else None
    return None


def _find_category_by_name(root: ET.Element, name: str) -> Optional[str]:
    def _tstr_value(elem: ET.Element) -> str:
        tstr = elem.find("TStr")
        if tstr is not None:
            for t in tstr.findall("T"):
                s = t.findtext("S")
                if s:
                    return s
        return elem.text or ""

    cats = root.find("Categories")
    if cats is None:
        return None
    for cat in cats.findall("Category"):
        name_el = cat.find("Name")
        if name_el is None:
            continue
        cname = _tstr_value(name_el)
        if cname.lower() == name.lower():
            return cat.findtext("CtgryNo")
    return None


def _find_dictionary_by_name(root: ET.Element, name: str) -> Optional[Dict[str, str]]:
    dictionaries = root.find("KeywordDictionaries")
    if dictionaries is None:
        return None
    for kd in dictionaries.findall("Dictionary"):
        dname = kd.findtext("KeyDicName") or kd.findtext("Name")
        if dname and dname.lower() == name.lower():
            return {
                "KeyDicNo": kd.findtext("KeyDicNo") or "",
                "SingleTypeNo": kd.findtext("SingleTypeNo") or "",
            }
    return None


def _find_referenced_table_by_name_api(client: Any, name: str) -> Optional[int]:
    """Find a referenced table (Type 5) by name and return its ID."""
    try:
        resp = client.get_objects(flags=0, obj_type=5)
    except Exception:
        return None

    item_list = resp.get("ItemList") or []
    for item in item_list:
        iname = item.get("Name") or ""
        if iname.lower() == name.lower():
            return item.get("ID")
    return None


def _get_referenced_table_info_api(client: Any, data_type_no: int) -> Optional[Dict[str, Any]]:
    """Get column information for a referenced table."""
    try:
        return client.get_referenced_table_info(data_type_no)
    except Exception:
        return None


def _find_dictionary_by_name_api(client: Any, name: str) -> Optional[Dict[str, str]]:
    try:
        # Type=22 corresponds to keyword dictionaries (KeyDict)
        resp = client.get_objects_list([
            {
                "Flags": 0,
                "Type": 22,
                "RoleAccessMask": 18446744073709551615,
            }
        ])
    except Exception:
        return None

    all_items = resp.get("AllItemsList") or []
    for entry in all_items:
        item_list = entry.get("ItemList") or []
        for item in item_list:
            iname = item.get("Name") or ""
            if iname.lower() != name.lower():
                continue
            dic_id = item.get("ID") or item.get("ObjID")
            if dic_id is None:
                continue
            try:
                dic_id_int = int(dic_id)
            except Exception:
                continue
            try:
                info = client.call_endpoint("GetDictionaryInfo", {"ByDictionaryID": dic_id_int})
            except Exception:
                continue
            d = info.get("Dictionary") if isinstance(info, dict) else None
            if not d:
                continue
            single_type = d.get("SingleTypeNo")
            if single_type is None:
                continue
            return {
                "KeyDicNo": str(d.get("KeywordDictionaryNo") or d.get("KeyDicNo") or dic_id_int),
                "SingleTypeNo": str(single_type),
            }
    return None


def _unique_dictionary_name(base_root: ET.Element, base_name: str) -> str:
    # Use baseline dictionaries to avoid collisions by name
    existing = set()
    dictionaries = base_root.find("KeywordDictionaries")
    if dictionaries is not None:
        for kd in dictionaries.findall("Dictionary"):
            dname = kd.findtext("KeyDicName") or kd.findtext("Name")
            if dname:
                existing.add(dname.lower())
    if base_name.lower() not in existing:
        return base_name
    i = 2
    while True:
        candidate = f"{base_name} ({i})"
        if candidate.lower() not in existing:
            return candidate
        i += 1


def _next_table_suffix(root: ET.Element, prefix: str) -> int:
    max_val = 0
    for elem in root.iter():
        if elem.text and elem.text.startswith(prefix):
            suffix = elem.text[len(prefix):]
            if suffix.isdigit():
                max_val = max(max_val, int(suffix))
    return max_val + 1


def build_delta_xml(
    spec: CategorySpec,
    baseline_path: Optional[str] = None,
    api_client: Optional[Any] = None,
    interactive: bool = False,
) -> ET.ElementTree:
    # When a baseline is provided, use it for collision checks and suffix numbering.
    # Otherwise generate a fully self-contained delta XML.
    base_root = None
    if baseline_path:
        base_root = ET.fromstring(Path(baseline_path).read_text(errors="ignore"))
        if _find_category_by_name(base_root, spec.name):
            raise ValueError(f"Category '{spec.name}' already exists in baseline export.")

    # ID pools (separate ranges to avoid collisions across object types)
    cat_ids = NegativeIdPool(-100)
    field_ids = NegativeIdPool(-1000)
    dict_ids = NegativeIdPool(-2000)
    dict_type_ids = NegativeIdPool(-3000)
    folder_ids = NegativeIdPool(-4000)

    folder_no = None
    new_folder_elem = None
    existing_folder_no = None
    folder_found_via_api = False
    if spec.folder and api_client is not None:
        existing_folder_no = _find_folder_by_name_api(api_client, spec.folder)
        if existing_folder_no:
            folder_found_via_api = True
    if spec.folder and not existing_folder_no and base_root is not None:
        existing_folder_no = _find_folder_by_name(base_root, spec.folder)

    if spec.folder and not spec.force_new_folder and existing_folder_no:
        folder_no = existing_folder_no

    if spec.force_new_folder and existing_folder_no:
        if spec.folder_conflict_policy == "use-existing":
            folder_no = existing_folder_no
        elif spec.folder_conflict_policy == "unique" and base_root is not None:
            spec.folder = _unique_folder_name(base_root, spec.folder)
        else:
            if interactive and folder_found_via_api and sys.stdin.isatty():
                prompt = (
                    f"Folder '{spec.folder}' already exists. Choose: "
                    "[u]se existing, [n]ew unique name, [a]bort: "
                )
                choice = input(prompt).strip().lower()
                if choice in ("u", "use", "existing"):
                    folder_no = existing_folder_no
                elif choice in ("n", "new", "unique") and base_root is not None:
                    spec.folder = _unique_folder_name(base_root, spec.folder)
                else:
                    raise ValueError("Aborted by user.")
            elif base_root is not None:
                raise ValueError(
                    f"Folder '{spec.folder}' already exists in baseline export. "
                    "Specify 'use existing folder' or 'create unique folder name' in the description, "
                    "set folder_conflict_policy in a YAML spec, or run with --api-check --interactive."
                )

    if not folder_no:
        # create folder if missing or not specified
        folder_name = spec.folder or "Generated"
        folder_no = str(folder_ids.take())
        new_folder_elem = ET.Element("Folder")
        ET.SubElement(new_folder_elem, "FolderNo").text = folder_no
        ET.SubElement(new_folder_elem, "Type").text = "3"
        new_folder_elem.append(_tstr_element("Name", folder_name))
        ET.SubElement(new_folder_elem, "Id").text = _guid()

    # Build keyword dictionaries (new only)
    new_dicts: List[ET.Element] = []
    dict_type_map: Dict[str, int] = {}
    next_keywords_suffix = _next_table_suffix(base_root, "TheKeywords") if base_root is not None else 1

    def ensure_dictionary(dspec: DictionarySpec) -> int:
        existing_info = None
        if base_root is not None:
            existing_info = _find_dictionary_by_name(base_root, dspec.name)
        existing_found_via_api = False
        if not existing_info and api_client is not None:
            existing_info = _find_dictionary_by_name_api(api_client, dspec.name)
            if existing_info:
                existing_found_via_api = True

        if dspec.mode == "existing":
            if existing_info:
                return int(existing_info["SingleTypeNo"])
            raise ValueError(
                f"Dictionary '{dspec.name}' not found. "
                "Use --api-check to query the tenant, or provide a --baseline export that includes it."
            )

        # create new dictionary
        if existing_info:
            if spec.dictionary_conflict_policy == "use-existing":
                return int(existing_info["SingleTypeNo"])
            if spec.dictionary_conflict_policy == "unique" and base_root is not None:
                dspec.name = _unique_dictionary_name(base_root, dspec.name)
            elif interactive and existing_found_via_api and sys.stdin.isatty():
                prompt = (
                    f"Dictionary '{dspec.name}' already exists. Choose: "
                    "[u]se existing, [n]ew unique name, [a]bort: "
                )
                choice = input(prompt).strip().lower()
                if choice in ("u", "use", "existing"):
                    return int(existing_info["SingleTypeNo"])
                if choice in ("n", "new", "unique") and base_root is not None:
                    dspec.name = _unique_dictionary_name(base_root, dspec.name)
                else:
                    raise ValueError("Aborted by user.")
            elif base_root is not None:
                raise ValueError(
                    f"Dictionary '{dspec.name}' already exists. Use 'existing dictionary' in the description "
                    "or choose a different name. (Or run with --api-check --interactive for a prompt.)"
                )
        if dspec.name in dict_type_map:
            return dict_type_map[dspec.name]

        key_dic_no = dict_ids.take()
        single_type_no = dict_type_ids.take()
        nonlocal next_keywords_suffix
        key_table = f"TheKeywords{next_keywords_suffix}"
        next_keywords_suffix += 1

        d_el = ET.Element("Dictionary")
        ET.SubElement(d_el, "KeyDicNo").text = str(key_dic_no)
        # NextNo is max keyword no (or 0 if none)
        next_no = len(dspec.keywords) if dspec.keywords else 0
        ET.SubElement(d_el, "NextNo").text = str(next_no)
        ET.SubElement(d_el, "SingleTypeNo").text = str(single_type_no)
        ET.SubElement(d_el, "KeyDicName").text = dspec.name
        ET.SubElement(d_el, "KeyDicTable").text = key_table

        kw_root = ET.SubElement(d_el, "Keywords")
        for i, kw in enumerate(dspec.keywords, start=1):
            kw_el = ET.SubElement(kw_root, "KW")
            ET.SubElement(kw_el, "KeywordNo").text = str(i)
            kw_el.append(_tstr_element("Keyword", kw))
            ET.SubElement(kw_el, "Id").text = _guid()

        ET.SubElement(d_el, "Id").text = _guid()
        new_dicts.append(d_el)
        dict_type_map[dspec.name] = single_type_no
        return single_type_no

    # Build fields
    layout = Layout()
    y = layout.start_y
    tab_order = 1
    disp_order = 1
    field_elems: List[ET.Element] = []
    min_x = None
    min_y = None
    max_x = None
    max_y = None

    def update_bounds(x: int, y: int, w: int, h: int) -> None:
        nonlocal min_x, min_y, max_x, max_y
        if min_x is None or x < min_x:
            min_x = x
        if min_y is None or y < min_y:
            min_y = y
        if max_x is None or (x + w) > max_x:
            max_x = x + w
        if max_y is None or (y + h) > max_y:
            max_y = y + h

    next_table_suffix = _next_table_suffix(base_root, "TheIxTable") if base_root is not None else 1

    for f in spec.fields:
        if f.type not in ("text", "number", "decimal", "date", "keyword_single", "keyword_multiple", "table", "reference_table"):
            raise ValueError(f"Unsupported field type: {f.type}")

        # determine widths
        width = layout.widths.get(f.type, 120)
        height = layout.field_h
        pos_x = layout.field_x
        pos_y = y
        label_x = layout.label_x
        label_y = y + layout.label_offset_y

        label_w = 63
        if f.type == "text":
            length = f.length or 50
            if length > TEXT_FIELD_MAX_LENGTH:
                raise ValueError(
                    f"Text field '{f.name}' length {length} exceeds Therefore maximum of {TEXT_FIELD_MAX_LENGTH}"
                )
            field_no = field_ids.take()
            data_field = _make_text_field(field_no, f.name, length, pos_x, pos_y, tab_order, disp_order, width, height, f.default_value, f.index_type, f.mandatory)
            label_field = _make_label_field(field_ids.take(), f.name, f"Label_{_slugify(f.name)}", label_x, label_y, 63, layout.label_h)
            field_elems.extend([data_field, label_field])
            update_bounds(pos_x, pos_y, width, height)
            update_bounds(label_x, label_y, label_w, layout.label_h)
        elif f.type == "number":
            length = f.length or 10
            field_no = field_ids.take()
            data_field = _make_number_field(field_no, f.name, length, pos_x, pos_y, tab_order, disp_order, width, height, f.default_value, f.index_type, f.mandatory)
            label_field = _make_label_field(field_ids.take(), f.name, f"Label_{_slugify(f.name)}", label_x, label_y, 63, layout.label_h)
            field_elems.extend([data_field, label_field])
            update_bounds(pos_x, pos_y, width, height)
            update_bounds(label_x, label_y, label_w, layout.label_h)
        elif f.type == "decimal":
            length = f.length or 10
            scale = f.scale or 2
            field_no = field_ids.take()
            data_field = _make_decimal_field(field_no, f.name, length, scale, pos_x, pos_y, tab_order, disp_order, width, height, f.default_value, f.index_type, f.mandatory)
            label_field = _make_label_field(field_ids.take(), f.name, f"Label_{_slugify(f.name)}", label_x, label_y, 63, layout.label_h)
            field_elems.extend([data_field, label_field])
            update_bounds(pos_x, pos_y, width, height)
            update_bounds(label_x, label_y, label_w, layout.label_h)
        elif f.type == "date":
            length = f.length or 4
            field_no = field_ids.take()
            data_field = _make_date_field(field_no, f.name, length, pos_x, pos_y, tab_order, disp_order, width, height, f.default_value, f.index_type, f.mandatory)
            label_field = _make_label_field(field_ids.take(), f.name, f"Label_{_slugify(f.name)}", label_x, label_y, 63, layout.label_h)
            field_elems.extend([data_field, label_field])
            update_bounds(pos_x, pos_y, width, height)
            update_bounds(label_x, label_y, label_w, layout.label_h)
        elif f.type == "keyword_single":
            if not f.dictionary:
                raise ValueError(f"Keyword field '{f.name}' missing dictionary spec")
            dict_type_no = ensure_dictionary(f.dictionary)
            hidden_no = field_ids.take()
            hidden_field = _make_keyword_hidden_field(hidden_no, f.name, dict_type_no)
            visible_no = field_ids.take()
            visible_field = _make_keyword_text_field(visible_no, f.name, hidden_no, pos_x, pos_y, tab_order, disp_order, width, height)
            label_field = _make_label_field(field_ids.take(), f.name, f"Label_{_slugify(f.name)}_Text", label_x, label_y, 63, layout.label_h)
            field_elems.extend([visible_field, label_field, hidden_field])
            update_bounds(pos_x, pos_y, width, height)
            update_bounds(label_x, label_y, label_w, layout.label_h)
        elif f.type == "keyword_multiple":
            if not f.dictionary:
                raise ValueError(f"Multiple keyword field '{f.name}' missing dictionary spec")
            ensure_dictionary(f.dictionary)  # Ensure dictionary exists but don't use TypeNo
            field_no = field_ids.take()
            data_field = _make_multi_keyword_field(field_no, f.name, pos_x, pos_y, tab_order, disp_order, width, height, f.default_value, f.index_type, f.mandatory)
            label_field = _make_label_field(field_ids.take(), f.name, f"Label_{_slugify(f.name)}", label_x, label_y, 63, layout.label_h)
            field_elems.extend([data_field, label_field])
            update_bounds(pos_x, pos_y, width, height)
            update_bounds(label_x, label_y, label_w, layout.label_h)
        elif f.type == "reference_table":
            if not f.reference_table:
                raise ValueError(f"Reference table field '{f.name}' missing reference_table spec")

            # Resolve referenced category
            ref_cat_id = f.reference_table.category_no
            ref_table_info = None

            if not ref_cat_id and f.reference_table.category_name:
                if api_client is not None:
                    ref_cat_id = _find_referenced_table_by_name_api(api_client, f.reference_table.category_name)
                    if not ref_cat_id:
                        raise ValueError(
                            f"Referenced table '{f.reference_table.category_name}' not found. "
                            "Provide category_no explicitly or ensure --api-check is enabled."
                        )
                else:
                    raise ValueError(
                        f"Referenced table '{f.reference_table.category_name}' requires API access to resolve. "
                        "Provide category_no explicitly or use --api-check."
                    )

            if not ref_cat_id:
                raise ValueError(f"Reference table field '{f.name}' requires either category_name or category_no")

            # Get table info if API available
            if api_client is not None:
                ref_table_info = _get_referenced_table_info_api(api_client, ref_cat_id)

            # Create primary (hidden) field
            primary_no = field_ids.take()
            primary_field = _make_primary_reference_field(primary_no, f.name, ref_cat_id, pos_x, pos_y, tab_order, disp_order, width, height, f.index_type, f.mandatory)
            field_elems.append(primary_field)

            # Create dependent fields
            if f.reference_table.dependent_fields:
                for dep_field_name in f.reference_table.dependent_fields:
                    # Try to get column name from table info if available
                    column_name = dep_field_name
                    if ref_table_info:
                        columns = ref_table_info.get("Columns") or []
                        # Match by name (case-insensitive)
                        for col in columns:
                            if col.get("ColumnName", "").lower() == dep_field_name.lower():
                                column_name = col["ColumnName"]
                                break

                    dep_no = field_ids.take()
                    dep_field = _make_dependent_reference_field(dep_no, dep_field_name, column_name, primary_no, pos_x, pos_y, tab_order, disp_order, width, height, f.reference_table.dependency_mode)
                    label_field = _make_label_field(field_ids.take(), dep_field_name, f"Label_{_slugify(dep_field_name)}", label_x, label_y, 63, layout.label_h)
                    field_elems.extend([dep_field, label_field])
                    update_bounds(pos_x, pos_y, width, height)
                    update_bounds(label_x, label_y, label_w, layout.label_h)
                    y += layout.row_height
                    pos_y = y
                    label_y = y + layout.label_offset_y
                    tab_order += 1
                    disp_order += 1
        elif f.type == "table":
            table_name = f.name or "Line Items"
            if not f.columns:
                raise ValueError("Table field requires columns.")
            foreign_table = f"TheIxTable{next_table_suffix}"
            next_table_suffix += 1
            table_field_no = field_ids.take()
            table_field = _make_table_field(table_field_no, table_name, foreign_table, pos_x, pos_y, tab_order, disp_order, width, layout.table_height)
            label_field = _make_label_field(field_ids.take(), table_name, f"Label_{_slugify(table_name)}", label_x, label_y, 63, layout.table_height)
            field_elems.extend([table_field, label_field])
            update_bounds(pos_x, pos_y, width, layout.table_height)
            update_bounds(label_x, label_y, label_w, layout.table_height)
            # table columns
            col_disp = 1
            for col in f.columns:
                col_field = _make_table_column_field(
                    field_ids.take(),
                    col.name,
                    col.type,
                    table_field_no,
                    col_disp,
                    col.length,
                    col.scale,
                    _column_width_for(col.name, col.type),
                )
                field_elems.append(col_field)
                col_disp += 1

        # Skip position increment for reference_table (already handled in dependent field loop)
        if f.type != "reference_table":
            row_step = layout.row_height
            if f.type == "table":
                row_step = max(row_step, layout.table_height + 8)
            y += row_step
            tab_order += 1
            disp_order += 1

    # Category element
    cat_no = cat_ids.take()
    cat = ET.Element("Category")
    ET.SubElement(cat, "CtgryNo").text = str(cat_no)
    cat.append(_tstr_element("Name", spec.name))
    ET.SubElement(cat, "Version").text = "0"

    fields_el = ET.SubElement(cat, "Fields")
    for f in field_elems:
        fields_el.append(f)

    ET.SubElement(cat, "DataTypes")
    ET.SubElement(cat, "Title").text = spec.name

    # Calculate width/height based on actual content bounds
    padding_x = 30
    padding_y = 30
    if min_x is None or min_y is None or max_x is None or max_y is None:
        calc_width = 300
        calc_height = 200
    else:
        calc_width = int((max_x - min_x) + padding_x)
        calc_height = int((max_y - min_y) + padding_y)
        calc_width = max(calc_width, 200)
        calc_height = max(calc_height, 120)
    ET.SubElement(cat, "Width").text = str(calc_width)
    ET.SubElement(cat, "Height").text = str(calc_height)

    ET.SubElement(cat, "FolderNo").text = str(folder_no)
    watermark = ET.SubElement(cat, "Watermark")
    ET.SubElement(watermark, "DocNo").text = "0"
    ET.SubElement(cat, "FulltextMode").text = "1"
    ET.SubElement(cat, "FulltextDate").text = "18991230"
    ET.SubElement(cat, "CheckInMode").text = "1"
    if spec.description:
        cat.append(_tstr_element("Description", spec.description))
    else:
        cat.append(_empty_tstr("Description"))
    ET.SubElement(cat, "Header")
    ET.SubElement(cat, "Id").text = _guid()
    ET.SubElement(cat, "EmptyDocMode").text = "1"
    ET.SubElement(cat, "CoverMode").text = "1"
    ET.SubElement(cat, "DocTitles")
    ET.SubElement(cat, "CtgryID").text = _slugify(spec.name)

    # Root configuration
    cfg = ET.Element("Configuration")
    if base_root is not None:
        version_info = _find_baseline_version(base_root)
        ET.SubElement(cfg, "Version").text = version_info["Version"]
        ET.SubElement(cfg, "NewImportExport").text = version_info["NewImportExport"]
    else:
        ET.SubElement(cfg, "Version").text = CONFIG_XML_VERSION
        ET.SubElement(cfg, "NewImportExport").text = CONFIG_XML_NEW_IMPORT_EXPORT

    if new_folder_elem is not None:
        folders_el = ET.SubElement(cfg, "Folders")
        folders_el.append(new_folder_elem)

    if new_dicts:
        dicts_el = ET.SubElement(cfg, "KeywordDictionaries")
        for d in new_dicts:
            dicts_el.append(d)

    cats_el = ET.SubElement(cfg, "Categories")
    cats_el.append(cat)

    return ET.ElementTree(cfg)


# ------------------------- CLI -------------------------


def main() -> None:
    epilog = """
EXAMPLES:
  Basic usage (no API, no baseline):
    %(prog)s --description description.txt --output output.xml

  With API check (recommended for reference_table fields):
    %(prog)s --description description.txt --output output.xml --api-check --tenant craigdemo

  With baseline validation (collision detection):
    %(prog)s --description spec.json --baseline craigdemo-baseline.xml --output output.xml

  Full options with interactive prompts:
    %(prog)s --description description.txt --baseline baseline.xml --output output.xml \\
             --api-check --tenant craigdemo --interactive --folder-on-exists unique

WHEN TO USE OPTIONS:
  --api-check
    • Creating reference_table fields (resolves category names to IDs)
    • Using "existing dictionary" (verifies dictionary exists)
    • Checking for conflicts with existing folders/categories
    • Required when description uses category names instead of IDs

  --baseline
    • Collision detection for categories/folders/dictionaries
    • Generating unique names on conflicts (with --folder-on-exists unique)
    • Validating against known server state
    • Not required for normal generation

  --interactive
    • Running manually (not in automated scripts)
    • Want prompts for conflict resolution decisions
    • Need to choose between existing objects or creating new ones

  --tenant
    • Multiple tenants configured in .env file
    • Overriding the default tenant
    • Required with --api-check if default tenant not set

FIELD TYPES SUPPORTED:
  • text - Text field with configurable length (max 4000)
  • number - Integer field
  • decimal - Decimal field with precision/scale
  • date - Date field with optional default values
  • keyword_single - Single-select dropdown from dictionary
  • keyword_multiple - Multi-select dropdown from dictionary
  • table - Embedded table with columns
  • reference_table - Referenced table with dependent fields

REFERENCE TABLE DEPENDENCY MODES:
  • Referenced (default) - Data pulled from source, not stored locally
  • Synchronized redundant - Data copied locally and kept in sync (read-only)
  • Editable redundant - Data copied locally, can be edited independently

  Natural language: "with synchronized columns" or "with editable columns"
  JSON: "dependency_mode": "synchronized" or "editable" or "referenced"
"""

    parser = argparse.ArgumentParser(
        description="Generate Therefore config delta XML for a new category.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--baseline",
        metavar="PATH",
        help="Optional baseline TheConfiguration.xml export for diff-mode collision checks. "
             "Use to validate against existing server state and detect naming conflicts."
    )

    parser.add_argument(
        "--description",
        required=True,
        metavar="PATH",
        help="Path to natural language description (.txt), YAML (.yaml/.yml), or JSON (.json) spec file. "
             "Examples: 'description.txt', 'category.json', 'spec.yaml'"
    )

    parser.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Output delta XML file path. This file can be imported into Therefore to create the category. "
             "Example: 'output.xml'"
    )

    parser.add_argument(
        "--folder-on-exists",
        choices=["error", "use-existing", "unique"],
        metavar="POLICY",
        help="Policy when requested folder already exists. "
             "Choices: error (fail), use-existing (reuse folder - default), unique (generate unique name)."
    )

    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Enable interactive prompts for conflict resolution. "
             "Use when running manually to make decisions about existing objects. "
             "Not recommended for automated scripts."
    )

    parser.add_argument(
        "--api-check",
        action="store_true",
        help="Query Therefore tenant via WebAPI to resolve names and validate objects. "
             "REQUIRED for: reference_table fields (resolves category names), "
             "existing dictionaries (validates they exist). "
             "Requires valid tenant configuration in .env file."
    )

    parser.add_argument(
        "--env",
        metavar="PATH",
        help="Path to Therefore .env configuration file. "
             "Defaults to THEREFORE_ENV_PATH environment variable if set. "
             "Contains tenant connection details for --api-check. "
             "Example: '/path/to/.env.local'"
    )

    parser.add_argument(
        "--tenant",
        metavar="NAME",
        help="Tenant key/name to use when multiple tenants are configured in .env file. "
             "Required with --api-check if default tenant not specified. "
             "Examples: 'craigdemo', 'production', 'dev'"
    )

    args = parser.parse_args()

    spec = parse_description(args.description)
    if args.folder_on_exists:
        spec.folder_conflict_policy = args.folder_on_exists
    api_client = None
    env_path = args.env or os.environ.get("THEREFORE_ENV_PATH")
    if args.api_check or (env_path and Path(env_path).exists()):
        api_client = _build_api_client(env_path, args.tenant)
    tree = build_delta_xml(spec, args.baseline, api_client=api_client, interactive=args.interactive)
    Path(args.output).write_text(ET.tostring(tree.getroot(), encoding="unicode"))


if __name__ == "__main__":
    main()
