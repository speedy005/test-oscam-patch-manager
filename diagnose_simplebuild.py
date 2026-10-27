from windows_msys2_setup import (
    diagnose_system,
    diagnose_tool,
    get_build_status,
    list_build_logs,
    analyse_build_log,
    write_diagnostic_report,
)

# 1. System prüfen – kein Build wird gestartet.
system = diagnose_system()

# 2. Ein Tool prüfen – kein Klonen und kein Fetch.
tool_report = diagnose_tool(
    "s3_gorgone",
    base_dir=r"C:\Simplebuild",
)

# 3. Diagnosebericht speichern.
report_path = write_diagnostic_report({
    "system": system,
    "s3_gorgone": tool_report,
})

# 4. Alle bekannten Build-Logs anzeigen.
for item in list_build_logs():
    print(item["tool"], item["status"], item["path"])

# 5. Status für ein bestimmtes Tool abfragen.
print(get_build_status("s3_speedy005"))

# 6. Ein konkretes Log analysieren.
# log_path muss auf eine existierende Logdatei zeigen.
# analysis = analyse_build_log(log_path)