"""The /dev page: a live map of the agents and the messages between them.  (Owner: Dinuka)

One self-contained HTML document. It polls the read-only ``/dev/*`` endpoints; there is no build step.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATLAS developer view</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#1a1f2b;--mut:#6b7280;--line:#e5e7eb;--accent:#1f6feb;
--hot:#e8590c;--ok:#2b8a3e;--bad:#c92a2a;--sense:#0b7285;--plan:#5f3dc4;--control:#e67700;--act:#2b8a3e;--world:#495057}
@media (prefers-color-scheme:dark){:root{--bg:#0f1420;--card:#171d2c;--ink:#e6e9f0;--mut:#98a2b3;--line:#2a3247;
--accent:#6ea8ff;--hot:#ffa94d;--ok:#69db7c;--bad:#ff8787;--sense:#3bc9db;--plan:#b197fc;--control:#ffc078;--act:#8ce99a;--world:#adb5bd}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;background:var(--card);border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{font-size:16px;margin:0}header .sp{flex:1}
select,button{font:inherit;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);cursor:pointer}
.live{display:inline-flex;align-items:center;gap:6px;color:var(--mut)}.dot{width:9px;height:9px;border-radius:50%;background:var(--ok)}
.dot.off{background:var(--mut)}
main{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(320px,1fr);gap:14px;padding:14px}
@media(max-width:980px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
h2{font-size:13px;margin:0 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
svg{width:100%;height:auto;display:block}
.node rect{fill:var(--card);stroke-width:2;rx:8}.node text{fill:var(--ink);font-size:11px;pointer-events:none}
.node .sub{fill:var(--mut);font-size:9px}.node{cursor:pointer}.node.dim{opacity:.35}
.edge{fill:none;stroke:var(--mut);opacity:.5}.edge.hot{stroke:var(--hot);opacity:1;stroke-dasharray:6 4;animation:dash 1s linear infinite}
.edge.execute{stroke:var(--act)}.edge.review{stroke:var(--control)}
@keyframes dash{to{stroke-dashoffset:-20}}
.elabel{font-size:9px;fill:var(--mut)}
.layer{font-size:10px;fill:var(--mut);letter-spacing:.08em}
#stream{max-height:360px;overflow:auto;border-top:1px solid var(--line)}
.row{display:grid;grid-template-columns:62px 150px 1fr;gap:8px;padding:5px 2px;border-bottom:1px solid var(--line);cursor:pointer;align-items:baseline}
.row:hover,.row.sel{background:color-mix(in srgb,var(--accent) 12%,transparent)}
.row .t{color:var(--mut);font-size:12px}.row .w{font-size:12px;color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{display:inline-block;padding:0 6px;border-radius:9px;font-size:11px;background:color-mix(in srgb,var(--accent) 18%,transparent);margin-right:4px}
.tag.trigger{background:color-mix(in srgb,var(--hot) 30%,transparent)}.tag.action_blocked{background:color-mix(in srgb,var(--bad) 30%,transparent)}
.tag.action_authorised{background:color-mix(in srgb,var(--ok) 28%,transparent)}.tag.action_escalated{background:color-mix(in srgb,var(--control) 30%,transparent)}
pre{margin:6px 0;padding:8px;background:var(--bg);border-radius:6px;overflow:auto;max-height:240px;font-size:12px}
.chain a{display:block;padding:2px 0;cursor:pointer;color:var(--accent)}.chain .me{font-weight:600;color:var(--ink)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px}
.card{border:1px solid var(--line);border-left:4px solid var(--world);border-radius:8px;padding:9px 11px}
.card.sense{border-left-color:var(--sense)}.card.plan{border-left-color:var(--plan)}.card.control{border-left-color:var(--control)}.card.act{border-left-color:var(--act)}
.card h3{margin:0 0 3px;font-size:14px}.card p{margin:2px 0 6px}.card small{color:var(--mut);display:block}
.k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.muted{color:var(--mut)}.pill{font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--mut)}
</style></head><body>
<header>
  <h1>ATLAS developer view</h1>
  <span class="live"><span class="dot" id="dot"></span><span id="status">connecting</span></span>
  <span class="sp"></span>
  <label class="muted">Incident <select id="incident"><option value="">all</option></select></label>
  <button id="pause">Pause</button>
  <button id="clear" title="Show agent filter off">Clear filter</button>
  <a href="/dashboard" class="muted">Responder dashboard</a>
</header>
<main>
  <div style="display:grid;gap:14px;min-width:0">
    <section>
      <h2>Who talks to whom <span class="muted" id="mapnote"></span></h2>
      <svg id="map" viewBox="0 0 900 440" role="img" aria-label="Agent message map"></svg>
      <div class="muted" style="font-size:12px">Click an agent to filter the stream. Orange dashed lines carried a message in the last few seconds.
        Green: the Gateway calling an executor. Amber: human review.</div>
    </section>
    <section>
      <h2>Message stream <span class="muted" id="count"></span></h2>
      <div id="stream"></div>
    </section>
  </div>
  <div style="display:grid;gap:14px;align-content:start;min-width:0">
    <section><h2>Selected message</h2><div id="detail" class="muted">Click a message to see what it carried, what caused it and what it led to.</div></section>
    <section><h2>Why this matters</h2>
      <p class="muted" style="margin:0">Agents never call each other. Each one only reads and writes events on the bus and the shared picture of the situation. Every arrow here is a real recorded event, and every event links to the one that caused it.</p></section>
  </div>
  <section style="grid-column:1/-1"><h2>What each agent does</h2><div class="cards" id="cards"></div></section>
</main>
<script>
const NODES={
 sensors:{x:60,y:220,t:"Sensors",s:"river, rain, soil",layer:"world"},
 a1_sentinel:{x:190,y:220,t:"A1 Sentinel",s:"detect",layer:"sense"},
 a2_hazard_intel:{x:350,y:50,t:"A2 Hazard",s:"risk + people",layer:"sense"},
 a5_triage:{x:350,y:130,t:"A5 Triage",s:"who first",layer:"plan"},
 a4_evacuation:{x:350,y:220,t:"A4 Evacuation",s:"safe routes",layer:"plan"},
 a7_shelter:{x:350,y:310,t:"A7 Shelter",s:"where to",layer:"plan"},
 a6_logistics:{x:350,y:390,t:"A6 Logistics",s:"who goes",layer:"plan"},
 a3_incident_commander:{x:530,y:220,t:"A3 Commander",s:"reconcile + plan",layer:"plan"},
 action_gateway:{x:690,y:220,t:"Action Gateway",s:"policy + ledger",layer:"control"},
 human:{x:690,y:80,t:"Human IC",s:"approve / edit",layer:"control"},
 a8_dispatch:{x:830,y:150,t:"A8 Dispatch",s:"missions",layer:"act"},
 a9_comms:{x:830,y:300,t:"A9 Comms",s:"alerts + sitreps",layer:"act"},
 feeds:{x:60,y:320,t:"Feeds",s:"official reports",layer:"world"}};
const W=112,H=44,COLOR={sense:'var(--sense)',plan:'var(--plan)',control:'var(--control)',act:'var(--act)',world:'var(--world)'};
let lastSeq=0,events=[],flow=[],agents=[],paused=false,filterAgent=null,selected=null,prevCounts={},hotUntil={},incident='';
const $=id=>document.getElementById(id), esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function j(url){const r=await fetch(url);if(!r.ok)throw new Error(r.status);return r.json()}
function hhmmss(iso){return (iso||'').slice(11,19)}
function edgePath(a,b,off){const A=NODES[a],B=NODES[b];if(!A||!B)return null;
 const dx=B.x-A.x,dy=B.y-A.y,len=Math.hypot(dx,dy)||1,ux=dx/len,uy=dy/len;
 const sx=A.x+ux*W/2*(Math.abs(ux)>Math.abs(uy)*1.2?1:.2),sy=A.y+uy*H/2*(Math.abs(uy)>=Math.abs(ux)*.8?1:.2);
 const ex=B.x-ux*W/2*(Math.abs(ux)>Math.abs(uy)*1.2?1:.2),ey=B.y-uy*H/2*(Math.abs(uy)>=Math.abs(ux)*.8?1:.2);
 const mx=(sx+ex)/2-uy*off,my=(sy+ey)/2+ux*off;return {d:`M${sx},${sy} Q${mx},${my} ${ex},${ey}`,lx:(sx+2*mx+ex)/4,ly:(sy+2*my+ey)/4}}
function drawMap(){
 const merged={};
 for(const e of flow){const k=e.source+'>'+e.target+'>'+(e.kind==='delivered'?'d':e.kind);
  const m=merged[k]||(merged[k]={source:e.source,target:e.target,kind:e.kind,count:0,labels:{},last:0});
  m.count+=e.count;m.labels[e.label]=(m.labels[e.label]||0)+e.count;m.last=Math.max(m.last,e.last_seq)}
 const now=Date.now();let out='';
 for(const [k,layer] of [['sense',0]]){}
 out+=`<text class="layer" x="40" y="14">SENSE</text><text class="layer" x="330" y="14">PLAN</text><text class="layer" x="670" y="14">CONTROL</text><text class="layer" x="812" y="14">ACT</text>`;
 const keys=Object.keys(merged);
 for(const k of keys){const m=merged[k];const rev=merged[m.target+'>'+m.source+'>'+(m.kind==='delivered'?'d':m.kind)];
  const p=edgePath(m.source,m.target,rev?18:0);if(!p)continue;
  if(prevCounts[k]!==undefined&&m.count>prevCounts[k])hotUntil[k]=now+4000;prevCounts[k]=m.count;
  const hot=(m.last>lastSeq-12&&m.kind==='delivered'&&m.last>0)||(hotUntil[k]||0)>now;
  const dim=filterAgent&&m.source!==filterAgent&&m.target!==filterAgent;
  const tip=Object.entries(m.labels).map(([l,c])=>`${l} x${c}`).join(', ');
  out+=`<g opacity="${dim?.15:1}"><path class="edge ${m.kind==='delivered'?'':m.kind} ${hot?'hot':''}" d="${p.d}" stroke-width="${Math.min(1.2+Math.log2(m.count+1),5)}" marker-end="url(#arr)"><title>${esc(m.source)} to ${esc(m.target)}: ${esc(tip)}</title></path>
  <text class="elabel" x="${p.lx}" y="${p.ly-3}" text-anchor="middle">${m.count}</text></g>`}
 let nodes='';
 for(const [id,n] of Object.entries(NODES)){const c=agents.find(a=>a.name===id)||{};const dim=filterAgent&&filterAgent!==id;
  nodes+=`<g class="node ${dim?'dim':''}" data-id="${id}" transform="translate(${n.x-W/2},${n.y-H/2})"><rect width="${W}" height="${H}" stroke="${COLOR[n.layer]}"/>
   <text x="${W/2}" y="18" text-anchor="middle" font-weight="600">${esc(n.t)}</text><text class="sub" x="${W/2}" y="31" text-anchor="middle">${esc(n.s)}</text>
   <text class="sub" x="${W/2}" y="41" text-anchor="middle">${c.received!==undefined?`in ${c.received} / out ${c.produced}`:''}</text><title>${esc(c.purpose||'')}</title></g>`}
 $('map').innerHTML=`<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--mut)"/></marker></defs>`+out+nodes;
 document.querySelectorAll('.node').forEach(g=>g.onclick=()=>{filterAgent=filterAgent===g.dataset.id?null:g.dataset.id;render()});
 $('mapnote').textContent=filterAgent?`filtered to ${filterAgent}`:''}
function visible(){return events.filter(e=>!filterAgent||e.source===filterAgent||e.handled_by.includes(filterAgent))}
function drawStream(){const v=visible().slice(-300).reverse();$('count').textContent=`(${visible().length} of ${events.length} shown)`;
 $('stream').innerHTML=v.map(e=>`<div class="row ${selected===e.id?'sel':''}" data-id="${e.id}"><span class="t">${hhmmss(e.created_at)}</span>
  <span class="w" title="${esc(e.source)} to ${esc(e.handled_by.join(', '))}">${esc(e.source)} &rarr; ${esc(e.handled_by.join(', ')||'-')}</span>
  <span><span class="tag ${e.type}">${e.type}</span>${esc(e.summary)}</span></div>`).join('')||'<div class="muted" style="padding:10px">No messages yet. Post a sensor reading or replay a scenario.</div>';
 document.querySelectorAll('.row').forEach(r=>r.onclick=()=>select(r.dataset.id))}
async function select(id){selected=id;drawStream();try{const d=await j('/dev/chain/'+id);const e=d.event;
 const link=x=>`<a data-id="${x.id}">${hhmmss(x.created_at)} ${esc(x.source)}: ${esc(x.summary)}</a>`;
 $('detail').innerHTML=`<div><span class="tag ${e.type}">${e.type}</span> <span class="muted">#${e.seq}</span></div><p style="margin:6px 0"><b>${esc(e.summary)}</b></p>
 <div class="k">From</div>${esc(e.source)} <span class="muted">(${esc(e.raw_source)})</span><div class="k">Handled by</div>${esc(e.handled_by.join(', ')||'nobody subscribes to this type')}
 <div class="k">Payload</div><pre>${esc(JSON.stringify(e.payload,null,1))}</pre>
 <div class="k">What caused it</div><div class="chain">${d.ancestors.length?d.ancestors.map(link).join(''):'<span class="muted">nothing recorded (an input from outside the system)</span>'}<div class="me">&#9679; this message</div></div>
 <div class="k">What it led to (${d.descendants.length})</div><div class="chain">${d.descendants.slice(0,40).map(link).join('')||'<span class="muted">nothing yet</span>'}</div>`;
 $('detail').querySelectorAll('.chain a').forEach(a=>a.onclick=()=>select(a.dataset.id))}catch(x){$('detail').textContent='Could not load that message.'}}
function drawCards(){$('cards').innerHTML=agents.map(a=>`<div class="card ${a.layer}"><h3>${esc(a.title)} <span class="pill">${esc(a.layer)}</span></h3><p>${esc(a.purpose)}</p>
 <small><b>Reads:</b> ${esc((a.reads||[]).join(', ')||'-')}</small><small><b>Produces:</b> ${esc((a.writes||[]).join(', ')||'-')}</small>
 <small><b>Tools:</b> ${esc((a.deterministic_tools||[]).join(', ')||'-')} &middot; ${a.uses_llm?'uses an LLM':'no LLM'}</small>
 <small><b>Activity:</b> received ${a.received}, produced ${a.produced}</small>
 <small><b>Last in:</b> ${esc(a.last_received||'-')}</small><small><b>Last out:</b> ${esc(a.last_produced||'-')}</small>
 <small class="muted">${esc(a.owner)}</small></div>`).join('')}
function render(){drawMap();drawStream()}
async function poll(){if(paused)return;const q=incident?`&incident_id=${encodeURIComponent(incident)}`:'';
 try{const t=await j(`/dev/trace?after_seq=${lastSeq}&limit=500${q}`);for(const e of t){events.push(e);lastSeq=Math.max(lastSeq,e.seq)}
  flow=await j('/dev/flow'+(incident?`?incident_id=${encodeURIComponent(incident)}`:''));render();$('dot').className='dot';$('status').textContent=`live, ${events.length} messages`}
 catch(x){$('dot').className='dot off';$('status').textContent='disconnected'}}
async function slow(){try{agents=await j('/dev/agents');drawCards();const inc=await j('/dev/incidents');const s=$('incident');const cur=s.value;
 s.innerHTML='<option value="">all</option>'+inc.map(i=>`<option ${i.incident_id===cur?'selected':''}>${esc(i.incident_id)}</option>`).join('')}catch(x){}}
$('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'Resume':'Pause'};
$('clear').onclick=()=>{filterAgent=null;render()};
$('incident').onchange=e=>{incident=e.target.value;events=[];lastSeq=0;prevCounts={};poll()};
poll();slow();setInterval(poll,1500);setInterval(slow,5000);
</script></body></html>"""
