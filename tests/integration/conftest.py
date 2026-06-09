import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def tenants():
    path = os.path.join(ROOT, "harness", "tenants.json")
    if not os.path.exists(path):
        pytest.skip("run `python harness/provision.py` first")
    return json.load(open(path))
