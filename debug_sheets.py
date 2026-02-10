import sys
import os
import logging
from pathlib import Path

# Setup basic logging to stdout
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add src to path
sys.path.append(str(Path.cwd()))

try:
    from src.config import (
        GOOGLE_SHEET_CREDENTIALS_PATH,
        GOOGLE_SHEET_ID,
        GOOGLE_SHEET_WORKSHEET_NAME,
        GOOGLE_CREDENTIALS_JSON
    )
    from src.services.sheets_service import _get_client, _get_worksheet
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)

print(f"--- Config Check ---")
print(f"CWD: {os.getcwd()}")
print(f"GOOGLE_SHEET_CREDENTIALS_PATH: '{GOOGLE_SHEET_CREDENTIALS_PATH}'")
print(f"GOOGLE_SHEET_ID: '{GOOGLE_SHEET_ID}'")
print(f"GOOGLE_SHEET_WORKSHEET_NAME: '{GOOGLE_SHEET_WORKSHEET_NAME}'")
print(f"GOOGLE_CREDENTIALS_JSON is set: {bool(GOOGLE_CREDENTIALS_JSON)}")

if not GOOGLE_SHEET_CREDENTIALS_PATH and not GOOGLE_CREDENTIALS_JSON:
    print("ERROR: No credentials configured.")
    sys.exit(1)

print("\n--- Connection Check ---")
try:
    print("Getting client...")
    client = _get_client()
    print("Client created successfully.")
    
    print(f"Opening spreadsheet by key: {GOOGLE_SHEET_ID}...")
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    print(f"Spreadsheet opened: {spreadsheet.title}")
    
    print(f"Getting worksheet: {GOOGLE_SHEET_WORKSHEET_NAME}...")
    try:
        worksheet = _get_worksheet(client)
        print(f"Worksheet found/created: {worksheet.title}")
        print(f"Headers: {worksheet.row_values(1)}")
    except Exception as e:
        print(f"Failed to get/create worksheet: {e}")
        
except Exception as e:
    print(f"Connection failed: {e}")
    import traceback
    traceback.print_exc()
