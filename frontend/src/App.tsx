import { useCallback, useEffect, useMemo, useState } from 'react';
import { ReactFlow, Background, Controls, type Edge, type Node } from '@xyflow/react';
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Bell, Box, Check, ChevronDown,
  CircleHelp, Clock3, FileSearch, Fingerprint, Gauge, GitBranch, Layers3, LockKeyhole,
  Pause, Play, Radar, RefreshCw, Search, Shield, ShieldAlert, ShieldCheck, Sparkles,
  Users, X,
} from 'lucide-react';
import IncidentBriefing from './IncidentBriefing';

type MarketEvent = { id: number; ts: number; event_type: string; trader_id: number; side: number;
  price: number; qty: number; best_bid: number; best_ask: number; counterparty_id: number };
type Agent = { id: number; name: string; role: string; status: string; last_activity: number };
type CaseRow = { id: string; title: string; status: string; created_ts: number; agents: number[];
  graph: { nodes: Array<{id:string; label:string; role:string}>; edges: Array<{source:string;target:string;kind:string;weight:number;reason:string;matches:Array<{event_ids:number[];lag_ms:number;event_type:string}>}> };
  event_ids: number[]; report: null | {source:string; summary:string; findings:Array<{text:string;evidence_ids:number[]}>; limitations:string[]; llm_error?:string} };
type Proposal = { id:string; case_id:string; title:string; action:string; agents:number[]; duration_ns:number; status:string };
export type Snapshot = { run_id:string|null; seed:number; status:string; speed:number; sim_time_s:number; error:string|null;
  model_status:string; market:{mid:number;bid:number;ask:number;spread:number;volume:number;event_count:number;rejected_count:number};
  agents:Agent[]; graph:{nodes:Array<{id:string;label:string;role:string}>;edges:Array<{source:string;target:string;kind:string;weight:number;reason:string}>};
  events:MarketEvent[]; alerts:Array<{id:number;ts:number;trader_id:number;kind:string;source:string;score:number;evidence:Array<Record<string,unknown>>}>;
  cases:CaseRow[]; proposals:Proposal[]; restrictions:Array<{agent_id:number;expires_ts:number}>;
  audit:Array<{id:number;ts:number;action:string;case_id?:string|null;details:Record<string,unknown>}> };

const empty: Snapshot = {run_id:null,seed:7,status:'ready',speed:8,sim_time_s:0,error:null,model_status:'Loading surveillance engines…',
  market:{mid:10000,bid:0,ask:0,spread:0,volume:0,event_count:0,rejected_count:0},agents:[],graph:{nodes:[],edges:[]},events:[],alerts:[],cases:[],proposals:[],restrictions:[],audit:[]};
const time = (seconds:number) => `${Math.floor(seconds/60).toString().padStart(2,'0')}:${Math.floor(seconds%60).toString().padStart(2,'0')}`;
const fmt = (n:number) => new Intl.NumberFormat('en-US').format(n);

export default function App() {
  const [data, setData] = useState<Snapshot>(empty);
  const [seed, setSeed] = useState(7);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState<'cases'|'audit'>('cases');
  const [briefingOpen, setBriefingOpen] = useState(false);
  const activeCase = data.cases[0];

  const refresh = useCallback(async () => {
    try { const response = await fetch('/api/state'); if (response.ok) setData(await response.json()); }
    catch { /* the API may still be starting */ }
  }, []);
  useEffect(() => { void refresh(); const id = window.setInterval(refresh, 1000); return () => clearInterval(id); }, [refresh]);
  useEffect(() => {
    let socket: WebSocket | null = null; let timer = 0; let alive = true;
    const connect = () => {
      if (!alive) return;
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
      socket.onmessage = event => { try { setData(JSON.parse(event.data)); } catch { /* ignore malformed frame */ } };
      socket.onclose = () => { if (alive) timer = window.setTimeout(connect, 1500); };
    };
    connect(); return () => { alive = false; clearTimeout(timer); socket?.close(); };
  }, []);

  const call = async (path:string, body?:unknown) => {
    setBusy(true); setNotice('');
    try {
      const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json'},body:body ? JSON.stringify(body) : undefined});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Request failed');
      if (result.run_id !== undefined) setData(result);
      await refresh();
      return result;
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Request failed'); }
    finally { setBusy(false); }
  };

  const start = async () => { await call('/api/run/start',{seed}); };
  const changeSpeed = async (value:number) => { await call('/api/run/speed',{speed:value}); };
  const investigate = async (id:string) => { const result = await call(`/api/cases/${id}/investigate`); if(result?.report) setNotice(`Investigation ready · ${result.report.source}`); };
  const approve = async (id:string) => { await call(`/api/proposals/${id}/approve`,{reviewer:'Demo analyst'}); };
  const reject = async (id:string) => { await call(`/api/proposals/${id}/reject`,{reviewer:'Demo analyst'}); };

  const nodes:Node[] = useMemo(() => data.graph.nodes.map((node,index) => {
    const angle = (index / Math.max(1,data.graph.nodes.length)) * Math.PI * 2 - Math.PI / 2;
    const agent = data.agents.find(item => item.id === Number(node.id));
    const related = data.graph.edges.some(edge => edge.source === node.id || edge.target === node.id);
    return {id:node.id,position:{x:300+Math.cos(angle)*215,y:190+Math.sin(angle)*135},data:{label:<div className={`graph-node ${related?'is-linked':''} ${agent?.status==='restricted'?'is-restricted':''}`}>
      <span className="node-orbit"><Fingerprint size={15}/></span><b>{node.label}</b><small>{node.role}</small>{agent?.status==='restricted'&&<i>RESTRICTED</i>}</div>},type:'default'};
  }),[data.graph.nodes,data.graph.edges,data.agents]);
  const edges:Edge[] = useMemo(() => data.graph.edges.map((edge,index)=>({id:`${edge.source}-${edge.target}-${index}`,source:edge.source,target:edge.target,
    label:edge.kind==='counterparty'?`${edge.weight} vol`:`${edge.weight} sync`,animated:edge.kind!=='counterparty',
    labelStyle:{fill:edge.kind==='counterparty'?'#83b9e9':'#f6ad55',fontSize:10},labelBgStyle:{fill:'#111a24'},
    style:{stroke:edge.kind==='counterparty'?'#5289b8':'#f59e0b',strokeWidth:Math.min(4,1+Math.log10(Math.max(1,edge.weight))*1.3),strokeDasharray:edge.kind==='counterparty'?'4 3':undefined}})),[data.graph.edges]);
  const chart = useMemo(() => {
    const values=data.events.map(event=>event.best_bid&&event.best_ask?(event.best_bid+event.best_ask)/2:null).filter((v):v is number=>v!==null);
    if(values.length<2) return '';
    const lo=Math.min(...values), hi=Math.max(...values), span=Math.max(1,hi-lo);
    return values.map((v,i)=>`${(i/(values.length-1))*100},${88-((v-lo)/span)*70}`).join(' ');
  },[data.events]);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Radar size={22}/></div><div><strong>ARGUS</strong><small>MARKET OVERSIGHT</small></div></div>
      <div className="workspace-select"><div className="workspace-dot"/><div><small>WORKSPACE</small><b>Simulation lab</b></div><ChevronDown size={15}/></div>
      <div className="nav-label">SURVEILLANCE</div>
      <nav><a className="nav-item selected"><Layers3 size={17}/>Overview<span className="nav-live"/></a>
        <a className="nav-item"><Activity size={17}/>Market activity</a><a className="nav-item"><GitBranch size={17}/>Agent network{data.graph.edges.length>0&&<em>{data.graph.edges.length}</em>}</a>
        <a className="nav-item"><FileSearch size={17}/>Investigations{data.cases.length>0&&<em>{data.cases.length}</em>}</a></nav>
      <div className="nav-label governance-label">GOVERNANCE</div>
      <nav><a className="nav-item"><Shield size={17}/>Control center</a><a className="nav-item"><Clock3 size={17}/>Audit trail</a></nav>
      <div className="sidebar-bottom"><div className="live-health"><span className="pulse-dot"/><div><b>All systems operational</b><small>Local synthetic environment</small></div></div>
        <div className="user-chip"><div className="avatar">DA</div><div><b>Demo analyst</b><small>Surveillance operator</small></div><CircleHelp size={16}/></div>
      </div>
    </aside>

    <main className="main-area">
      <header className="topbar"><div className="breadcrumb">Surveillance <span>/</span> <b>Overview</b></div><div className="top-actions"><div className="environment-tag"><span/>SYNTHETIC MARKET</div><button className="button briefing-launch" onClick={()=>setBriefingOpen(true)}><Box size={14}/>3D briefing</button><button className="icon-button" title="Notifications"><Bell size={17}/><i/></button><div className="avatar small-avatar">DA</div></div></header>
      <div className="page-content">
        <div className="page-heading"><div><div className="eyebrow"><span className="pulse-dot"/>LIVE SURVEILLANCE</div><h1>Market overview</h1><p>Observe agent behavior, investigate patterns, and govern with evidence.</p></div>
          <div className="run-controls"><label className="seed-control">SEED<input type="number" value={seed} onChange={e=>setSeed(Number(e.target.value))}/></label>
            {data.status==='running'?<button className="button secondary" onClick={()=>void call('/api/run/pause')}><Pause size={15}/>Pause</button>:data.status==='paused'?<button className="button secondary" onClick={()=>void call('/api/run/resume')}><Play size={15}/>Resume</button>:<button className="button primary" disabled={busy} onClick={()=>void start()}><Play size={15}/>Start demo</button>}
            <button className="icon-button reset-button" title="Reset run" onClick={()=>void call('/api/run/reset')}><RefreshCw size={16}/></button>
          </div></div>

        {(notice||data.error)&&<div className={`notice ${data.error?'error':''}`}><AlertTriangle size={15}/>{data.error||notice}<button onClick={()=>{setNotice('');}}><X size={14}/></button></div>}
        <div className="status-strip"><div><span className={`status-pill ${data.status}`}><i/>{data.status.toUpperCase()}</span><span className="strip-divider"/><span>RUN <b>{data.run_id||'NOT STARTED'}</b></span><span className="strip-divider"/><span>SIM TIME <b>{time(data.sim_time_s)}</b></span></div>
          <div className="speed-control"><Gauge size={14}/><span>SPEED</span><select value={data.speed} onChange={e=>void changeSpeed(Number(e.target.value))}><option value={1}>1×</option><option value={4}>4×</option><option value={8}>8×</option><option value={16}>16×</option><option value={32}>32×</option></select></div>
        </div>

        <section className="metric-grid">
          <Metric icon={<Activity size={17}/>} label="MID PRICE" value={data.market.mid.toFixed(2)} sub={`Spread ${data.market.spread||'—'} ticks`} tone="blue" trend={data.market.mid>=10000}/>
          <Metric icon={<Users size={17}/>} label="MONITORED AGENTS" value={`${data.agents.length||10}`} sub={`${data.agents.filter(a=>a.status==='restricted').length} under restriction`} tone="violet"/>
          <Metric icon={<Layers3 size={17}/>} label="MARKET EVENTS" value={fmt(data.market.event_count)} sub={`${fmt(data.market.volume)} shares executed`} tone="teal"/>
          <Metric icon={<ShieldAlert size={17}/>} label="OPEN INVESTIGATIONS" value={`${data.cases.length}`} sub={`${data.graph.edges.length} linked agent relationships`} tone="amber"/>
        </section>

        <div className="dashboard-grid">
          <section className="panel market-panel"><PanelTitle icon={<Activity size={16}/>} title="Market activity" subtitle="ARGUS · Synthetic instrument" trailing={<span className="live-label"><i/>LIVE</span>}/>
            <div className="chart-head"><div><b>{data.market.mid.toFixed(2)}</b><span className="chart-change"><ArrowUpRight size={13}/> +0.08%</span></div><small>LAST WINDOW · 10 SEC</small></div>
            <div className="chart-wrap"><div className="chart-gridlines"><span>10,004</span><span>10,002</span><span>10,000</span><span>9,998</span></div>
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="price-chart"><defs><linearGradient id="area" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#3e93e9" stopOpacity=".22"/><stop offset="1" stopColor="#3e93e9" stopOpacity="0"/></linearGradient></defs>
                {chart&&<><polygon points={`0,100 ${chart} 100,100`} fill="url(#area)"/><polyline points={chart} fill="none" stroke="#53a7ff" strokeWidth=".8" vectorEffect="non-scaling-stroke"/></>}</svg>
            </div><div className="chart-footer"><span>09:30:00</span><span>09:30:10</span><span>09:30:20</span><span>09:30:30</span><span>NOW</span></div>
            <div className="market-stats"><div><small>BEST BID</small><b>{data.market.bid||'—'}</b></div><div><small>BEST ASK</small><b>{data.market.ask||'—'}</b></div><div><small>REJECTED ORDERS</small><b className={data.market.rejected_count?'warning-text':''}>{data.market.rejected_count}</b></div><div><small>INSTRUMENT</small><b>ARGUS</b></div></div>
          </section>

          <section className="panel agents-panel"><PanelTitle icon={<Users size={16}/>} title="Agent activity" subtitle="10 participants · 1 instrument" trailing={<button className="text-button">View all <ArrowUpRight size={13}/></button>}/>
            <div className="agent-summary"><div className="agent-count"><b>10</b><small>total agents</small></div><div className="agent-bars"><div><span>Market makers</span><b>2</b><i><em style={{width:'20%'}}/></i></div><div><span>Momentum</span><b>2</b><i><em style={{width:'20%'}}/></i></div><div><span>Flow agents</span><b>6</b><i className="flow"><em style={{width:'60%'}}/></i></div></div></div>
            <div className="agent-list">{data.agents.map(agent=><div className="agent-row" key={agent.id}><span className={`agent-icon ${agent.role.includes('coordinated')?'cohort':''}`}><Fingerprint size={15}/></span><div className="agent-name"><b>{agent.name}</b><small>{agent.role}</small></div><span className={`agent-status ${agent.status}`}>{agent.status==='restricted'?<><LockKeyhole size={12}/>Restricted</>:<><i/>Active</>}</span><span className="agent-last">{agent.last_activity?`${Math.max(0,data.sim_time_s-agent.last_activity/1e9).toFixed(1)}s ago`:'—'}</span></div>)}</div>
          </section>
        </div>

        <div className="dashboard-grid lower-grid">
          <section className="panel graph-panel"><PanelTitle icon={<GitBranch size={16}/>} title="Agent interaction graph" subtitle="Observed trades and repeated synchronized behavior" trailing={<span className="graph-legend"><i/>Behavioral</span>}/>
            {nodes.length?<div className="flow-wrap"><ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{padding:.13}} minZoom={.5} maxZoom={1.4} nodesDraggable={false} nodesConnectable={false} elementsSelectable>
              <Background color="#24313d" gap={18}/><Controls showInteractive={false}/></ReactFlow>{data.graph.edges.length===0&&<div className="graph-empty"><div className="radar-illustration"><Radar size={25}/></div><b>Building relationship baseline</b><span>Repeated cross-agent patterns appear here as evidence accumulates.</span></div>}</div>
              :<div className="graph-placeholder"><Radar size={28}/><b>Start the simulation to populate the network</b><span>ARGUS tracks synchronized orders, cancellations, and executions.</span></div>}
            <div className="graph-foot"><span><i className="legend-green"/>10 agents monitored</span><span><i className="legend-amber"/>{data.graph.edges.filter(edge=>edge.kind==='behavior').length} behavior links</span><span><i className="legend-blue"/>{data.graph.edges.filter(edge=>edge.kind==='counterparty').length} trade links</span><span><CircleHelp size={13}/> Similarity is a lead, not proof</span></div>
          </section>

          <section className="panel cases-panel"><div className="case-tabs"><button className={tab==='cases'?'active':''} onClick={()=>setTab('cases')}><ShieldAlert size={15}/>Investigations <em>{data.cases.length}</em></button><button className={tab==='audit'?'active':''} onClick={()=>setTab('audit')}><Clock3 size={15}/>Audit trail</button></div>
            {tab==='cases'?<div className="case-content">{activeCase?<>
              <div className="case-card"><div className="case-card-head"><span className="severity"><i/>ELEVATED</span><span className="case-code">CASE {activeCase.id.toUpperCase()}</span></div><h3>{activeCase.title}</h3><p>{activeCase.agents.length} related agents · {activeCase.event_ids.length} supporting events</p>
                <div className="case-agents">{activeCase.agents.map(id=><span key={id}>AGENT {String(id).padStart(2,'0')}</span>)}</div>
                {activeCase.report?<div className="report-box"><div><Sparkles size={14}/><b>Investigation report</b><small>{activeCase.report.source}</small></div><p>{activeCase.report.summary}</p>{activeCase.report.limitations?.slice(0,1).map(item=><small key={item} className="caveat">{item}</small>)}{activeCase.report.llm_error&&<small className="caveat">{activeCase.report.llm_error}</small>}</div>
                  :<button className="button investigate-button" disabled={busy} onClick={()=>void investigate(activeCase.id)}><Sparkles size={15}/>Generate investigation</button>}
              </div>
              <div className="proposal-title"><span><ShieldCheck size={15}/>Governance recommendation</span><span className="human-tag"><Users size={12}/>Human approval required</span></div>
              {data.proposals.filter(p=>p.case_id===activeCase.id).map(proposal=><div className="proposal-card" key={proposal.id}><div className="proposal-icon"><LockKeyhole size={16}/></div><div className="proposal-copy"><b>Temporary agent restriction</b><small>Restrict agents {proposal.agents.map(id=>String(id).padStart(2,'0')).join(', ')} for 10 simulation seconds</small>
                <small className="proposal-reason">Repeated order events within 150 ms · {activeCase.graph.edges.length} links</small></div>
                {proposal.status==='pending'?<div className="proposal-actions"><button title="Approve restriction" className="approve" disabled={busy} onClick={()=>void approve(proposal.id)}><Check size={15}/></button><button title="Reject proposal" className="reject" disabled={busy} onClick={()=>void reject(proposal.id)}><X size={15}/></button></div>:<span className={`reviewed ${proposal.status}`}>{proposal.status}</span>}</div>)}
              </>:<div className="empty-case"><div className="empty-icon"><Search size={19}/></div><b>No active investigations</b><span>Coordinated patterns will appear here with their supporting evidence.</span></div>}</div>
              :<div className="audit-list">{data.audit.length?data.audit.map(item=><div className="audit-row" key={item.id}><span className="audit-icon"><ShieldCheck size={14}/></span><div><b>{item.action.replaceAll('_',' ')}</b><small>{item.case_id?`Case ${item.case_id}`:'Run event'} · {Object.keys(item.details).join(', ')}</small></div><time>{time(item.ts/1e9)}</time></div>):<div className="empty-case"><Clock3 size={21}/><b>No audit events yet</b><span>Decisions and exchange outcomes are recorded here.</span></div>}</div>}
          </section>
        </div>

        <section className="panel event-panel"><PanelTitle icon={<Activity size={16}/>} title="Live event stream" subtitle="Most recent exchange events" trailing={<button className="text-button">All events <ArrowUpRight size={13}/></button>}/>
          <div className="event-table-wrap"><table><thead><tr><th>TIME</th><th>EVENT</th><th>AGENT</th><th>SIDE</th><th>PRICE</th><th>QUANTITY</th><th>COUNTERPARTY</th></tr></thead><tbody>
            {[...data.events].reverse().slice(0,7).map(event=><tr key={event.id}><td className="mono">{time(event.ts/1e9)}</td><td><span className={`event-kind ${event.event_type.toLowerCase()}`}>{event.event_type}</span></td><td>Agent {String(event.trader_id).padStart(2,'0')}</td><td>{event.side>0?<span className="buy-side">BUY</span>:<span className="sell-side">SELL</span>}</td><td className="mono">{event.price?fmt(event.price):'MARKET'}</td><td className="mono">{fmt(event.qty)}</td><td>{event.counterparty_id?`Agent ${String(event.counterparty_id).padStart(2,'0')}`:'—'}</td></tr>)}
          </tbody></table>{data.events.length===0&&<div className="table-empty">Start the demo to see exchange events.</div>}</div>
        </section>
        <footer className="footer-note"><Shield size={13}/> ARGUS flags suspicious patterns for review. Alerts do not establish misconduct. <span>·</span>{data.model_status}</footer>
      </div>
    </main>
    {briefingOpen&&<IncidentBriefing data={data} busy={busy} onClose={()=>setBriefingOpen(false)} onStart={()=>void start()} onInvestigate={investigate} onApprove={approve} onReject={reject}/>}
  </div>;
}

function Metric({icon,label,value,sub,tone,trend}:{icon:React.ReactNode;label:string;value:string;sub:string;tone:string;trend?:boolean}) {
  return <div className="metric-card"><div className={`metric-icon ${tone}`}>{icon}</div><div className="metric-label">{label}</div><b className="metric-value">{value}</b><div className="metric-sub">{trend&&<ArrowUpRight size={13}/>} {sub}</div></div>;
}
function PanelTitle({icon,title,subtitle,trailing}:{icon:React.ReactNode;title:string;subtitle:string;trailing?:React.ReactNode}) {
  return <div className="panel-title"><div className="panel-title-icon">{icon}</div><div><h2>{title}</h2><small>{subtitle}</small></div>{trailing&&<div className="panel-trailing">{trailing}</div>}</div>;
}
