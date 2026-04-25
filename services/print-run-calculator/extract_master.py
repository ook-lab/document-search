import openpyxl
import json

wb = openpyxl.load_workbook(r'c:\Users\ookub\document-management-system\services\print-run-calculator\excel_templates\白色光の影を浚う.xlsm')
ws = wb.active

elements = []
for row in ws.iter_rows():
    for cell in row:
        if cell.value is not None:
            coord = cell.coordinate
            val = cell.value
            
            # Determine type
            if isinstance(val, str) and val.startswith('='):
                el_type = 'formula'
                value = val
            elif isinstance(val, (int, float)):
                el_type = 'number'
                value = val
            else:
                el_type = 'text'
                value = str(val)
                
            elements.append({
                "coord": coord,
                "type": el_type,
                "value": value
            })

with open('extracted_elements.json', 'w', encoding='utf-8') as f:
    json.dump(elements, f, indent=4, ensure_ascii=False)
