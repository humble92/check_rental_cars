from pathlib import Path


def pytest_configure(config):
    # Keep temporary test databases inside the workspace on restricted hosts.
    (Path(__file__).resolve().parents[1] / "tmp").mkdir(exist_ok=True)
