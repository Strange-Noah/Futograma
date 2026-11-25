"""Run the existing isolated production acceptance over a verified catch-up window."""
import json
import math
import os
from datetime import datetime, timezone

from ops.cloud import release_acceptance
from ops.cloud.production_entry import with_audits
from squad_pipeline.core import ROOT


def main():
    release_acceptance.guard()
    production = release_acceptance.store('main')
    isolated = release_acceptance.store(release_acceptance.REF)
    production_head, production_pointer = production.head()
    candidate_head, candidate_pointer = isolated.head()
    if (production_head != os.environ['EXPECTED_PRODUCTION_HEAD']
            or candidate_head != os.environ['GITHUB_SHA']
            or candidate_pointer != production_pointer
            or production_pointer['current']['database']['sha256'] != os.environ['EXPECTED_DATABASE_SHA']):
        raise ValueError('RECOVERY_REQUIRES_EXACT_FRESH_PRODUCTION_BASE')

    runtime = ROOT / 'ops/cloud/runtime.json'
    settings = json.loads(runtime.read_text())
    published = datetime.fromisoformat(production_pointer['current']['created_at'])
    outage_hours = math.ceil((datetime.now(timezone.utc) - published).total_seconds() / 3600)
    settings['lookback_hours'] += max(0, outage_hours)
    runtime.write_text(json.dumps(settings) + '\n')
    print(json.dumps(dict(baseline_published_at=published.isoformat(),
                          configured_catchup_hours=settings['lookback_hours'])), flush=True)

    release_acceptance.run = with_audits(release_acceptance.run)
    release_acceptance.producer()


if __name__ == '__main__':
    main()
