from pathlib import Path
from urllib.request import urlopen


BASE_URL = "https://www.football-data.co.uk/mmz4281"
LEAGUE_CODE = "E0"  # English Premier League
SEASONS = ["1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526"]
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


def download_csv(season: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{BASE_URL}/{season}/{LEAGUE_CODE}.csv"
    output_path = RAW_DIR / f"{season}_{LEAGUE_CODE}.csv"

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
        download_csv(season)


if __name__ == "__main__":
    main()
