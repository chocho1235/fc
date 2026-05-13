import os
import subprocess


CONFIGS = [
    ("baseline_no_context", {"ENABLE_CONTEXT_FEATURES": "0", "ENABLE_WEATHER_FEATURES": "0"}),
    ("weather_only", {"ENABLE_CONTEXT_FEATURES": "0", "ENABLE_WEATHER_FEATURES": "1"}),
    ("context_no_weather", {"ENABLE_CONTEXT_FEATURES": "1", "ENABLE_WEATHER_FEATURES": "0"}),
    ("context_and_weather", {"ENABLE_CONTEXT_FEATURES": "1", "ENABLE_WEATHER_FEATURES": "1"}),
]


def run_config(name, env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)
    env.setdefault("TRAINING_WINDOW_SEASONS", "5")
    env.setdefault("BET_THRESHOLD", "0.08")
    result = subprocess.run(
        ["python3", "src/train_1x2_model.py"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    lines = [line for line in result.stdout.splitlines() if "ROI:" in line or "Accuracy:" in line or "Value bets" in line]
    return name, lines


def main():
    for name, env in CONFIGS:
        print(f"\n== {name} ==")
        _name, lines = run_config(name, env)
        for line in lines:
            print(line)


if __name__ == "__main__":
    main()

