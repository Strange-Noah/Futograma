import sys, os, json, socket, builtins, unittest, hashlib, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests/cloud')]
os.chdir(ROOT)
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
os.environ['TMPDIR']=str(Path(__file__).parent/'temporary')
Path(os.environ['TMPDIR']).mkdir(exist_ok=True)
counts={'network_attempts':0,'llm_import_attempts':0}
def no_network(*args,**kwargs):
 counts['network_attempts']+=1
 raise RuntimeError('offline runtime independence guard')
socket.socket.connect=no_network
socket.socket.connect_ex=no_network
socket.create_connection=no_network
socket.getaddrinfo=no_network
original_import=builtins.__import__
def guarded_import(name,*args,**kwargs):
 if name.split('.')[0] in {'openai','anthropic','langchain','mcp'}:
  counts['llm_import_attempts']+=1
  raise RuntimeError('LLM/Agent import forbidden by test')
 return original_import(name,*args,**kwargs)
builtins.__import__=guarded_import
modules=['test_source_interface','test_coldstart_persistence','test_reconstruction','test_quality_pipeline','test_cloud','test_roster_initialization']
suite=unittest.TestLoader().loadTestsFromNames(modules)
class Result(unittest.TextTestResult):
 def startTest(self,test):
  super().startTest(test); executed.append(test.id())
executed=[]
started=time.time()
with (Path(__file__).parent/'test-log-private.txt').open('w') as log:
 result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(suite)
from fixtures import database
from squad_pipeline.cloud.storage import LocalStore,StaleWriter,UploadFailed
from squad_pipeline.cloud.quality import pack,sha
from squad_pipeline.cloud.export import export
phase=Path(__file__).parent/'isolated-local-store-verified'
phase.mkdir(exist_ok=False)
seed=phase/'synthetic.sqlite'; database(seed)
zipped=phase/'database.sqlite.gz';pack(seed,zipped)
store=LocalStore(phase/'store')
first,p1=store.publish(seed,zipped,None)
second,p2=store.publish(seed,zipped,first)
store.restore(phase/'restored.sqlite')
assert sha(phase/'restored.sqlite')==sha(seed)
manifest=export(phase/'restored.sqlite',phase/'site',second,p2,ROOT/'web')
assert (phase/'site/data/manifest.json').is_file()
try: store.publish(seed,zipped,first)
except StaleWriter: pass
else: raise AssertionError('stale pointer was accepted')
assert store.head()==(second,p2)
try: store.publish(seed,zipped,second,fail_upload=True)
except UploadFailed: pass
else: raise AssertionError('failed upload changed pointer')
assert store.head()==(second,p2)
asset=phase/'store/versions'/p2['current']['version']/'database.sqlite.gz'
asset.write_bytes(b'controlled corruption in synthetic store only')
try:store.restore(phase/'corrupt-restore.sqlite')
except ValueError:pass
else:raise AssertionError('corrupt asset was accepted')
store.restore(phase/'previous.sqlite',previous=True)
assert sha(phase/'previous.sqlite')==sha(seed)
assert store.head()==(second,p2)
local_store_checks=['snapshot round-trip SHA-256','ordinary website export','stale-writer rejection','upload-failure pointer preservation','corrupt-current rejection','previous-snapshot recovery without pointer mutation']
paths=subprocess.check_output(['git','ls-files','-z'],text=True).split('\0')
h=hashlib.sha256()
for name in sorted(x for x in paths if x):
 body=(ROOT/name).read_bytes(); h.update(name.encode()+b'\0'+hashlib.sha256(body).digest())
report={'scope':'Representative component integration; not complete historical orchestration','head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'tracked_working_content_sha256':h.hexdigest(),'dirty_tracked_files':subprocess.check_output(['git','diff','--name-only'],text=True).splitlines(),'modules':modules,'local_store_checks_passed':local_store_checks,'tests_passed':result.testsRun-len(result.skipped)-len(result.failures)-len(result.errors),'skip_reasons':sorted(set(reason for _,reason in result.skipped)),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'successful':result.wasSuccessful(),'network_guard':'Python socket connect/connect_ex/create_connection/DNS denied','llm_guard':'Imports openai/anthropic/langchain/mcp denied','guard_counts':counts,'actual_external_acquisitions':0,'production_writes':0,'seconds':round(time.time()-started,3),'executed_tests':executed,'limits':['In-process Python guards; not OS network isolation','Source acquisition uses injected synthetic transport and retained local fixtures','No claim of complete unattended historical job or production end-to-end test']}
(Path(__file__).parent/'AGENT_ABSENT_EXECUTION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='executed_tests'},indent=2))
if not result.wasSuccessful():
 print('\n'.join(str(x) for x in result.failures+result.errors))
sys.exit(0 if result.wasSuccessful() else 1)
