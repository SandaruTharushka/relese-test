import win32print
import json

def debug_printers():
    print(f"PRINTER_ATTRIBUTE_WORK_OFFLINE: {win32print.PRINTER_ATTRIBUTE_WORK_OFFLINE:08x}")
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    printers = win32print.EnumPrinters(flags, None, 2)
    results = []
    for p in printers:
        name = p.get("pPrinterName") or p.get("PrinterName")
        try:
            handle = win32print.OpenPrinter(name)
            info = win32print.GetPrinter(handle, 2)
            results.append({
                "Name": name,
                "pPortName": info.get("pPortName"),
                "PortName": info.get("PortName"),
                "Attributes": info.get("Attributes"),
                "Status": info.get("Status")
            })
            win32print.ClosePrinter(handle)
        except Exception as e:
            results.append({"Name": name, "Error": str(e)})
    
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    debug_printers()
