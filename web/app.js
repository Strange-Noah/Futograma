const get = async path => {const r=await fetch(`data/${path}`);if(!r.ok)throw Error(`读取失败：${r.status}`);return r.json();};
try {
 const [manifest,clubs,players,cohorts,rolling]=await Promise.all(['manifest.json','clubs.json','players.json','cohorts.json','rolling.json'].map(get));
 const names=new Map(players.map(p=>[p.player_id,p.name])); const select=document.querySelector('#club');
 for(const club of clubs){const option=document.createElement('option');option.value=club.club_id;option.textContent=club.name;select.append(option);}
 document.querySelector('#status').textContent=`数据导出：${manifest.exported_at}；按马德里日期汇总。`;
 document.querySelector('#version').textContent=`数据版本：${manifest.version.data_version}`;
 const render=()=>{const id=select.value;const cohort=cohorts.find(c=>c.club_id===id);document.querySelector('#cohort').textContent=cohort?.population_complete?'常规成员边界已核实。':'常规成员边界仍有待核实项；以下仅展示已记录的俱乐部比赛分钟。';const body=document.querySelector('#rows');body.replaceChildren();for(const player of rolling.filter(p=>p.club_id===id)){const tr=document.createElement('tr');const values=[names.get(player.player_id)||player.player_id,...player.windows.map(w=>`${w.club_minutes}${w.unknown_rows?'（有未知）':''}`)];for(const value of values){const td=document.createElement('td');td.textContent=String(value);tr.append(td);}body.append(tr);}};
 select.addEventListener('change',render);render();
} catch(error){document.querySelector('#status').textContent=`数据暂不可用：${error.message}`;}
