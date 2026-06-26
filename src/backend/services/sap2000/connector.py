"""
SAP2000 COM connection manager.
Connects to a running SAP2000 instance or starts a new one.

Uses comtypes (not pywin32) because cSapModel exposes only a vtable interface
and does not support IDispatch — pywin32 raises E_NOINTERFACE on SapModel.
comtypes reads the registered typelib and generates proper vtable wrappers.
"""
from __future__ import annotations
import logging
import subprocess
import time
import winreg

log = logging.getLogger(__name__)

# SAP2000 unit system codes
UNIT_CODES = {
    "kN_m":   6,   # kN, m, C
    "kip_ft": 4,   # kip, ft, F
    "kip_in": 3,   # kip, in, F
}

_SAP_PROGID   = "CSI.SAP2000.API.SapObject"
_SAP_CLSID    = "{B6B21850-FB75-41DE-85EC-BC9DBEC69BD3}"
# CSi Application Programming Interface (API) v1 typelib
_TYPELIB_GUID = "{F896D55D-8BDF-4232-B9AB-4B210897A81D}"


def _find_sap2000_exe() -> str | None:
    """Read the SAP2000 executable path from the COM registry."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{_SAP_PROGID}\CLSID")
        clsid = winreg.QueryValue(key, "")
        winreg.CloseKey(key)
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (r"SOFTWARE\Classes\CLSID", r"SOFTWARE\WOW6432Node\Classes\CLSID"):
                try:
                    k2 = winreg.OpenKey(hive, rf"{sub}\{clsid}\LocalServer32")
                    exe = winreg.QueryValue(k2, "")
                    winreg.CloseKey(k2)
                    exe = exe.strip('"').split('"')[0].strip()
                    return exe
                except OSError:
                    pass
    except OSError:
        pass
    return None


def _get_sap_lib():
    """Generate (or load cached) comtypes wrappers from the SAP2000 typelib."""
    import comtypes.client
    return comtypes.client.GetModule((_TYPELIB_GUID, 1, 0))


class SAP2000Connection:
    """Wraps the SAP2000 COM SapObject via comtypes vtable interface."""

    def __init__(self):
        self._sap_obj = None
        self._model = None

    @property
    def model(self):
        if self._model is None:
            raise RuntimeError("Not connected to SAP2000. Call connect() first.")
        return self._model

    def connect(self, visible: bool = True) -> None:
        """Attach to a running SAP2000 instance or launch a new one."""
        try:
            import pythoncom
            import comtypes
            import comtypes.client
            pythoncom.CoInitialize()
        except ImportError as e:
            raise RuntimeError(f"Required COM library missing: {e}. Install with: pip install comtypes pywin32")

        lib  = _get_sap_lib()
        clsid = comtypes.GUID(_SAP_CLSID)

        # 1. Try to attach to a SAP2000 that is already running
        try:
            self._sap_obj = comtypes.client.GetActiveObject(clsid, interface=lib.cOAPI)
            log.info("Attached to running SAP2000 instance.")
            self._model = self._sap_obj.SapModel
            return
        except Exception:
            pass

        # 2. Use SAP2000v1.Helper to create and start a new instance.
        #    Confirmed-working launch pattern from Project_003.
        #    Try QI to cHelper for typed vtable access; if that fails (interface not
        #    exposed on this installation), call CreateObject on the raw helper.
        exe = _find_sap2000_exe() or r"D:\CSI\SAP2000.exe"
        log.info("Launching SAP2000 via Helper from: %s", exe)

        helper = comtypes.client.CreateObject("SAP2000v1.Helper")
        try:
            helper = helper.QueryInterface(lib.cHelper)
            log.info("Helper QI to cHelper succeeded.")
        except Exception as qi_exc:
            log.warning("cHelper QI failed (%s) — using raw helper.", qi_exc)

        self._sap_obj = helper.CreateObject(exe)
        self._sap_obj.ApplicationStart()
        try:
            self._sap_obj.Visible = True
        except Exception:
            pass

        self._model = self._sap_obj.SapModel

    def initialize_new_model(self, unit_system: str = "kN_m") -> None:
        unit_code = UNIT_CODES.get(unit_system, 6)
        self.model.InitializeNewModel(unit_code)
        self.model.File.NewBlank()
        log.info("Initialized new SAP2000 model (units=%s)", unit_system)

    def save(self, path: str) -> None:
        ret = self.model.File.Save(path)
        if ret != 0:
            raise RuntimeError(f"SAP2000 Save failed (code {ret})")

    def run_analysis(self) -> None:
        ret = self.model.Analyze.RunDirectAnalysis()
        if ret != 0:
            raise RuntimeError(f"SAP2000 analysis failed (code {ret})")

    def close(self, save: bool = False) -> None:
        if self._sap_obj:
            self._sap_obj.ApplicationExit(save)
            self._sap_obj = None
            self._model = None


# Module-level singleton
_connection: SAP2000Connection | None = None


def get_connection() -> SAP2000Connection:
    global _connection
    if _connection is None:
        _connection = SAP2000Connection()
    return _connection
