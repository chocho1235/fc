import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from leagues import DEFAULT_LEAGUE_CODES

BASE_URL = "https://www.football-data.co.uk/mmz4281"
FIRST_SEASON_START = 2017
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


def season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def current_season_start(today: date | None = None) -> int:
    today = today or date.today()
    # European seasons start in August; July still belongs to the old season.
    return today.year if today.month >= 8 else today.year - 1


def all_seasons() -> list[str]:
    return [season_code(year) for year in range(FIRST_SEASON_START, current_season_start() + 1)]


def fetch(url: str) -> bytes:
    for attempt in range(1, 4):
        try:
            with urlopen(url, timeout=60) as response:
                return response.read()
        except HTTPError:
            raise
        except OSError as exc:
            if attempt == 3:
                raise
            print(f"  failed ({exc}), retrying in {attempt * 10}s")
            time.sleep(attempt * 10)
    raise RuntimeError("unreachable")


def download_csv(season: str, league_code: str) -> bool:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{BASE_URL}/{season}/{league_code}.csv"
    output_path = RAW_DIR / f"{season}_{league_code}.csv"

    print(f"Downloading {url}")
    try:
        content = fetch(url)
    except (HTTPError, OSError) as exc:
        print(f"  skipped: {exc}")
        return False

    first_line = content.splitlines()[0].lstrip(b"\xef\xbb\xbf") if content.splitlines() else b""
    if not first_line.startswith(b"Div,Date"):
        print(f"  skipped: unexpected response {first_line[:80]!r}")
        return False

    output_path.write_bytes(content)
    print(f"  saved {output_path.name}")
    return True


def main(current_only: bool = False) -> None:
    """Download results CSVs.

    current_only=True refreshes just the season in progress (used by the
    6-hourly refresh so form, Elo and the bet tracker see the latest results).
    Old seasons never change, so they are only fetched if missing or on a full
    run.
    """
    seasons = all_seasons()
    current = seasons[-1]
    failures = 0
    for season in seasons:
        for league_code in DEFAULT_LEAGUE_CODES:
            path = RAW_DIR / f"{season}_{league_code}.csv"
            if current_only and season != current and path.exists():
                continue
            if not download_csv(season, league_code) and not path.exists() and season != current:
                failures += 1
    if failures:
        raise SystemExit(f"{failures} past-season file(s) could not be downloaded")


if __name__ == "__main__":
    main(current_only="--current" in sys.argv)
