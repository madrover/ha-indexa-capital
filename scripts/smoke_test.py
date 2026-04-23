"""Standalone smoke test for the Indexa Capital API client."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from aiohttp import ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.indexa_capital.api import IndexaApiClient, IndexaApiError, IndexaAuthError


async def _async_main(token: str) -> int:
    """Validate a token and print the normalized portfolio snapshot."""
    async with ClientSession() as session:
        client = IndexaApiClient(session=session, token=token)

        try:
            await client.async_validate_token()
            snapshot = await client.async_fetch_portfolio_snapshot()
        except IndexaAuthError:
            print("Authentication failed: invalid Indexa API token.", file=sys.stderr)
            return 1
        except IndexaApiError as err:
            print(f"Indexa API request failed: {err}", file=sys.stderr)
            return 1

    print(json.dumps(asdict(snapshot), indent=2, default=str))
    return 0


def main() -> int:
    """Parse arguments and run the async smoke test."""
    parser = argparse.ArgumentParser(
        description="Validate an Indexa API token and print the normalized snapshot."
    )
    parser.add_argument(
        "--token",
        required=True,
        help="Indexa API token to test.",
    )
    args = parser.parse_args()
    return asyncio.run(_async_main(args.token))


if __name__ == "__main__":
    raise SystemExit(main())
