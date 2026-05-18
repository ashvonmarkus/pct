#!/usr/bin/env python3
from __future__ import annotations

import html
import csv
import json
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE = Path('/home/pi/.openclaw/workspace')
TOKEN_FILE = Path.home() / '.config/openclaw-secrets/google-token.json'
SPREADSHEET_ID = '1YKmJ1GwR2ZOfQQzrWQtNQ3JALGDj4NsxiNL-s2Fq8KU'
OUTPUT = BASE / 'index.html'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']


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


def main():
    service = build('sheets', 'v4', credentials=get_creds())
    dashboard = values(service, "'pct-dashboard'!A22:E80")
    dashboard_fmt = formatted(service, "'pct-dashboard'!A22:E80")
    # In der Tabelle die Durchschnitts-Spalte ausblenden; sie steht bereits in der Zusammenfassung/Chart.
    for row in dashboard_fmt:
        if len(row) > 3:
            del row[3]
    ramen = formatted(service, "'Ramen Index'!A1:C20")
    summary = formatted(service, "'Trackings'!A1:L5")
    off_trail = off_trail_miles()
    off_trail_html = '' if off_trail is None else f'<div class="metric"><b>Off Trail Miles getrackt</b>{fmt_de(off_trail)}</div>'

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
      <h1>PCT Dashboard</h1>
      <p class="muted">Quelle: <a href="https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}" target="_blank" rel="noopener">Google Sheet</a></p>
      <p class="muted">Aufrufe insgesamt: <img alt="Aufrufe insgesamt" src="https://hits.seeyoufarm.com/api/count/incr/badge.svg?url=https%3A%2F%2Fashvonmarkus.github.io%2Fpct%2F&count_bg=%2364748B&title_bg=%23CBD5E1&icon=&icon_color=%23E7E7E7&title=Aufrufe&edge_flat=false" style="vertical-align: middle; height: 20px;" /></p>
    </div>

    <section class="card chart-card">
      <h2>Meilen pro PCT-Tag</h2>
      <p class="muted">Tagesmeilen als Balken, kumulierte Meilen als Linie rechts.</p>
      <div class="chart-wrap"><canvas id="pctChart"></canvas></div>
    </section>

    <section class="card">
      <h2>Zusammenfassung</h2>
      <div class="grid">
        {''.join(f'<div class="metric"><b>{html.escape(str(row[i]))}</b>{html.escape(str(row[i+1]))}</div>' for row in summary[1:5] for i in range(0, min(len(row)-1, 12), 2) if row[i])}{off_trail_html}
      </div>
    </section>

    <section class="card scroll">
      <h2>pct-dashboard</h2>
      {table_html(dashboard_fmt)}
    </section>

    <section class="card scroll">
      <h2>Ramen Index</h2>
      {table_html(ramen)}
    </section>
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
