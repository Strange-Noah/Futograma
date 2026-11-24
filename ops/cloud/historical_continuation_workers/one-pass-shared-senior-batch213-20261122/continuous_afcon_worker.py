import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name('continuous_worker.py')), init_globals={'prepare_inputs': {
    'documented_countries': {'Algeria', 'Egypt', 'Morocco', 'Senegal'},
    'additional_competitions': {'Africa Cup of Nations': 'OFFICIAL_COMPETITION:CAF_AFCON'},
}})
