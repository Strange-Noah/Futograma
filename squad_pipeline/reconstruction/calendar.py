"""R5 windows from the accepted typed source parser; no duplicate extraction."""
from datetime import timedelta
from .engine import Evidence, Window, day
from ..source_interface import SourceRequest, RawSourceArtifact, sha
from ..source_parsing import parse


def fifa_windows(raw, url, observed_at, start, cutoff, time_precision='TIMESTAMP', observation=None):
    if url != 'https://inside.fifa.com/tournament-organisation/international-match-calendars':
        raise ValueError('OFFICIAL_CALENDAR_ORIGIN_REQUIRED')
    request=SourceRequest('FIFA','calendar',url,'international-match-calendar')
    metadata={'response_timestamp':observed_at} if time_precision=='TIMESTAMP' else {'retained_file_mtime':observed_at}
    artifact=RawSourceArtifact(request,sha(raw),'caller-supplied-archive',metadata,'ARCHIVED_RESPONSE')
    # Transitional pure-call adapter for existing tests; operational callers
    # supply the observation obtained through their explicit SourceService.
    observation=observation if observation is not None else parse(artifact,raw)
    if observation.outcome!='PARSED':
        raise ValueError('OFFICIAL_SENIOR_MENS_SCOPE_REQUIRED:'+observation.outcome)
    if observation.provider!='FIFA' or observation.category!='calendar' or any(row.get('source_sha256')!=artifact.content_sha256 or row.get('source_url')!=url for row in observation.native.get('rows',[])):
        raise ValueError('CALENDAR_OBSERVATION_PROVENANCE_MISMATCH')
    result=[]
    for row in observation.native['rows']:
        fields=row['fields'];left=(fields.get('DateStart') or '')[:10];right=(fields.get('DateEnd') or '')[:10]
        if not left or not right:continue
        end=(day(right)+timedelta(days=1)).isoformat()
        if left>cutoff or end<=start:continue
        evidence=Evidence('FIFA',url,artifact.content_sha256,row['locator'],observed_at,'FIFA',time_precision)
        result.append(Window('OFFICIAL',left,end,fields['TypeName'],None,'SENIOR',
            'ANNOUNCED_CALENDAR;NOT_PROOF_OF_INDIVIDUAL_SELECTION',(evidence,)))
    if not result:raise ValueError('OFFICIAL_CALENDAR_SCOPE_OR_ROWS_UNAVAILABLE')
    return result
