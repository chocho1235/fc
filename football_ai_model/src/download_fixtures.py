import time
from pathlib import Path
from urllib.request import urlopen


URL = "https://www.football-data.co.uk/fixtures.csv"
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RAW_DIR / "fixtures.csv"
    print(f"Downloading {URL}")
    for attempt in range(1, 4):
        try:
            with urlopen(URL, timeout=60) as response:
                content = response.read()
            break
        except OSError as exc:
            if attempt == 3:
                raise
            print(f"Download failed ({exc}), retrying in {attempt * 20}s")
            time.sleep(attempt * 20)
    first_line = content.splitlines()[0].lstrip(b"\xef\xbb\xbf") if content.splitlines() else b""
    if b"," not in first_line:
        raise ValueError(f"Unexpected fixtures CSV response: {first_line[:120]!r}")
    output_path.write_bytes(content)
    print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
