"""Use the same batch acquisition with the existing historical club stages."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name('continuous_worker.py')),
               init_globals={'club': True}, run_name='__main__')
