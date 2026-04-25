import openpyxl
import json
import os

wb = openpyxl.load_workbook(r'c:\Users\ookub\document-management-system\services\print-run-calculator\excel_templates\白色光の影を浚う.xlsm', data_only=True)
ws = wb.active

formats = {
    'column_dimensions': {},
    'row_dimensions': {},
    'cells': {}
}

for col_letter, col_dim in ws.column_dimensions.items():
    formats['column_dimensions'][col_letter] = col_dim.width

for row_idx, row_dim in ws.row_dimensions.items():
    formats['row_dimensions'][row_idx] = row_dim.height

for row in ws.iter_rows():
    for cell in row:
        if cell.value is not None or cell.has_style:
            cell_format = {}
            if cell.font:
                cell_format['font'] = {
                    'name': cell.font.name,
                    'size': cell.font.size,
                    'bold': cell.font.bold,
                    'italic': cell.font.italic,
                    'color': str(cell.font.color.rgb) if cell.font.color and hasattr(cell.font.color, 'rgb') else None
                }
            if cell.fill and cell.fill.patternType:
                cell_format['fill'] = {
                    'patternType': cell.fill.patternType,
                    'fgColor': str(cell.fill.fgColor.rgb) if cell.fill.fgColor and hasattr(cell.fill.fgColor, 'rgb') else None
                }
            if cell.alignment:
                cell_format['alignment'] = {
                    'horizontal': cell.alignment.horizontal,
                    'vertical': cell.alignment.vertical,
                    'wrap_text': cell.alignment.wrap_text
                }
            if cell.border:
                cell_format['border'] = {
                    'top': cell.border.top.style if cell.border.top else None,
                    'bottom': cell.border.bottom.style if cell.border.bottom else None,
                    'left': cell.border.left.style if cell.border.left else None,
                    'right': cell.border.right.style if cell.border.right else None,
                }
            formats['cells'][cell.coordinate] = cell_format

with open('format_dump.json', 'w', encoding='utf-8') as f:
    json.dump(formats, f, indent=4, ensure_ascii=False)
