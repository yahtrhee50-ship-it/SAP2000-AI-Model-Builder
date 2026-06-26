"""
Diagnostic: connect to running SAP2000 and print raw return values from
SetRestraint and AreaObj.AddByPoint so we can fix the builder checks.
Run with SAP2000 already open (from the live build test).
"""
import pythoncom, comtypes, comtypes.client

TYPELIB_GUID = "{F896D55D-8BDF-4232-B9AB-4B210897A81D}"
SAP_CLSID    = "{B6B21850-FB75-41DE-85EC-BC9DBEC69BD3}"

pythoncom.CoInitialize()
lib   = comtypes.client.GetModule((TYPELIB_GUID, 1, 0))
clsid = comtypes.GUID(SAP_CLSID)

print("Attaching to running SAP2000 ...")
sap = comtypes.client.GetActiveObject(clsid, interface=lib.cOAPI)
m   = sap.SapModel
print("  SapModel type:", type(m).__name__)

# Init a fresh blank model
m.InitializeNewModel(4)   # kip_ft
m.File.NewBlank()
print("  New blank model OK")

# ----- Test PointObj.AddCartesian -----
print("\n-- PointObj.AddCartesian --")
r = m.PointObj.AddCartesian(0.0, 0.0, 0.0, "P1")
print("  raw return:", repr(r), "  type:", type(r).__name__)
if isinstance(r, (list, tuple)):
    print("  r[0]=", r[0], " r[1]=", r[1])
    jname = str(r[0])
else:
    jname = "P1"
print("  using joint name:", jname)

# ----- Test PointObj.SetRestraint -----
print("\n-- PointObj.SetRestraint --")
dof = [True, True, True, False, False, False]
ret = m.PointObj.SetRestraint(jname, dof)
print("  raw return:", repr(ret), "  type:", type(ret).__name__)
if ret is None:
    print("  -> None (treat as success)")
elif isinstance(ret, (int, float)):
    print("  -> int/float:", int(ret), "(0=success)")
else:
    print("  -> unexpected type")

# ----- Add 4 joints for area test -----
print("\n-- Adding 4 corner joints for area test --")
corners = [(10.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 10.0, 0.0), (10.0, 10.0, 0.0)]
jnames = []
for i, (x, y, z) in enumerate(corners):
    r2 = m.PointObj.AddCartesian(x, y, z, f"C{i}")
    print(f"  C{i}: raw={repr(r2)}")
    jnames.append(str(r2[0]) if isinstance(r2, (list, tuple)) else f"C{i}")
print("  jnames:", jnames)

# ----- Test AreaObj.AddByPoint -----
print("\n-- AreaObj.AddByPoint --")
ret2 = m.AreaObj.AddByPoint(4, jnames, "A1")
print("  raw return:", repr(ret2), "  type:", type(ret2).__name__)
if isinstance(ret2, (list, tuple)):
    print("  ret2[0]=", ret2[0], "  ret2[1]=", ret2[1])
elif ret2 is None:
    print("  -> None")
else:
    print("  -> scalar:", ret2)

print("\nDone.")
