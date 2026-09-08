import json, openpyxl
from pathlib import Path
deleted_rows = json.load(open('outputs/supabase_deleted_records_current.json', encoding='utf-8-sig'))
deleted = {r['record_id'] for r in deleted_rows if r.get('record_id')}
wb = openpyxl.load_workbook('outputs/s2b_custom_tabs_deleted_excluded/s2b_custom_tabs_deleted_excluded.xlsx', read_only=True, data_only=True)
bad = []
for name in wb.sheetnames:
    ws = wb[name]
    for row in ws.iter_rows(min_row=3, values_only=True):
        tender = str(row[11] or '')
        if tender in deleted:
            bad.append((name, tender, row[2], row[4]))
            if len(bad) >= 5:
                break
    if len(bad) >= 5:
        break
print('deleted_ids_in_workbook', len(bad))
print('sample', bad)
