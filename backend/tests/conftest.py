import os
import sys
import tempfile
from pathlib import Path

test_root = Path(__file__).resolve().parents[2] / 'data' / 'tests'
test_root.mkdir(parents=True, exist_ok=True)
TEST_DIR = tempfile.TemporaryDirectory(prefix='run-', dir=test_root, ignore_cleanup_errors=True)
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(TEST_DIR.name) / 'test.db')
os.environ['COLLECTOR_MODE'] = 'mock'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from app.db import Base, engine
from app.main import app
import app.images as image_module
image_module.IMAGES = Path(TEST_DIR.name)

@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c
