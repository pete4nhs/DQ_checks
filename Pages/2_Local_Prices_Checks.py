# cd "C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks"

import streamlit as st
import pandas as pd
import re
#import io
#from datetime import datetime
#import os

# ---------------------- Page config (must be first) ----------------------
st.set_page_config(
    page_title="Local Prices Reporting DQ checks",
    page_icon="https://www.england.nhs.uk/wp-content/themes/nhsengland/static/img/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": "This tool is designed to support ICBs/Trusts to check the data quality of their submission for Local Prices."},)

# ---------------------- Session state initialisation ----------------------
if "final_df" not in st.session_state:
    st.session_state.final_df = None
if "csv_bytes" not in st.session_state:
    st.session_state.csv_bytes = None
if "calc_done" not in st.session_state:
    st.session_state.calc_done = False
if "show_preview" not in st.session_state:
    st.session_state.show_preview = False
if "uploaded_signature" not in st.session_state:
    st.session_state.uploaded_signature = None  # to detect file changes
if "show_instruction_msg" not in st.session_state:
    st.session_state.show_instruction_msg = True
if "upload_success" not in st.session_state:
    st.session_state.upload_success = False


# ---------------------- Helpers ----------------------

def file_signature(uploaded_file):
    """Create a simple signature of the uploaded CSV file to detect changes."""
    if uploaded_file is None:
        return None
    return (uploaded_file.name, uploaded_file.size)


def to_1_based_indices(result, limit=1000):
    """
    Convert 0-based pandas indices to Excel-style row numbers:
    +1 for 1-based indexing, +1 for header row => +2 total.
    """
    if isinstance(result, str):
        return result

    if isinstance(result, (list, tuple, pd.Index)):
        uniq = sorted(set(int(i) for i in result))
        rows = [i + 2 for i in uniq]
        return f"More than {limit} invalid rows" if len(rows) > limit else rows

    if isinstance(result, pd.DataFrame):
        uniq = sorted(set(int(i) for i in result.index))
        rows = [i + 2 for i in uniq]
        return f"More than {limit} invalid" if len(rows) > limit else rows

    return "Unexpected error"


def clean_numeric_text(s: pd.Series) -> pd.Series:
    return (
        s.astype("string")
         .str.replace("\ufeff", "", regex=False)
         .str.replace("\u00a0", "", regex=False)
         .str.replace(r"[\u200B-\u200D\uFEFF]", "", regex=True)
         .str.strip()
         .str.replace(r"\.0+$", "", regex=True))  # strip trailing .0/.00...


ALLOWED_COMMISSIONED_SERVICE_CATEGORY_CODES = {
    "12", "21", "22", "25", "26","31", "32", "41",
    "51", "55","61","71", "75","81", "85",
    "91", "92", "93","98", "99",}

NON_ACTIVITY_PODS = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

# Your extra exemption list goes here
OTHER_BLANK_ALLOWED_PODS = set()

BLANK_ALLOWED_PODS = NON_ACTIVITY_PODS.union(OTHER_BLANK_ALLOWED_PODS)

def get_clean_and_blank(df, col):
    cleaned = df[col].fillna("").astype("string").str.strip()
    blank = cleaned.eq("")
    return cleaned, blank

def normalise_header(h: str) -> str:
    """
    Standardise a column name so that:
    - Underscores are treated as spaces
    - Case differences are removed (converted to uppercase)
    - Hidden Excel characters are removed
    - Extra spaces inside the string are NOT corrected (strict mode)
    """
    if h is None:
        return ""

    h = str(h)

    # Remove hidden / problematic characters from Excel exports
    h = (h.replace("\ufeff", "")   # BOM
         .replace("\u00a0", " "))  # NBSP → normal space
    
    # Remove zero-width characters
    h = re.sub(r"[\u200B-\u200D\uFEFF]", "", h)

    # Trim leading/trailing spaces ONLY (keep internal spacing strict)
    h = h.strip()

    # Treat underscores as spaces
    h = h.replace("_", " ")

    # Convert to uppercase (final step)
    h = h.upper()

    return h



def normalise_dataframe_headers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply header normalisation to the whole dataframe.
    If two columns collapse to the same normalised name, keep the first and suffix the rest.
    """
    new_cols = []
    seen = {}
    for c in df.columns:
        nc = normalise_header(c)
        if nc in seen:
            seen[nc] += 1
            nc = f"{nc} ({seen[nc]})"
        else:
            seen[nc] = 0
        new_cols.append(nc)

    df = df.copy()
    df.columns = new_cols
    return df


def format_status_for_output(val):
    """
    Format the Status column for display.
    - 'Valid' stays as-is
    - Row lists become 'Invalid rows: [..]'
    - 'More than X invalid' stays as-is (no prefix)
    """
    if isinstance(val, str):
        v = val.strip()

        if v == "Valid":
            return "Valid"

        # Do NOT prefix summary messages
        if v.startswith("More than"):
            return v

        # Other strings (e.g. Error: column not found)
        return v

    # Only lists / indices get the prefix
    if isinstance(val, (list, tuple, pd.Index)):
        return f"Invalid rows: {list(val)}"

    return val

BLANK_WHEN_NON_ACTIVITY_POD_FIELDS = {
    "ACTIVITY TREATMENT FUNCTION CODE"}

BLANK_RULE_NOTE = (
    "Leave this field blank when POINT OF DELIVERY CODE is "
    "ADJUSTMENT, BLOCK, CQUIN, DRUG, DEVICE, or NAOTHER.")

TARIFF_RULE_NOTE = (
    "Leave this field blank when POINT OF DELIVERY CODE is "
    "ADJUSTMENT, BLOCK, CQUIN, DRUG, DEVICE, NAOTHER, or OTHER. "
    "For all other Point of Delivery Codes, a valid HRG‑based tariff code is required.")


def non_activity_blank_rule_triggered(df: pd.DataFrame, field_col: str) -> bool:
    """
    Returns True only when:
      - POD is a non-activity value, AND
      - the field is populated (non-empty)
    """
    pod_col = "POINT OF DELIVERY CODE"
    if field_col not in df.columns or pod_col not in df.columns:
        return False

    pod = (
        df[pod_col]
        .astype("string")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper())

    field_raw = df[field_col].astype("string")
    field_has_value = field_raw.notna() & (field_raw.str.strip() != "")

    return (field_has_value & pod.isin(NON_ACTIVITY_PODS)).any()



def get_tariff_invalid_mask(df: pd.DataFrame) -> pd.Series | None:
    col = "TARIFF CODE"
    pod_col = "POINT OF DELIVERY CODE"

    for c in (col, pod_col):
        if c not in df.columns:
            return None

    exclude_pods = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER", "OTHER"}

    pod_raw = df[pod_col]
    pod = pod_raw.astype("string").str.strip().str.upper()
    pod_known = pod_raw.notna() & (pod != "")

    tariff_raw = df[col]
    tariff = tariff_raw.astype("string").str.strip()

    tariff_clean = (tariff
        .str.replace("\ufeff", "", regex=False)
        .str.replace("\xa0", " ", regex=False)
        .str.strip())

    tariff_up = tariff_clean.str.upper()
    has_tariff = tariff_raw.notna() & (tariff_clean != "")

    # when running locally
#    hrg = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\HRG.csv")

    # when running in stlite
    hrg_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/HRG.csv")
    hrg = pd.read_csv(hrg_URL)

    if "HRG_code" in hrg.columns:
        hrg_col = "HRG_code"
    elif "HRG_Code" in hrg.columns:
        hrg_col = "HRG_Code"
    else:
        hrg_col = hrg.columns[0]

    valid_hrg = hrg[hrg_col].dropna().astype(str).str.strip().str.upper()

    codes_by_len = {}
    for code in valid_hrg:
        codes_by_len.setdefault(len(code), set()).add(code)

    starts_with_hrg = pd.Series(False, index=df.index)
    for L, code_set in codes_by_len.items():
        starts_with_hrg |= tariff_up.str[:L].isin(code_set)

    invalid_too_long = has_tariff & (tariff_clean.str.len() > 50)

    required = pod_known & (~pod.isin(exclude_pods))
    invalid_missing_when_required = required & (~has_tariff)
    invalid_bad_prefix_required = required & has_tariff & (~starts_with_hrg)

    excluded = pod_known & pod.isin(exclude_pods)
    invalid_bad_prefix_excluded = excluded & has_tariff & (~starts_with_hrg)

    return (invalid_too_long
        | invalid_missing_when_required
        | invalid_bad_prefix_required
        | invalid_bad_prefix_excluded)

def tariff_rule_triggered(df: pd.DataFrame) -> bool:
    invalid_mask = get_tariff_invalid_mask(df)
    return False if invalid_mask is None else invalid_mask.any()

# Length restriction suggestions

LENGTH_RULES = {
    "LOCAL SUB-SPECIALTY CODE": {"type": "max", "value": 8},
    "LOCAL POINT OF DELIVERY CODE": {"type": "max", "value": 50},
    "LOCAL POINT OF DELIVERY DESCRIPTION": {"type": "max", "value": 100},
    "COMMISSIONING_SERIAL_NUMBER": {"type": "max", "value": 6},
    "PROVIDER_REFERENCE_IDENTIFIER": {"type": "max", "value": 20},
    "NHS_SERVICE_AGREEMENT_LINE_NUMBER": {"type": "max", "value": 10},
    "LOCAL PRICE": {"type": "max", "value": 18}}

def length_rule_triggered(df: pd.DataFrame, col: str) -> bool:
    """
    Returns True only when the field has an actual character-length issue.
    This avoids showing a length note for unrelated problems such as a missing column.
    """
    if col not in df.columns or col not in LENGTH_RULES:
        return False

    rule = LENGTH_RULES[col]
    s = df[col].astype("string")
    present = s.notna()

    if rule["type"] == "exact":
        return (present & (s.str.len() != rule["value"])).any()

    if rule["type"] == "max":
        return (present & (s.str.len() > rule["value"])).any()

    return False

def get_length_rule_note(col: str) -> str:
    """
    Returns a friendly note describing the character length rule.
    """
    rule = LENGTH_RULES.get(col)
    if not rule:
        return ""

    if rule["type"] == "exact":
        return f"This field must be exactly {rule['value']} characters long."

    if rule["type"] == "max":
        return f"This field must be {rule['value']} characters or fewer."

    return ""


# ---------------------- Header ----------------------
st.image('input_data_other/london_logos_n_name.png', width=1050)
st.title("Automated _Local Prices_ Reporting DQ checks")
st.write('')
st.write("The full documentation on how to fill in the report can be found at "
    "[https://www.england.nhs.uk/publication/local-prices-reporting-specification-technical-detail-specific-data-requirements/]"
    "(https://www.england.nhs.uk/publication/local-prices-reporting-specification-technical-detail-specific-data-requirements/)")


# ---------------------- Instruction message ----------------------
instruction_msg = st.empty()

if st.session_state.show_instruction_msg:
    instruction_msg.info("Please upload a CSV file and click 'Run checks'.")
else:
    instruction_msg.empty()

# ---------------------- File upload (CSV only) ----------------------
uploaded_lpr = st.file_uploader(
    "📤 **Upload your Local Prices Reporting as a CSV file.**",
    type=["csv"],
    help="Upload your Local Prices Reporting here. Import only the essential tab as a '.csv' file.")

# ---------------------- Reset state if file changes ----------------------

sig = file_signature(uploaded_lpr)

# ---------------------- Handle upload / removal ----------------------

# Case 1: file removed
if uploaded_lpr is None:
    st.session_state.uploaded_signature = None
    st.session_state.upload_success = False
    st.session_state.final_df = None
    st.session_state.csv_bytes = None
    st.session_state.calc_done = False
    st.session_state.show_preview = False
    if not st.session_state.calc_done:
        st.session_state.show_instruction_msg = True

# Case 2: new or changed file uploaded
elif sig != st.session_state.uploaded_signature:
    st.session_state.uploaded_signature = sig
    st.session_state.upload_success = True
    st.session_state.final_df = None
    st.session_state.csv_bytes = None
    st.session_state.calc_done = False
    st.session_state.show_preview = False
    st.session_state.show_instruction_msg = False
# ---------------------- Upload message ----------------------

if st.session_state.upload_success:
    st.success("Local Price CSV uploaded successfully!")

# ---------------------- Validators  ----------------------

# --------------------- FINANCIAL YEAR (mandatory)
def validate_year_columns(df):
    col = "FINANCIAL YEAR"
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    # Clean + coerce
    s = df[col].astype(str).str.strip()
    yr = pd.to_numeric(s, errors="coerce")

    invalid = df[
        yr.isna() | (yr < 201011) | (yr > 205051)]
    
    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- DATE AND TIME DATA SET CREATED (mandatory)
def validate_datetime_columns(df):
    col = 'DATE AND TIME DATA SET CREATED'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    df[col] = clean_numeric_text(df[col])
    parsed = pd.to_datetime(df[col], errors="coerce")
    invalid = df[
        df[col].notna() & (
            parsed.isna() |        # not a datetime at all
            parsed.dt.second.isna())]  # seconds missing
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ORGANISATION IDENTIFIER (CODE OF PROVIDER) (mandatory)
def validate_cop_columns(df):
    col = 'ORGANISATION IDENTIFIER (CODE OF PROVIDER)'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    series = df[col].astype(str)
    invalid = df[
        df[col].isna() |
        (series.str.len() < 3) |
        (series.str.len() > 6) |
        series.str.endswith("00", na=False)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ORGANISATION SITE IDENTIFIER (OF TREATMENT) (mandatory where relevant)
def validate_of_treatment_columns(df):
    col = 'ORGANISATION SITE IDENTIFIER (OF TREATMENT)'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() < 5]
    invalid = pd.concat([invalid, df[df[col].astype(str).str.len() > 9]])
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ORGANISATION IDENTIFIER (CODE OF COMMISSIONER) (mandatory)
def validate_commissioner_code_columns(df):
    col = 'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[df[col].isna()]
    invalid = pd.concat([invalid, df[df[col].astype(str).str.len() < 3]])
    invalid = pd.concat([invalid, df[df[col].astype(str).str.len() > 5]])
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ACTIVITY TREATMENT FUNCTION CODE (mandatory where relevant)
def validate_activity_TFC_columns(df):
    col = 'ACTIVITY TREATMENT FUNCTION CODE'
    pod_col = 'POINT OF DELIVERY CODE'

    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # when running locally
#    tfc_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\TFC.csv")

    # when running in stlite
    tfc_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/TFC.csv")
    tfc_df = pd.read_csv(tfc_URL)    
    
    valid_codes = set(tfc_df.iloc[:, 0].dropna().astype(str).str.strip())
    pod = df[pod_col].fillna("").astype("string").str.strip().str.upper()
    cleaned, blank = get_clean_and_blank(df, col)

    invalid_when_pod_non_activity = pod.isin(NON_ACTIVITY_PODS) & (~blank)
    invalid_required_missing = (~pod.isin(BLANK_ALLOWED_PODS)) & blank

    invalid_code = (~blank) & (~cleaned.isin(valid_codes))

    invalid = df[invalid_when_pod_non_activity | invalid_required_missing | invalid_code]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL SUB-SPECIALTY CODE (optional)
def validate_local_sub_columns(df):
    col = 'LOCAL SUB-SPECIALTY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 8]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- COMMISSIONING SERIAL NUMBER (optional)
def validate_comm_serial_n_columns(df):
    col = 'COMMISSIONING SERIAL NUMBER'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 6]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- PROVIDER REFERENCE IDENTIFIER (optional)
def validate_provider_ref_identifier_columns(df):
    col = 'PROVIDER REFERENCE IDENTIFIER'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 20]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- NHS SERVICE AGREEMENT LINE NUMBER (optional)
def validate_nhs_service_cat_n_columns(df):
    col = 'NHS SERVICE AGREEMENT LINE NUMBER'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 10]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- COMMISSIONED SERVICE CATEGORY CODE (mandatory)
def validate_commissioned_service_code_columns(df):
    col = "COMMISSIONED SERVICE CATEGORY CODE"
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    s = clean_numeric_text(df[col])

    # Mandatory: blank / NA is invalid
    invalid_mask = s.isna() | (s == "")

    # If present, must be exactly 2 digits (digits-only + length rule)
    present = ~invalid_mask
    invalid_mask |= present & ~s.str.fullmatch(r"\d{2}", na=False)

    # If present and format OK, must be one of the allowed codes
    format_ok = present & s.str.fullmatch(r"\d{2}", na=False)
    invalid_mask |= format_ok & ~s.isin(ALLOWED_COMMISSIONED_SERVICE_CATEGORY_CODES)

    invalid = df[invalid_mask]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- SERVICE CODE (mandatory where relevant)
def validate_service_code_columns(df):
    col = 'SERVICE CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    
    # when running in stlite
    del_serv_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/Delegationservices_v38.csv")
    del_df = pd.read_csv(del_serv_URL)   

    # ✅ Make reference codes uppercase + trimmed
    valid_codes = {str(v).strip().upper()
        for v in del_df.iloc[:, 0].dropna()}

    # ✅ Clean + normalise user input the same way
    s = df[col].astype("string").str.strip()
    s_up = s.str.upper()

    # ✅ Case-insensitive comparison
    invalid = df[~s_up.isin(valid_codes)]

    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- POINT OF DELIVERY CODE (mandatory)
def validate_pod_code_columns(df):
    col = 'POINT OF DELIVERY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    NPOD_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/NPOD.csv")
    npod = pd.read_csv(NPOD_URL)

    # Clean and normalise NPOD reference values
    valid_codes = set(
        clean_numeric_text(npod.iloc[:, 0])
        .str.upper()
        .dropna())
    
    pod = (clean_numeric_text(df[col])
        .str.upper())

    # Validity check
    invalid_mask = ~pod.isin(valid_codes)
    
    # Lenght rule validation
    invalid_length = pod.notna() & (pod.str.len() > 10)

    # In valid rows
    invalid = df[invalid_mask | invalid_length]

    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- POINT OF DELIVERY FURTHER DETAIL CODE (mandatory where relevant)
def validate_pod_further_detail_code_columns(df):
    col = 'POINT OF DELIVERY FURTHER DETAIL CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[df[col].notna() &
        (df[col].astype(str).str.len() > 10)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- POINT OF DELIVERY FURTHER DETAIL DESCRIPTION (mandatory where relevant)
def validate_pod_further_detail_desc_columns(df):
    col = 'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[df[col].notna() &
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL POINT OF DELIVERY CODE (optional)
def validate_local_pod_code_columns(df):
    col = 'LOCAL POINT OF DELIVERY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 50)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL POINT OF DELIVERY DESCRIPTION (optional)
def validate_local_pod_desc_columns(df):
    col = 'LOCAL POINT OF DELIVERY DESCRIPTION'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- TARIFF CODE (mandatory where relevant)

def validate_tariff_code_columns(df):
    invalid_mask = get_tariff_invalid_mask(df)

    if invalid_mask is None:
        missing = [c for c in ["TARIFF CODE", "POINT OF DELIVERY CODE"] if c not in df.columns]
        return f"Error: '{missing[0]}' column not found in the data."

    invalid_rows = df[invalid_mask]
    return list(invalid_rows.index) if not invalid_rows.empty else "Valid"

# --------------------- LOCAL PRICE (mandatory)
def validate_local_price_columns(df):
    col = 'LOCAL PRICE'

    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    series = df[col]
    s = series.astype(str).str.strip()

    # Must not be empty
    empty_invalid = series.isna() | (s == "")

    # Must be a number (integer or decimal)
    decimal_ok = s.str.fullmatch(r"\d+(\.\d+)?")

    # Total digits (excluding decimal point) ≤ 18
    digit_count_ok = s.str.replace(".", "", regex=False).str.len() <= 18

    numeric_invalid = ~(decimal_ok & digit_count_ok)

    invalid_mask = empty_invalid | numeric_invalid
    invalid_indices = list(series.index[invalid_mask])

    return "Valid" if not invalid_indices else invalid_indices


# ---------------------- FIELD REQUIREMENT MAP ----------------------
REQUIREMENT_MAP = {
    'FINANCIAL YEAR': 'mandatory',
    'DATE AND TIME DATA SET CREATED': 'mandatory',
    'ORGANISATION IDENTIFIER (CODE OF PROVIDER)': 'mandatory',
    'ORGANISATION SITE IDENTIFIER (OF TREATMENT)': 'mandatory where relevant',
    'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)': 'mandatory',
    'ACTIVITY TREATMENT FUNCTION CODE': 'mandatory where relevant',
    'LOCAL SUB-SPECIALTY CODE': 'optional',
    'COMMISSIONING SERIAL NUMBER': 'optional',
    'PROVIDER REFERENCE IDENTIFIER': 'optional',
    'NHS SERVICE AGREEMENT LINE NUMBER': 'optional',
    'COMMISSIONED SERVICE CATEGORY CODE': 'mandatory',
    'SERVICE CODE': 'mandatory',
    'POINT OF DELIVERY CODE': 'mandatory',
    'POINT OF DELIVERY FURTHER DETAIL CODE': 'mandatory where relevant',
    'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION': 'mandatory where relevant',
    'LOCAL POINT OF DELIVERY CODE': 'optional',
    'LOCAL POINT OF DELIVERY DESCRIPTION': 'optional',
    'TARIFF CODE': 'mandatory where relevant',
    'LOCAL PRICE': 'mandatory',}

# ---------------------- STYLING (only Status column coloured) ----------------------

def style_results_table(df: pd.DataFrame):
    """
    Colour only the 'Status' column:
      - Blue when Status == "Valid"
      - Red when Requirement == "mandatory" and the row is invalid or Empty
      - Black otherwise
    """
    def _style_status_cell(row_slice):
        row_idx = row_slice.name
        req = str(df.loc[row_idx, 'Field requirement']).strip().lower()
        status = df.loc[row_idx, 'Status']

        def is_invalid_or_empty(val):
            if isinstance(val, str):
                return val.strip() != "Valid"
            return True

        is_valid = isinstance(status, str) and status.strip() == "Valid"

        if is_valid:
            return ['color: blue']
        elif req == 'mandatory' and is_invalid_or_empty(status):
            return ['color: red']
        else:
            return ['color: black']

    return df.style.apply(_style_status_cell, axis=1, subset=['Status'])


# ---------------------- Run checks button ----------------------
if st.button("Run checks", type="primary"):
    if uploaded_lpr is None:
        st.warning("Please upload a CSV file before running checks.")
        st.session_state.show_instruction_msg = True
    else:
        try:
            with st.spinner("Running calculations..."):
                df = pd.read_csv(
                    uploaded_lpr,
                    dtype="string",         # read everything safely as string
                    encoding="utf-8-sig")

                df = df.dropna(how="all").copy()


                # Clean month/year values (before validation)
                if "FINANCIAL YEAR" in df.columns:
                    df["FINANCIAL YEAR"] = clean_numeric_text(df["FINANCIAL YEAR"])

                # Build results
                columns = pd.Series([
                    'FINANCIAL YEAR', 'DATE AND TIME DATA SET CREATED',
                    'ORGANISATION IDENTIFIER (CODE OF PROVIDER)',
                    'ORGANISATION SITE IDENTIFIER (OF TREATMENT)',
                    'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)',
                    'ACTIVITY TREATMENT FUNCTION CODE', 'LOCAL SUB-SPECIALTY CODE',
                    'COMMISSIONING SERIAL NUMBER', 'PROVIDER REFERENCE IDENTIFIER',
                    'NHS SERVICE AGREEMENT LINE NUMBER',
                    'COMMISSIONED SERVICE CATEGORY CODE', 'SERVICE CODE',
                    'POINT OF DELIVERY CODE', 'POINT OF DELIVERY FURTHER DETAIL CODE',
                    'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION',
                    'LOCAL POINT OF DELIVERY CODE', 'LOCAL POINT OF DELIVERY DESCRIPTION',
                    'TARIFF CODE', 'LOCAL PRICE'
                ], name='Column name')

                requirement = columns.map(REQUIREMENT_MAP).rename("Field requirement")

                status = pd.Series([
                    format_status_for_output(to_1_based_indices(validate_year_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_datetime_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_cop_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_of_treatment_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_commissioner_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_activity_TFC_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_sub_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_comm_serial_n_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_provider_ref_identifier_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_nhs_service_cat_n_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_commissioned_service_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_service_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_pod_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_pod_further_detail_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_pod_further_detail_desc_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_pod_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_pod_desc_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_tariff_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_price_columns(df))),
                ], name="Status")

                def build_note(df: pd.DataFrame, col: str) -> str:
                    if col in BLANK_WHEN_NON_ACTIVITY_POD_FIELDS and non_activity_blank_rule_triggered(df, col):
                        return BLANK_RULE_NOTE

                    if col == "TARIFF CODE" and tariff_rule_triggered(df):
                        return TARIFF_RULE_NOTE

                    if col in LENGTH_RULES and length_rule_triggered(df, col):
                        return get_length_rule_note(col)

                    return ""

                suggestions = columns.map(lambda c: build_note(df, c)).rename("Suggestions")

                dfs = [columns, requirement, status]

                # Only include suggestions if at least one note is populated
                if suggestions.str.strip().ne("").any():
                    dfs.append(suggestions)

                final_df = pd.concat(dfs, axis=1)

                # Save for preview/download
                csv = final_df.to_csv(index=False)
                st.session_state.csv_bytes = csv.encode("utf-8")
                st.session_state.final_df = final_df
                st.session_state.calc_done = True
                st.session_state.show_preview = False  # do not auto-open
                st.session_state.show_instruction_msg = False

        except Exception as e:
            st.error(f"Failed to read CSV file. {e}")

# ---------------------- Results  ----------------------
if st.session_state.calc_done and st.session_state.final_df is not None:
    st.subheader("Results")

    with st.container(border=True):
        st.markdown(
            """
            <div style="font-size:1.05rem; font-weight:600; line-height:1.3;">
                Local Prices Reporting DQ results
            </div>
            """,
            unsafe_allow_html=True,)
        st.caption("Preview or download the analysed results")

        # Two half-width buttons
        col1, col2 = st.columns([1, 1], vertical_alignment="top")
        with col1:
            if st.button("👁️ View results", key="view_results_btn", use_container_width=True):
                st.session_state.show_preview = True
        with col2:
            st.download_button(
                label="⬇️ Download CSV",
                data=st.session_state.csv_bytes,
                file_name="Analysed Local Prices DQ checks.csv",
                mime="text/csv",
                key="dq_download_btn",
                use_container_width=True)

    # Inline preview that persists across reruns (only Status column coloured)
    if st.session_state.show_preview:
        with st.container(border=True):
            st.markdown("**This table shows which columns in your Local Prices Reporting are valid. If data is invalid, the Status column lists the row numbers with incorrect formatting** (if less than 100 records).")
            styled = style_results_table(st.session_state.final_df)
            st.dataframe(
                styled,
                use_container_width=True,
                height=560,
                hide_index=True)
            st.button("Close preview", key="close_preview_btn", on_click=lambda: st.session_state.update(show_preview=False))


# ---------------------- Important note (always visible at the bottom) ----------------------
st.write('')
st.write('')
st.warning("**Please note that uploading and processing DQ checks through this tool does not constitute data submission. " \
"This tool is solely intended to assess the formatting of your file.**")
