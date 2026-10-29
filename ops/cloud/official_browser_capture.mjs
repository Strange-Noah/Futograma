// Normal rendering of an allowed HTTP-200 official roster shell; no challenge bypass or direct API calls.
import {spawn} from 'node:child_process';
import {writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const [url,out]=process.argv.slice(2);
if(process.env.GITHUB_ACTIONS!=='true'||process.platform!=='linux'||!/^https:\/\/www\.arsenal\.com\/fixtures\/men\/players(?:\/[^/?#]+)?$/.test(url))throw Error('Explicit approved hosted official shell URL required');
const chrome=spawn('google-chrome',['--headless','--no-sandbox','--disable-dev-shm-usage','--remote-debugging-port=9223','--no-first-run','about:blank'],{stdio:'ignore'});
let ws;
try{
 let target;for(let i=0;i<40;i++){try{target=await(await fetch('http://127.0.0.1:9223/json/new?'+encodeURIComponent(url),{method:'PUT'})).json();break;}catch{await new Promise(r=>setTimeout(r,250));}}
 if(!target)throw Error('HOSTED_BROWSER_UNAVAILABLE');ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});let seq=0;const pending=new Map();ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}};const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});await cdp('Runtime.enable');
 const evaluate=async expression=>{const r=await cdp('Runtime.evaluate',{expression,returnByValue:true});if(r.exceptionDetails)throw Error('OFFICIAL_RENDER_EXCEPTION');return r.result.value;};
 let blocked=false;const profile=new URL(url).pathname.replace(/\/$/,'')!=='/fixtures/men/players';
 for(let i=0;i<80;i++){const text=await evaluate('document.body?.innerText||""');if(/access denied|verify you are human|just a moment|captcha/i.test(text)){blocked=true;break;}if(profile&&await evaluate('Boolean(document.querySelector("h1")?.innerText.trim()) && /Date of birth|Born|Height|Position/i.test(document.body.innerText)'))break;if(await evaluate('document.querySelectorAll("a[href*=\"/fixtures/men/players/\"]").length>10'))break;await new Promise(r=>setTimeout(r,500));}
 const body=await evaluate('document.documentElement.outerHTML');const final=await evaluate('location.href');const links=await evaluate('Array.from(document.querySelectorAll("a[href]"),a=>({href:a.href,text:a.innerText})).filter(a=>a.href.includes("/fixtures/men/players/"))');
 await writeFile(out+'/official-rendered.html',body,{flag:'wx'});const receipt={url,final_url:final,status:blocked?'SOURCE_POLICY_RESTRICTED':links.length||profile?'SUCCESS':'RENDERED_ROSTER_UNAVAILABLE',transport_mode:'NORMAL_BROWSER_RENDER_OF_HTTP_200_PUBLIC_PAGE',response_timestamp:new Date().toISOString(),sha256:createHash('sha256').update(body).digest('hex'),path:out+'/official-rendered.html',links,challenge_bypassed:false};await writeFile(out+'/official-rendered.json',JSON.stringify(receipt,null,2),{flag:'wx'});console.log(JSON.stringify(receipt));
}finally{if(ws)ws.close();chrome.kill();}
