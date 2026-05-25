#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import pathlib
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path('/home/pi/.openclaw/workspace')
CHECKINS_CSV = BASE / 'pct_stats_checkins.csv'
TRAIL_CODE = 'pct'
PROCESSED_URL = f'https://s3.amazonaws.com/www.longtrailsweather.net/forecasts/processed/{TRAIL_CODE}.json'
DETAIL_URL = 'https://s3.amazonaws.com/www.longtrailsweather.net/forecasts/detail/{trail}/{index:03d}.json'
TELEGRAM_TARGET = '8353258346'


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw/Ash PCT weather report'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def latest_position() -> dict:
    with CHECKINS_CSV.open(newline='') as f:
        rows = list(csv.DictReader(f))
    positioned = [r for r in rows if r.get('latitude') and r.get('longitude')]
    if not positioned:
        raise RuntimeError('No positioned check-ins found')
    positioned.sort(key=lambda r: r.get('timestamp_utc', ''))
    row = positioned[-1]
    return {
        'timestamp_utc': row.get('timestamp_utc', ''),
        'lat': float(row['latitude']),
        'lon': float(row['longitude']),
        'pct_mile': row.get('pct_mile', ''),
    }


def nearest_forecast(position: dict, processed: dict) -> dict:
    forecasts = processed.get('forecasts') or []
    if not forecasts:
        raise RuntimeError('No LongTrailsWeather forecasts found')
    return min(
        forecasts,
        key=lambda f: haversine_km(position['lat'], position['lon'], float(f['lat']), float(f['lon'])),
    )


def f_to_c(value: float | int | None) -> float | None:
    if value is None:
        return None
    return (float(value) - 32.0) * 5.0 / 9.0


def mph_to_kmh(value: float | int | None) -> float | None:
    if value is None:
        return None
    return float(value) * 1.609344


def inches_to_mm(value: float | int | None) -> float | None:
    if value is None:
        return None
    return float(value) * 25.4


def fmt_c(fahrenheit: float | int | None) -> str:
    c = f_to_c(fahrenheit)
    return '-' if c is None else f'{c:.1f} °C'


def fmt_kmh(mph: float | int | None) -> str:
    kmh = mph_to_kmh(mph)
    return '-' if kmh is None else f'{kmh:.0f} km/h'


def fmt_mm(inches: float | int | None) -> str:
    mm = inches_to_mm(inches)
    return '-' if mm is None else f'{mm:.1f} mm'


def bearing_to_compass(deg: float | int | None) -> str:
    if deg is None:
        return '-'
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return dirs[int((float(deg) + 11.25) / 22.5) % 16]


def day_label(epoch: int | float) -> str:
    dt = datetime.fromtimestamp(float(epoch), ZoneInfo('America/Los_Angeles'))
    return dt.strftime('%a %d.%m.')


def precip_line(day: dict) -> str:
    precip_type = day.get('precipType') or 'precip'
    probability = day.get('precipProbability')
    probability_pct = '-' if probability is None else f'{round(float(probability) * 100)}%'
    amount = fmt_mm(day.get('precipAccumulation'))
    if probability in (None, 0, 0.0) and (day.get('precipAccumulation') in (None, 0, 0.0)):
        return 'Precip: 0%, 0.0 mm'
    return f'Precip: {precip_type}, {probability_pct}, {amount}'


def build_report() -> str:
    position = latest_position()
    processed = fetch_json(PROCESSED_URL)
    forecast = nearest_forecast(position, processed)
    detail = fetch_json(DETAIL_URL.format(trail=TRAIL_CODE, index=int(forecast['location_index'])))
    days = (detail.get('days') or forecast.get('days') or [])[:2]
    if len(days) < 2:
        raise RuntimeError('Less than two forecast days available')

    forecast_distance_km = haversine_km(position['lat'], position['lon'], float(forecast['lat']), float(forecast['lon']))
    updated = processed.get('last_modified', '')
    try:
        updated_dt = datetime.fromisoformat(updated.replace('Z', '+00:00')).astimezone(ZoneInfo('America/Los_Angeles'))
        updated_text = updated_dt.strftime('%Y-%m-%d %H:%M Pacific Time')
    except Exception:
        updated_text = updated

    lines = [
        'LongTrailsWeather PCT Report',
        '',
        f"Current position: {position['lat']:.6f}, {position['lon']:.6f}" + (f" · PCT mile {position['pct_mile']}" if position.get('pct_mile') else ''),
        f"Nearest LTWx point: {forecast.get('location_name') or 'unnamed'} · mile {forecast.get('distance')} · {forecast_distance_km:.1f} km away",
        f'Forecast updated: {updated_text}',
        '',
    ]

    for day in days:
        wind = fmt_kmh(day.get('windSpeed'))
        gust = fmt_kmh(day.get('windGust'))
        direction = bearing_to_compass(day.get('windBearing'))
        lines.extend([
            day_label(day['time']),
            f"• {day.get('summary') or day.get('icon') or 'No summary'}",
            f"• High/Low: {fmt_c(day.get('temperatureHigh'))} / {fmt_c(day.get('temperatureLow'))}",
            f"• Feels like low: {fmt_c(day.get('apparentTemperatureLow'))}",
            f"• {precip_line(day)}",
            f"• Wind: {wind}, gusts {gust}, {direction}",
            f"• UV: {day.get('uvIndex', '-')}",
            '',
        ])
    return '\n'.join(lines).strip()


def send_telegram(text: str) -> None:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            subprocess.run([
                '/home/pi/.npm-global/bin/openclaw', 'message', 'send',
                '--channel', 'telegram',
                '--target', TELEGRAM_TARGET,
                '--message', text,
            ], check=True, timeout=90)
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(20)
    raise last_error or RuntimeError('Telegram send failed')


def main() -> None:
    report = build_report()
    if '--send' in sys.argv:
        send_telegram(report)
        print('sent')
    else:
        print(report)


if __name__ == '__main__':
    main()
