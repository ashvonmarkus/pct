#!/usr/bin/env python3
from __future__ import annotations

import html
import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE = Path('/home/pi/.openclaw/workspace')
TOKEN_FILE = Path.home() / '.config/openclaw-secrets/google-token.json'
SPREADSHEET_ID = '1YKmJ1GwR2ZOfQQzrWQtNQ3JALGDj4NsxiNL-s2Fq8KU'
OUTPUT = BASE / 'index.html'
PCT_START_DATE = date(2026, 5, 6)
PCT_TOTAL_MILES = 2650.0
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def get_creds():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
        TOKEN_FILE.chmod(0o600)
    return creds


def values(service, range_name: str):
    return service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueRenderOption='UNFORMATTED_VALUE',
    ).execute().get('values', [])


def formatted(service, range_name: str):
    return service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueRenderOption='FORMATTED_VALUE',
    ).execute().get('values', [])


def table_html(rows):
    if not rows:
        return '<p>No data.</p>'
    header, body = rows[0], rows[1:]
    out = ['<table><thead><tr>']
    out.extend(f'<th>{html.escape(str(c))}</th>' for c in header)
    out.append('</tr></thead><tbody>')
    for row in body:
        out.append('<tr>')
        for i in range(len(header)):
            out.append(f'<td>{html.escape(str(row[i])) if i < len(row) else ""}</td>')
        out.append('</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def pct_dashboard_table_html(rows):
    if not rows:
        return '<p>No data.</p>'
    header, body = rows[0], rows[1:]
    out = ['<table><thead><tr>']
    out.extend(f'<th>{html.escape(str(c))}</th>' for c in header)
    out.append('</tr></thead><tbody>')
    for row in body:
        tr_class = ''
        if len(row) > 1:
            try:
                miles = float(str(row[1]).replace(',', '.'))
                if miles == 0:
                    tr_class = ' class="zero-miles"'
            except ValueError:
                pass
        out.append(f'<tr{tr_class}>')
        for i in range(len(header)):
            out.append(f'<td>{html.escape(str(row[i])) if i < len(row) else ""}</td>')
        out.append('</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def off_trail_miles() -> float | None:
    """Tracked hiking miles minus latest projected PCT mile from check-ins."""
    garmin_csv = BASE / 'data/pct_stats_miles.csv'
    checkins_csv = BASE / 'pct_stats_checkins.csv'
    if not garmin_csv.exists() or not checkins_csv.exists():
        return None
    tracked = 0.0
    with garmin_csv.open(newline='') as f:
        for row in csv.DictReader(f):
            if row.get('type') != 'hiking':
                continue
            try:
                tracked += float(row.get('miles') or 0)
            except ValueError:
                pass
    try:
        import update_checkins_with_pct_miles as pct
        pts = pct.load_pct_points()
        pct_miles = []
        with checkins_csv.open(newline='') as f:
            for row in csv.DictReader(f):
                if not row.get('latitude') or not row.get('longitude'):
                    continue
                mile, _dist_m = pct.nearest_pct_mile(float(row['latitude']), float(row['longitude']), pts)
                pct_miles.append(mile)
        if not pct_miles:
            return None
        return max(0.0, tracked - max(pct_miles))
    except Exception:
        return None


def fmt_de(value: float) -> str:
    return f'{value:.2f}'.replace('.', ',')


def trail_progress() -> tuple[float, float] | None:
    """Return latest/max projected PCT mile and percent completed."""
    checkins_csv = BASE / 'pct_stats_checkins.csv'
    if not checkins_csv.exists():
        return None
    miles = []
    with checkins_csv.open(newline='') as f:
        for row in csv.DictReader(f):
            try:
                if row.get('pct_mile'):
                    miles.append(float(row['pct_mile']))
            except ValueError:
                continue
    if not miles:
        # Fallback: older/local CSV exports may not have pct_mile columns yet.
        # Project the inReach coordinates directly so the website progress bar
        # does not disappear between sync steps.
        try:
            import update_checkins_with_pct_miles as pct
            pts = pct.load_pct_points()
            with checkins_csv.open(newline='') as f:
                for row in csv.DictReader(f):
                    if not row.get('latitude') or not row.get('longitude'):
                        continue
                    mile, _dist_m = pct.nearest_pct_mile(float(row['latitude']), float(row['longitude']), pts)
                    miles.append(mile)
        except Exception:
            pass
    if not miles:
        return None
    marker = max(miles)
    return marker, min(100.0, max(0.0, marker / PCT_TOTAL_MILES * 100))


def format_hhmm(minutes: float) -> str:
    total = int(round(minutes))
    return f'{total // 60:02d}:{total % 60:02d}'



def elevation_by_day() -> dict[int, float]:
    garmin_csv = BASE / 'data/pct_stats_miles.csv'
    if not garmin_csv.exists():
        return {}
    meters_by_day: dict[int, float] = {}
    with garmin_csv.open(newline='') as f:
        for row in csv.DictReader(f):
            if row.get('type') != 'hiking':
                continue
            try:
                day = (datetime.strptime(row.get('date_local', ''), '%Y-%m-%d %H:%M:%S').date() - PCT_START_DATE).days + 1
                if day < 1:
                    continue
                meters_by_day[day] = meters_by_day.get(day, 0.0) + float(row.get('elevation_gain_m') or 0)
            except Exception:
                continue
    return meters_by_day

def activity_time_by_day() -> dict[int, str]:
    garmin_csv = BASE / 'data/pct_stats_miles.csv'
    if not garmin_csv.exists():
        return {}
    minutes_by_day: dict[int, float] = {}
    with garmin_csv.open(newline='') as f:
        for row in csv.DictReader(f):
            if row.get('type') != 'hiking':
                continue
            try:
                day = (datetime.strptime(row.get('date_local', ''), '%Y-%m-%d %H:%M:%S').date() - PCT_START_DATE).days + 1
                if day < 1:
                    continue
                minutes_by_day[day] = minutes_by_day.get(day, 0.0) + float(row.get('duration_min') or 0)
            except Exception:
                continue
    return {day: format_hhmm(minutes) for day, minutes in minutes_by_day.items()}


def dashboard_table_rows(rows: list[list[str]], elevation_by_day_m: dict[int, float]) -> list[list[str]]:
    if not rows:
        return []
    out = [[str(rows[0][0]), str(rows[0][1]), str(rows[0][2]), 'Höhenmeter']]
    for row in rows[1:]:
        if len(row) < 3 or row[0] == '':
            continue
        try:
            day = int(float(str(row[0]).replace(',', '.')))
        except Exception:
            day = None
        out.append([str(row[0]), str(row[1]), str(row[2]), fmt_de(elevation_by_day_m.get(day, 0.0)).rstrip('0').rstrip(',')])
    return out


def trim_trailing_zero_days(rows: list[list]) -> list[list]:
    """Keep real/rest days, but remove empty zero-mile days after the last activity.

    The dashboard sheet may contain formula rows up to "today". If Garmin has
    not synced a hike for the latest day yet, those rows evaluate to 0 and make
    the website look like the day/mileage data shifted. Zero-mile rest days in
    the middle are preserved; only the run of zero rows at the end is removed.
    """
    if len(rows) <= 1:
        return rows
    header, body = rows[0], list(rows[1:])
    while body:
        row = body[-1]
        if len(row) < 2 or row[0] == '':
            body.pop()
            continue
        try:
            miles = float(str(row[1] or 0).replace(',', '.'))
        except Exception:
            break
        if miles == 0:
            body.pop()
            continue
        break
    return [header] + body


def summary_html(summary: list[list[str]], required_miles: float | None) -> str:
    out = []
    for row in summary[1:5]:
        for i in range(0, min(len(row) - 1, 12), 2):
            if not row[i]:
                continue
            label = str(row[i])
            value = str(row[i + 1])
            if required_miles is not None and label == 'benötigt mi/Tag':
                value = fmt_de(required_miles)
            elif required_miles is not None and label == 'benötigt km/Tag':
                value = fmt_de(required_miles * 1.609344)
            out.append(f'<div class="metric"><b>{html.escape(label)}</b>{html.escape(value)}</div>')
    return ''.join(out)


def compute_section_stats(labels, daily):
    """Compute average miles per PCT section (desert <=44, sierra >44)."""
    desert_miles = 0.0
    desert_days = 0
    sierra_miles = 0.0
    sierra_days = 0
    for day, miles in zip(labels, daily):
        try:
            d = int(day)
            m = float(miles)
        except Exception:
            continue
        if d <= 44:
            if m > 0:
                desert_miles += m
                desert_days += 1
        else:
            if m > 0:
                sierra_miles += m
                sierra_days += 1
    desert_avg = desert_miles / desert_days if desert_days else 0.0
    sierra_avg = sierra_miles / sierra_days if sierra_days else 0.0
    return {
        'desert': {'total_miles': desert_miles, 'days': desert_days, 'avg': desert_avg},
        'sierra': {'total_miles': sierra_miles, 'days': sierra_days, 'avg': sierra_avg}
    }

def section_bg_color(latest_day):
    """Return a very transparent background color for chart based on latest day."""
    if latest_day <= 44:
        # desert tint: light brown
        return 'rgba(210,180,140,0.05)'
    else:
        # mountain tint: light blue
        return 'rgba(100,148,237,0.05)'

def section_stats_html(stats):
    """Generate HTML collapsible section for PCT sections."""
    out = ['<details class="card">']
    out.append('<summary><h2>Abschnitte</h2></summary>')
    out.append('<div class="grid">')
    for name, data in stats.items():
        name_display = 'Wüste' if name == 'desert' else 'Sierra'
        out.append(f'<div class="metric"><b>{name_display}</b>')
        out.append(f'<div>Ø {fmt_de(data["avg"])} mi/Tag</div>')
        out.append(f'<div>Gesammeilen: {fmt_de(data["total_miles"])}</div>')
        out.append(f'<div>Tage mit Daten: {data["days"]}</div>')
        out.append('</div>')
    out.append('</div>')
    out.append('</details>')
    return ''.join(out)


def main():
    service = build('sheets', 'v4', credentials=get_creds())
    last_updated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    dashboard = trim_trailing_zero_days(values(service, "'pct-dashboard'!A22:E200"))
    dashboard_fmt = trim_trailing_zero_days(formatted(service, "'pct-dashboard'!A22:E200"))
    # Unten nur Tageswerte plus Aktivitätszeit zeigen; Durchschnitte/Zieltempo stehen oben bzw. im Chart.
    elevation_daily = elevation_by_day()
    dashboard_fmt = dashboard_table_rows(dashboard_fmt, elevation_daily)
    # Reverse rows for table (newest day on top) while keeping header
    if len(dashboard_fmt) > 1:
        table_rows = [dashboard_fmt[0]] + list(reversed(dashboard_fmt[1:]))
    else:
        table_rows = dashboard_fmt
    ramen = formatted(service, "'Ramen Index'!A1:B20")
    summary = formatted(service, "'Trackings'!A1:L5")
    off_trail = off_trail_miles()
    progress = trail_progress()
    if progress:
        marker, percent = progress
        progress_html = f'''<div class="progress-block">
        <div class="progress-row"><b>PCT Fortschritt</b><span>{fmt_de(marker)} mi / {fmt_de(PCT_TOTAL_MILES)} mi · {fmt_de(percent)}%</span></div>
        <div class="progress-track"><div class="progress-fill" style="width: {percent:.2f}%"></div></div>
      </div>'''
    else:
        progress_html = ''
    elevation_total = sum(elevation_daily.values())
    elevation_avg = elevation_total / len(elevation_daily) if elevation_daily else 0.0
    off_trail_html = '' if off_trail is None else f'<div class="metric"><b>Off Trail Miles getrackt</b>{fmt_de(off_trail)}</div>'
    elevation_overview_html = f'''<details class="card">
      <summary><h2>Höhenmeter Übersicht</h2></summary>
      <div class="grid">
        <div class="metric"><b>Gesamthöhenmeter</b>{fmt_de(elevation_total).rstrip('0').rstrip(',')} m</div>
        <div class="metric"><b>Ø Höhenmeter pro Aktivitätstag</b>{fmt_de(elevation_avg).rstrip('0').rstrip(',')} m</div>
      </div>
    </details>'''

    chart_rows = []
    for row in dashboard[1:]:
        if len(row) < 5 or row[0] == '':
            continue
        try:
            chart_rows.append({
                'day': int(row[0]),
                'dailyMiles': float(row[1] or 0),
                'avgMiles': float(row[3] or 0),
                'requiredMiles': float(row[4] or 0),
            })
        except Exception:
            continue

    labels = [r['day'] for r in chart_rows]
    daily = [r['dailyMiles'] for r in chart_rows]
    cumulative = []
    running = 0.0
    for miles in daily:
        running += miles
        cumulative.append(round(running, 2))
    avg = [r['avgMiles'] for r in chart_rows]
    required = [r['requiredMiles'] for r in chart_rows]
    required_miles = required[0] if required else None
    summary_cards = summary_html(summary, required_miles) + off_trail_html

    doc = f'''<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PCT Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {{ color-scheme: light; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; margin: 0; background: #f5f7fb; color: #1f2937; }}
    .container {{ max-width: 1180px; margin: 0 auto; padding: 14px; }}
    .card {{ background: #fff; border-radius: 16px; padding: 16px; margin: 0 0 16px; box-shadow: 0 8px 28px rgba(15,23,42,.08); }}
    details.card summary {{ list-style: none; cursor: pointer; display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    details.card summary::-webkit-details-marker {{ display: none; }}
    details.card summary h2 {{ margin: 0; }}
    details.card summary::after {{ content: '▾'; color: #64748b; font-size: 1.1rem; transition: transform .18s ease; }}
    details.card:not([open]) summary::after {{ transform: rotate(-90deg); }}
    details.card[open] summary {{ margin-bottom: 14px; }}
    h1 {{ margin: 0 0 6px; }}
    h2 {{ margin: 0 0 14px; }}
    .muted {{ color: #64748b; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .metric {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:12px; }}
    .metric b {{ display:block; font-size:.85rem; color:#64748b; margin-bottom:6px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: .92rem; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 9px 8px; text-align: left; }}
    th {{ background: #f8fafc; position: sticky; top:0; }}
    .scroll {{ overflow-x:auto; }}
    .chart-card {{ padding: 14px 10px 16px; }}
    .chart-wrap {{ position: relative; height: min(72vh, 520px); min-height: 360px; }}
    .progress-block {{ margin: 14px 6px 2px; padding: 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; }}
    .progress-row {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; color: #334155; }}
    .progress-row span {{ color: #64748b; font-variant-numeric: tabular-nums; }}
    .progress-track {{ height: 16px; margin-top: 10px; background: #e2e8f0; border-radius: 999px; overflow: hidden; }}
    .progress-fill {{ height: 100%; background: linear-gradient(90deg, #2563eb, #16a34a); border-radius: inherit; }}
    .zero-miles {{ background-color: #ffe0b2; }}
    canvas {{ width: 100% !important; height: 100% !important; }}
    @media (max-width: 640px) {{
      h1 {{ font-size: 1.35rem; }}
      h2 {{ font-size: 1.05rem; }}
      .container {{ padding: 8px; }}
      .card {{ border-radius: 12px; padding: 12px; margin-bottom: 12px; }}
      .chart-card {{ padding: 10px 6px 12px; }}
      .chart-wrap {{ height: 68vh; min-height: 420px; }}
      table {{ font-size: .82rem; }}
      th, td {{ padding: 7px 6px; }}
    }}
  </style>
</head>
<body>
  <main class="container">
    <div class="card">
      <p class="muted">Letzte Aktualisierung: {last_updated}</p>
      <p class="muted">insgesamt: <img alt="Aufrufe insgesamt" src="https://hits.sh/ashvonmarkus.github.io/pct.svg?label=Aufrufe&color=64748b&labelColor=cbd5e1" style="vertical-align: middle; height: 20px;" /></p>
      {progress_html}
    </div>

    <details class="card chart-card" open>
      <summary><h2>Meilen pro PCT-Tag</h2></summary>
      <p class="muted">Tagesmeilen als Balken, kumulierte Meilen als Linie rechts.</p>
      <div class="chart-wrap"><canvas id="pctChart"></canvas></div>
    </details>

    <details class="card" open>
      <summary><h2>Zusammenfassung</h2></summary>
      <div class="grid">
        {summary_cards}
      </div>
    </details>

    <details class="card scroll">
      <summary><h2>PCT-Tage aus Garmin</h2></summary>
      {pct_dashboard_table_html(table_rows)}
    </details>

    {elevation_overview_html}

    <details class="card scroll">
      <summary><h2>Ramen Index</h2></summary>
      {table_html(ramen)}
    </details>
  </main>

<script>
const labels = {json.dumps(labels)};
const daily = {json.dumps(daily)};
const cumulative = {json.dumps(cumulative)};
const avg = {json.dumps(avg)};
const required = {json.dumps(required)};
const isMobile = window.matchMedia('(max-width: 640px)').matches;
new Chart(document.getElementById('pctChart'), {{
  data: {{
    labels,
    datasets: [
      {{
        type: 'bar',
        label: 'Meilen pro Tag',
        data: daily,
        yAxisID: 'y',
        borderColor: '#2563eb',
        backgroundColor: 'rgba(37,99,235,.72)',
        borderRadius: 8,
        maxBarThickness: isMobile ? 22 : 34
      }},
      {{
        type: 'line',
        label: 'Kumulierte Meilen',
        data: cumulative,
        yAxisID: 'y1',
        borderColor: '#f97316',
        backgroundColor: '#f97316',
        borderWidth: 3,
        tension: .25,
        pointRadius: isMobile ? 2 : 3
      }},
      {{ label: 'Ø mi/Tag', type: 'line', data: avg, yAxisID: 'y', borderColor: '#16a34a', borderDash: [6,4], borderWidth: 2, tension: .1, pointRadius: 0 }},
      {{ label: 'benötigte mi/Tag', type: 'line', data: required, yAxisID: 'y', borderColor: '#dc2626', borderDash: [2,4], borderWidth: 2, tension: .1, pointRadius: 0 }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: isMobile ? 10 : 14, font: {{ size: isMobile ? 11 : 13 }} }} }},
      tooltip: {{ callbacks: {{ label: ctx => `${{ctx.dataset.label}}: ${{Number(ctx.parsed.y).toFixed(2)}} mi` }} }}
    }},
    scales: {{
      x: {{ title: {{ display: !isMobile, text: 'PCT Tag' }}, ticks: {{ maxRotation: 0, autoSkip: true }} }},
      y: {{ position: 'left', title: {{ display: true, text: 'Tagesmeilen' }}, beginAtZero: true, grid: {{ color: 'rgba(148,163,184,.22)' }} }},
      y1: {{ position: 'right', title: {{ display: true, text: 'Kumuliert mi' }}, beginAtZero: true, grid: {{ drawOnChartArea: false }} }}
    }}
  }}
}});
</script>
</body>
</html>
'''
    OUTPUT.write_text(doc)
    print({'written': str(OUTPUT), 'chart_points': len(chart_rows)})


if __name__ == '__main__':
    main()
