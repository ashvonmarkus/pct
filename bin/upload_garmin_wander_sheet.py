#!/usr/bin/env python3
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

BASE = Path('/home/pi/.openclaw/workspace')
CSV_FILE = BASE / 'data/pct_stats_miles.csv'
TOKEN_FILE = Path.home() / '.config/openclaw-secrets/google-token.json'
SPREADSHEET_ID = '1YKmJ1GwR2ZOfQQzrWQtNQ3JALGDj4NsxiNL-s2Fq8KU'
TAB_NAME = 'Trackings'
DASHBOARD_TAB = 'pct-dashboard'
DASHBOARD_DATA_START_ROW = 22
SHARE_WITH = ['markusweissofner@gmail.com', 'komgneh@gmail.com']
START_DATE = datetime(2026, 5, 6)
END_DATE = datetime(2026, 9, 20)
PCT_TOTAL_MILES = 2650.0
PCT_TOTAL_KM = PCT_TOTAL_MILES * 1.609344
TRAIL_TZ = ZoneInfo('America/Los_Angeles')
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
]


def get_creds():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
        TOKEN_FILE.chmod(0o600)
    return creds


def read_rows():
    with CSV_FILE.open(newline='') as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        try:
            dt = datetime.strptime(r['date_local'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        if dt < START_DATE:
            continue
        # "Wandern" activities in Garmin are typeKey=hiking.
        if r.get('type') != 'hiking':
            continue
        out.append(r)
    out.sort(key=lambda r: r['date_local'])
    return out


def ensure_tab(sheets, title=TAB_NAME):
    meta = sheets.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta.get('sheets', []):
        if s['properties']['title'] == title:
            return s['properties']['sheetId']
    req = {'addSheet': {'properties': {'title': title}}}
    resp = sheets.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={'requests': [req]},
    ).execute()
    return resp['replies'][0]['addSheet']['properties']['sheetId']


def delete_charts(sheets, sheet_id):
    meta = sheets.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    requests = []
    for sheet in meta.get('sheets', []):
        if sheet.get('properties', {}).get('sheetId') != sheet_id:
            continue
        for chart in sheet.get('charts', []):
            requests.append({'deleteEmbeddedObject': {'objectId': chart['chartId']}})
    if requests:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={'requests': requests},
        ).execute()


def replace_miles_per_day_chart(sheets, dashboard_sheet_id, row_count):
    delete_charts(sheets, dashboard_sheet_id)
    start_row_index = DASHBOARD_DATA_START_ROW - 1
    end_row = start_row_index + max(2, row_count)
    requests = []

    requests.append({
        'addChart': {
            'chart': {
                'spec': {
                    'title': 'Meilen pro PCT-Tag vs. Schnitt und Zieltempo',
                    'basicChart': {
                        'chartType': 'LINE',
                        'legendPosition': 'BOTTOM_LEGEND',
                        'axis': [
                            {'position': 'BOTTOM_AXIS', 'title': 'PCT Tag'},
                            {'position': 'LEFT_AXIS', 'title': 'Meilen'},
                        ],
                        'domains': [{
                            'domain': {
                                'sourceRange': {
                                        'sources': [{
                                        'sheetId': dashboard_sheet_id,
                                        'startRowIndex': start_row_index,
                                        'endRowIndex': end_row,
                                        'startColumnIndex': 0,
                                        'endColumnIndex': 1,
                                    }]
                                }
                            }
                        }],
                        'series': [{
                            'series': {
                                    'sourceRange': {
                                        'sources': [{
                                        'sheetId': dashboard_sheet_id,
                                        'startRowIndex': start_row_index,
                                        'endRowIndex': end_row,
                                        'startColumnIndex': 1,
                                        'endColumnIndex': 2,
                                    }]
                                }
                            },
                            'targetAxis': 'LEFT_AXIS',
                            'type': 'LINE',
                        }, {
                            'series': {
                                'sourceRange': {
                                    'sources': [{
                                        'sheetId': dashboard_sheet_id,
                                        'startRowIndex': start_row_index,
                                        'endRowIndex': end_row,
                                        'startColumnIndex': 3,
                                        'endColumnIndex': 4,
                                    }]
                                }
                            },
                            'targetAxis': 'LEFT_AXIS',
                            'type': 'LINE',
                        }, {
                            'series': {
                                'sourceRange': {
                                    'sources': [{
                                        'sheetId': dashboard_sheet_id,
                                        'startRowIndex': start_row_index,
                                        'endRowIndex': end_row,
                                        'startColumnIndex': 4,
                                        'endColumnIndex': 5,
                                    }]
                                }
                            },
                            'targetAxis': 'LEFT_AXIS',
                            'type': 'LINE',
                        }],
                        'headerCount': 1,
                    },
                },
                'position': {
                    'overlayPosition': {
                        'anchorCell': {
                            'sheetId': dashboard_sheet_id,
                            'rowIndex': 0,
                            'columnIndex': 0,
                        },
                        'widthPixels': 430,
                        'heightPixels': 300,
                    }
                },
            }
        }
    })
    if requests:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={'requests': requests},
        ).execute()


def format_tab(sheets, sheet_id):
    # Column A is the PCT day counter. It previously inherited a date format in
    # the sheet, so force it back to a plain integer.
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            'requests': [
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startColumnIndex': 0,
                            'endColumnIndex': 1,
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'numberFormat': {'type': 'NUMBER', 'pattern': '0'}
                            }
                        },
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                }
            ]
        },
    ).execute()
    delete_charts(sheets, sheet_id)


def format_dashboard(sheets, dashboard_sheet_id, row_count):
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            'requests': [
                {
                    'updateSheetProperties': {
                        'properties': {'sheetId': dashboard_sheet_id, 'index': 0},
                        'fields': 'index',
                    }
                },
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': dashboard_sheet_id,
                            'startColumnIndex': 0,
                            'endColumnIndex': 1,
                        },
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': dashboard_sheet_id,
                            'startColumnIndex': 1,
                            'endColumnIndex': 5,
                        },
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.00'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
            ]
        },
    ).execute()
    replace_miles_per_day_chart(sheets, dashboard_sheet_id, row_count)


def ensure_spreadsheet_timezone(sheets):
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            'requests': [{
                'updateSpreadsheetProperties': {
                    'properties': {'timeZone': 'America/Los_Angeles'},
                    'fields': 'timeZone',
                }
            }]
        },
    ).execute()


def upload_values(sheets, rows, sheet_id, dashboard_sheet_id):
    ensure_spreadsheet_timezone(sheets)
    headers = [
        'Day', 'Datum lokal', 'Name', 'Typ', 'Meilen', 'Kilometer', 'Distanz m',
        'Dauer min', 'Höhenmeter m', 'Schritte', 'Start Lat', 'Start Lon',
        'End Lat', 'End Lon', 'Ort', 'Garmin Activity ID'
    ]
    values = []
    start_day = START_DATE.date()
    # Build dashboard days only through the latest Garmin activity day.
    # Rest days *between* activities stay visible as zero-mile days, but we do
    # not add trailing zero days for today/future before Garmin has data. Those
    # trailing rows made the GitHub page look shifted and hid the latest real
    # high-mileage day below a bogus 0-mile row.
    max_day = 1
    values.append(headers)
    for r in rows:
        dist_m = float(r.get('distance_m') or 0)
        try:
            day_number = (datetime.strptime(r.get('date_local', ''), '%Y-%m-%d %H:%M:%S').date() - start_day).days + 1
        except Exception:
            day_number = ''
        if isinstance(day_number, int):
            max_day = max(max_day, day_number)
        values.append([
            day_number,
            r.get('date_local', ''),
            r.get('activity_name', ''),
            r.get('type', ''),
            float(r.get('miles') or 0),
            round(dist_m / 1000, 2),
            dist_m,
            float(r.get('duration_min') or 0),
            float(r.get('elevation_gain_m') or 0),
            int(float(r.get('steps') or 0)) if r.get('steps') else '',
            r.get('start_lat', ''),
            r.get('start_lon', ''),
            r.get('end_lat', ''),
            r.get('end_lon', ''),
            r.get('location', ''),
            r.get('activity_id', ''),
        ])
    sheets.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{TAB_NAME}'!A:Z",
        body={},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{TAB_NAME}'!A7",
        valueInputOption='RAW',
        body={'values': values},
    ).execute()
    summary_values = [
        ['Zusammenfassung', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''],
        ['Startdatum', START_DATE.strftime('%Y-%m-%d'), 'Zieldatum', END_DATE.strftime('%Y-%m-%d'), 'PCT Gesamt mi', PCT_TOTAL_MILES, 'PCT Gesamt km', '=F2*1609344/1000000', '', '', '', '', '', '', '', ''],
        ['Aktivitäten', '=COUNTA(P8:P)', 'Tage seit Start', '=MAX(1;MIN(TODAY();D2)-B2+1)', 'Meilen gesamt', '=SUM(E8:E)', 'Kilometer gesamt', '=SUM(F8:F)', 'Höhenmeter gesamt', '=SUM(I8:I)', 'Schritte gesamt', '=SUM(J8:J)', '', '', '', ''],
        ['Durchschnitt pro PCT-Tag', '', '', '', 'mi/Tag', '=IFERROR(F3/D3;0)', 'km/Tag', '=IFERROR(H3/D3;0)', 'Schritte/Tag', '=IFERROR(L3/D3;0)', '', '', '', '', '', ''],
        ['Benötigt bis Ziel', '', 'Tage verbleibend inkl. heute', '=MAX(1;D2-MIN(TODAY();D2)+1)', 'Rest mi', '=MAX(0;F2-MAX(\'check-ins\'!K2:K))', 'Rest km', '=F5*1609344/1000000', 'benötigt mi/Tag', '=IFERROR(F5/D5;0)', 'benötigt km/Tag', '=IFERROR(H5/D5;0)', '', '', '', ''],
    ]
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{TAB_NAME}'!A1",
        valueInputOption='USER_ENTERED',
        body={'values': summary_values},
    ).execute()
    sheets.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{TAB_NAME}'!R:S",
        body={},
    ).execute()
    format_tab(sheets, sheet_id)

    dashboard_summary = [
        ['Gesamtsumme', '', '', '', ''],
        ['Meilen gesamt', f'=\'{TAB_NAME}\'!F3', '', 'Kilometer gesamt', f'=\'{TAB_NAME}\'!H3'],
        ['Tage seit Start', f'=\'{TAB_NAME}\'!D3', '', 'Ø mi/Tag', f'=\'{TAB_NAME}\'!F4'],
    ]
    dashboard_values = [['Day', 'Meilen pro Tag', 'Kilometer pro Tag', 'Ø gegangene mi/Tag', 'benötigte mi/Tag']]
    for day in range(1, max_day + 1):
        row_idx = DASHBOARD_DATA_START_ROW + day
        dashboard_values.append([
            day,
            f'=SUMIF(\'{TAB_NAME}\'!A:A;A{row_idx};\'{TAB_NAME}\'!E:E)',
            f'=SUMIF(\'{TAB_NAME}\'!A:A;A{row_idx};\'{TAB_NAME}\'!F:F)',
            f'=\'{TAB_NAME}\'!F4',
            f'=\'{TAB_NAME}\'!J5',
        ])
    sheets.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{DASHBOARD_TAB}'!A:Z",
        body={},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{DASHBOARD_TAB}'!F1",
        valueInputOption='USER_ENTERED',
        body={'values': dashboard_summary},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{DASHBOARD_TAB}'!A{DASHBOARD_DATA_START_ROW}",
        valueInputOption='USER_ENTERED',
        body={'values': dashboard_values},
    ).execute()
    format_dashboard(sheets, dashboard_sheet_id, len(dashboard_values))
    return {'count': len(rows), 'url': f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=0'}


def share(drive):
    try:
        perms = drive.permissions().list(fileId=SPREADSHEET_ID, fields='permissions(emailAddress,role)').execute().get('permissions', [])
    except HttpError as e:
        if e.resp.status == 403:
            return {'skipped': 'insufficient_drive_permission_scope'}
        raise
    results = {}
    for email in SHARE_WITH:
        match = next((p for p in perms if p.get('emailAddress') == email), None)
        if match:
            results[email] = f"already_shared_as_{match.get('role')}"
            continue
        try:
            drive.permissions().create(
                fileId=SPREADSHEET_ID,
                body={'type': 'user', 'role': 'writer', 'emailAddress': email},
                sendNotificationEmail=True,
                fields='id',
            ).execute()
            results[email] = 'created'
        except HttpError as e:
            if e.resp.status in (400, 403, 409):
                perms = drive.permissions().list(fileId=SPREADSHEET_ID, fields='permissions(emailAddress,role)').execute().get('permissions', [])
                for p in perms:
                    if p.get('emailAddress') == email:
                        results[email] = f"already_shared_as_{p.get('role')}"
                        break
                if email in results:
                    continue
            raise
    return results


def main():
    creds = get_creds()
    sheets = build('sheets', 'v4', credentials=creds)
    drive = build('drive', 'v3', credentials=creds)
    sheet_id = ensure_tab(sheets, TAB_NAME)
    dashboard_sheet_id = ensure_tab(sheets, DASHBOARD_TAB)
    rows = read_rows()
    info = upload_values(sheets, rows, sheet_id, dashboard_sheet_id)
    info['share'] = share(drive)
    print(info)


if __name__ == '__main__':
    main()
