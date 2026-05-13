from pathlib import Path
from urllib.request import urlopen

from leagues import DEFAULT_LEAGUE_CODES

BASE_URL = "https://www.football-data.co.uk/mmz4281"
SEASONS = ["1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526"]
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


def download_csv(season: str, league_code: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{BASE_URL}/{season}/{league_code}.csv"
    output_path = RAW_DIR / f"{season}_{league_code}.csv"

    print(f"Downloading {url}")
    with urlopen(url, timeout=30) as response:
        content = response.read()

    first_line = content.splitlines()[0].lstrip(b"\xef\xbb\xbf") if content.splitlines() else b""
    if not first_line.startswith(b"Div,Date"):
        raise ValueError(f"Unexpected CSV response for season {season}")

    output_path.write_bytes(content)
    print(f"Saved {output_path}")


def main() -> None:
    for season in SEASONS:
        for league_code in DEFAULT_LEAGUE_CODES:
            download_csv(season, league_code)


if __name__ == "__main__":
    main()
