"""German Sky match-page adapter. Explicit competition/timezone; shared strict lineup validation."""
import re
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .live_parse import Tree,COMP,fixture,src,ParseError,Pending,_sky_lineups

def parse(raw,record):
 root=Tree(raw).root;h=root.one('sdc-site-match-header')
 if h.attrs.get('data-status')!='4':raise Pending('GERMAN_SKY_FULL_TIME_NOT_CONFIRMED')
 label=h.one('sdc-site-match-header__detail-fixture').txt();comp=next((x for x in sorted(COMP,key=len,reverse=True) if re.search(r'(?<!\w)'+re.escape(x)+r'(?!\w)',label)),None)
 if not comp:raise ParseError('UNSUPPORTED_COMPETITION:GERMAN_HEADER')
 stamp=h.one('sdc-site-match-header__detail-time').txt();m=re.search(r'(\d{1,2}):(\d{2}).*?(\d{2})[.](\d{2})[.](\d{4})',stamp)
 if not m:raise ParseError('GERMAN_SKY_KICKOFF_MISSING')
 hour,minute,day,month,year=map(int,m.groups());kick=datetime(year,month,day,hour,minute,tzinfo=ZoneInfo('Europe/Berlin')).astimezone(timezone.utc).isoformat();names=h.all('sdc-site-match-header__team-name-block-target');scores=h.all('sdc-site-match-header__team-score-block')
 if len(names)!=2 or len(scores)!=2:raise ParseError('GERMAN_SKY_ORDERED_TEAMS_OR_SCORE_MISSING')
 f=fixture(comp,kick,names[0].txt(),names[1].txt(),'-'.join(x.txt() for x in scores),src(record));return _sky_lineups(root,f,record)
