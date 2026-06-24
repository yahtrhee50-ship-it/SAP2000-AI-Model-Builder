"""
SAP2000 COM connection manager.
Connects to a running SAP2000 instance or starts a new one.
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

_SAP_PROGID = "CSI.SAP2000.API.SapObject"


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
                    # Strip any quoted path or trailing flags
                    exe = exe.strip('"').split('"')[0].strip()
                    return exe
                except OSError:
                    pass
    except OSError:
        pass
    return None


class SAP2000Connection:
    """Wraps the SAP2000 COM SapObject."""

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
            import win32com.client as win32
            pythoncom.CoInitialize()
        except ImportError:
            raise RuntimeError("pywin32 is required for SAP2000 integration.")

        # 1. Try to attach to a SAP2000 that is already running
        try:
            self._sap_obj = win32.GetActiveObject(_SAP_PROGID)
            log.info("Attached to running SAP2000 instance.")
            self._model = self._sap_obj.SapModel
            return
        except Exception:
            pass

        # 2. Find the exe and launch it directly (avoids the -Embedding dialog)
        exe = _find_sap2000_exe()
        if not exe:
            raise RuntimeError(
                "SAP2000 is not installed or its COM registration is missing."
            )

        log.info("Launching SAP2000 from: %s", exe)
        subprocess.Popen([exe])

        # 3. Poll until SAP2000 registers itself in the COM Running Object Table
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(2)
            try:
                self._sap_obj = win32.GetActiveObject(_SAP_PROGID)
                log.info("SAP2000 COM server ready.")
                break
            except Exception:
                continue
        else:
            raise RuntimeError(
                "SAP2000 launched but did not register as a COM server within 60 s. "
                "Try opening SAP2000 manually first."
            )

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
