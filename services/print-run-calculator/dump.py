import openpyxl
import json
import os

wb = openpyxl.load_workbook(r'c:\Users\ookub\document-management-system\services\print-run-calculator\excel_templates\白色光の影を浚う.xlsm', data_only=True)
ws = wb.active

data = {}
for row in ws.iter_rows(max_row=50, max_col=20):
    for cell in row:
        if cell.value is not None:
            data[cell.coordinate] = str(cell.value)

with open('template_dump.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
