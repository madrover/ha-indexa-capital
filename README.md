# Indexa Capital for Home Assistant

`indexa_capital` is a HACS-ready custom integration that tracks Indexa Capital portfolio performance inside Home Assistant.

API documentation: https://indexacapital.com/en/api-rest-v1

## Features

- Authenticates with an Indexa API token
- Discovers all accounts tied to the token automatically
- Creates per-account performance sensors in money and percentage
- Creates aggregate portfolio performance sensors
- Supports a manual Recorder statistics backfill for historical daily data
- Runs a daily refresh window from `08:00` to `11:00` local time with 15 minute retries until fresh data appears
- Sends a Home Assistant notification once fresh data is detected for the day

## Installation

1. In HACS, open the menu, choose `Custom repositories`, and add `https://github.com/madrover/ha-indexa-capital` as an `Integration`.
2. Restart Home Assistant.
3. Add the `Indexa Capital` integration from the UI.
4. Enter your API token.
5. Optionally configure a notify service and refresh schedule in integration options.

You can also install manually by copying `custom_components/indexa_capital` into your Home Assistant `custom_components` directory.

## Data model

The integration uses the latest performance-history date returned by Indexa to decide whether new daily data is available. If no new data arrives before the configured end time, the previous successful snapshot remains available and graphable.

Historical data before installation is not imported automatically. To backfill previous Indexa daily data into Home Assistant Recorder statistics, call the `indexa_capital.backfill_statistics` service after setup.

Example service call:

```yaml
service: indexa_capital.backfill_statistics
data:
  start_date: "2026-04-01"
  end_date: "2026-04-21"
```

This backfills long-term statistics for the existing Indexa sensor entities so charts can include earlier daily points.

## API reference

This integration is built against the public Indexa Capital API documentation:

- https://indexacapital.com/en/api-rest-v1

Indexa also documents API access and token generation here:

- https://support.indexacapital.com/es/esp/introduccion-api

## Development

Suggested local checks:

```bash
ruff check .
pytest
```

To test a real Indexa token without running Home Assistant, use the standalone smoke test:

```bash
.venv/bin/python scripts/smoke_test.py --token YOUR_INDEXA_TOKEN
```

That command validates the token and prints the normalized per-account portfolio snapshot used by the integration.
