from download_fixtures import main as download_fixtures
from export_frontend_data import main as export_frontend_data
from import_fpl_context import import_upcoming_window
from predict_upcoming_sklearn_fast import main as predict_upcoming
from track_closing_lines import main as track_closing_lines


def main():
    download_fixtures()
    import_upcoming_window(days=21, refresh_cache=True)
    predict_upcoming()
    track_closing_lines()
    export_frontend_data()


if __name__ == "__main__":
    main()
