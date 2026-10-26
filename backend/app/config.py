import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
DATA = ROOT / 'data'
IMAGES = DATA / 'images'
IMAGES.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///data/collector.db')
if DATABASE_URL.startswith('sqlite:///') and not Path(DATABASE_URL[10:]).is_absolute():
    DATABASE_URL = 'sqlite:///' + str(ROOT / DATABASE_URL[10:])
MODE = os.getenv('COLLECTOR_MODE', 'mock')
