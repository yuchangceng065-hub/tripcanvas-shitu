"""Seed POIs loaded once and shared by schemas, routing and persistence."""
import json
from pathlib import Path

ROOT = Path(__file__).parent
PLACES = json.loads((ROOT / 'places.json').read_text(encoding='utf-8'))
POIS = {place['id']: place for place in PLACES}
