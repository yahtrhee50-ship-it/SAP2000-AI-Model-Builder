"""
Close a running SAP2000 instance cleanly via COM ApplicationExit.
Run this instead of task-killing so the CSI license releases properly.
Usage: python scripts\close_sap2000.py
"""
import sys

TYPELIB_GUID = "{F896D55D-8BDF-4232-B9AB-4B210897A81D}"
SAP_CLSID    = "{B6B21850-FB75-41DE-85EC-BC9DBEC69BD3}"

try:
    import pythoncom
    import comtypes
    import comtypes.client
except ImportError as e:
    print("ERROR: Missing library -", e)
    sys.exit(1)

pythoncom.CoInitialize()

print("Loading SAP2000 typelib ...")
lib = comtypes.client.GetModule((TYPELIB_GUID, 1, 0))

print("Attaching to running SAP2000 ...")
try:
    clsid = comtypes.GUID(SAP_CLSID)
    sap = comtypes.client.GetActiveObject(clsid, interface=lib.cOAPI)
except Exception as e:
    print("Could not find a running SAP2000 instance:", e)
    sys.exit(1)

print("Sending ApplicationExit(False) ...")
try:
    sap.ApplicationExit(False)
    print("SAP2000 closed cleanly.")
except Exception as e:
    print("ApplicationExit raised (SAP2000 may already be closing):", e)
