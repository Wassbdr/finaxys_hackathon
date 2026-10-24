import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, CircleHelp, Clock3, FileSearch, LockKeyhole, Pause, Play, RotateCcw, ShieldAlert, Sparkles, Users, X } from 'lucide-react';
import type { Snapshot } from './App';

type Props = {
  data: Snapshot;
  busy: boolean;
  onClose: () => void;
  onStart: () => void;
  onInvestigate: (id: string) => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
};

const steps = [
  { label: 'Observe', eyebrow: '01 · MARKET OBSERVABILITY', title: 'Ten agents. One synthetic market.', body: 'ARGUS watches orders, executions, and cancellations as they happen, establishing normal activity before highlighting unusual patterns.' },
  { label: 'Detect', eyebrow: '02 · BEHAVIORAL SIGNAL', title: 'A pattern emerges across three agents.', body: 'Repeated same-side actions arrive within a narrow time window. ARGUS links the behavior and preserves the supporting events as evidence.' },
  { label: 'Investigate', eyebrow: '03 · EVIDENCE REVIEW', title: 'Reconstruct the incident.', body: 'The investigator summarizes the sequence and cites observed events. A coordination signal is a lead for review, never proof of intent.' },
  { label: 'Govern', eyebrow: '04 · HUMAN GOVERNANCE', title: 'Recommend a proportionate response.', body: 'ARGUS proposes a short, scoped restriction. A human analyst reviews the evidence and chooses whether to approve or reject it.' },
  { label: 'Enforce & audit', eyebrow: '05 · EXCHANGE CONTROL', title: 'Apply the decision. Record the outcome.', body: 'After approval, the simulated exchange blocks new orders for the selected agents. The decision, rejected attempts, and expiry are auditable.' },
];
const fallbackAgents = Array.from({ length: 10 }, (_, id) => ({ id, name: `Agent ${String(id).padStart(2, '0')}`, role: id < 2 ? 'Market maker' : id < 4 ? 'Momentum' : id < 7 ? 'Flow agent' : 'Coordinated scenario' }));

export default function IncidentBriefing({ data, busy, onClose, onStart, onInvestigate, onApprove, onReject }: Props) {
  const [step, setStep] = useState(0);
  const [autoplay, setAutoplay] = useState(false);
  const activeCase = data.cases[0];
  const proposal = activeCase && data.proposals.find(item => item.case_id === activeCase.id && item.status === 'pending');
  const approved = activeCase && data.proposals.find(item => item.case_id === activeCase.id && item.status === 'approved');
  const caseAgents = activeCase?.agents.length ? activeCase.agents : [7, 8, 9];
  const realRestrictions = data.restrictions.map(item => item.agent_id);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowRight') setStep(current => Math.min(steps.length - 1, current + 1));
      if (event.key === 'ArrowLeft') setStep(current => Math.max(0, current - 1));
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  useEffect(() => {
    if (!autoplay) return;
    const timer = window.setInterval(() => setStep(current => (current + 1) % steps.length), 6500);
    return () => window.clearInterval(timer);
  }, [autoplay]);

  const advance = () => setStep(current => Math.min(steps.length - 1, current + 1));
  const isTerminal = data.status === 'ready' || data.status === 'stopped';

  return <div className="briefing-backdrop" role="dialog" aria-modal="true" aria-label="ARGUS 3D incident briefing">
    <div className="briefing-shell">
      <header className="briefing-header">
        <div className="briefing-brand"><span className="briefing-mark"><ShieldAlert size={17}/></span><div><b>ARGUS</b><small>INCIDENT BRIEFING</small></div></div>
        <div className="briefing-head-center"><span className="briefing-live-dot"/>{data.status === 'running' ? 'LIVE SYNTHETIC RUN' : data.status === 'paused' ? 'SIMULATION PAUSED' : 'PRESENTATION WALKTHROUGH'}<i/>10 AGENTS</div>
        <button className="briefing-close" onClick={onClose} aria-label="Close 3D briefing"><X size={18}/></button>
      </header>

      <nav className="briefing-steps" aria-label="Briefing stages">
        {steps.map((item, index) => <button key={item.label} className={`${step === index ? 'active' : ''} ${step > index ? 'complete' : ''}`} onClick={() => setStep(index)}>
          <span className="briefing-step-index">{step > index ? <Check size={12}/> : `0${index + 1}`}</span><span>{item.label}</span>{index < steps.length - 1 && <i/>}
        </button>)}
      </nav>

      <div className="briefing-body">
        <section className="briefing-stage" aria-label="Interactive 3D agent network">
          <div className="scene-caption"><span className="scene-caption-dot"/>{step === 0 ? 'NORMAL ACTIVITY BASELINE' : step === 1 ? 'SYNCHRONIZED BEHAVIOR SIGNAL' : step === 2 ? 'EVIDENCE LINKS' : step === 3 ? 'PROPOSED GOVERNANCE ACTION' : 'RESTRICTION & AUDIT STATE'}</div>
          <Scene data={data} step={step} focusAgents={caseAgents} restrictedAgents={realRestrictions}/>
          <div className="scene-legend"><span><i className="scene-legend-trade"/>Exchange activity</span><span><i className="scene-legend-risk"/>Related behavior</span><span><i className="scene-legend-restricted"/>Restricted</span></div>
          <div className="scene-hint"><CircleHelp size={12}/> Drag to orbit · Scroll to zoom</div>
          <div className="scene-stats"><span><small>SIM TIME</small><b>{Math.floor(data.sim_time_s / 60).toString().padStart(2, '0')}:{Math.floor(data.sim_time_s % 60).toString().padStart(2, '0')}</b></span><span><small>EVENTS</small><b>{data.market.event_count.toLocaleString()}</b></span><span><small>LINKS</small><b>{data.graph.edges.length || (step > 0 ? 3 : 0)}</b></span></div>
        </section>

        <aside className="briefing-story">
          <div className="briefing-story-top"><span>{steps[step].eyebrow}</span><span className="story-seed">SEED {data.seed}</span></div>
          <h1>{steps[step].title}</h1><p className="briefing-copy">{steps[step].body}</p>

          <div className="story-evidence">
            <div className="evidence-heading"><b>{step < 2 ? 'SURVEILLANCE SNAPSHOT' : step === 2 ? 'INVESTIGATION EVIDENCE' : step === 3 ? 'GOVERNANCE REVIEW' : 'ENFORCEMENT OUTCOME'}</b><small>{activeCase ? `CASE ${activeCase.id.toUpperCase()}` : 'DEMO SCENARIO'}</small></div>
            {step < 2 && <div className="evidence-row"><span className="evidence-icon blue"><Users size={14}/></span><div><b>10 active participants</b><small>2 market makers · 2 momentum · 6 flow agents</small></div><span className="evidence-value">10/10</span></div>}
            {step >= 1 && <div className="evidence-row"><span className="evidence-icon amber"><ShieldAlert size={14}/></span><div><b>{activeCase ? activeCase.agents.length : 3} linked agents</b><small>{caseAgents.map(id => `AGENT ${String(id).padStart(2, '0')}`).join(' · ')}</small></div><span className="evidence-value alert">SIGNAL</span></div>}
            {step >= 2 && <div className="evidence-row"><span className="evidence-icon blue"><FileSearch size={14}/></span><div><b>{activeCase?.event_ids.length ?? 0} supporting events</b><small>{activeCase?.report ? `Report ready · ${activeCase.report.source}` : 'Event-linked behavioral evidence'}</small></div><span className={`evidence-value ${activeCase?.report ? 'good' : ''}`}>{activeCase?.report ? 'READY' : 'EVIDENCE'}</span></div>}
            {step >= 3 && <div className="evidence-row"><span className="evidence-icon amber"><LockKeyhole size={14}/></span><div><b>Temporary order-entry restriction</b><small>Scoped to linked agents · 10 simulated seconds</small></div><span className="evidence-value">REVIEW</span></div>}
            {step >= 4 && <div className="evidence-row"><span className="evidence-icon green"><Clock3 size={14}/></span><div><b>{data.market.rejected_count.toLocaleString()} rejected order attempts</b><small>{realRestrictions.length ? `${realRestrictions.length} agents currently restricted` : 'Waiting for an approved action'}</small></div><span className={`evidence-value ${approved || realRestrictions.length ? 'good' : ''}`}>{approved || realRestrictions.length ? 'ACTIVE' : 'PENDING'}</span></div>}
          </div>

          <div className="briefing-note"><Sparkles size={14}/><span>{step < 2 ? activeCase ? `Current case links ${caseAgents.map(id => `Agent ${String(id).padStart(2, '0')}`).join(', ')}. This is a behavioral signal, not a finding of intent.` : 'Seeded synthetic scenario: agents 08–10 exhibit the planted behavior.' : step === 2 ? 'Reports cite observed evidence; the LLM cannot take enforcement actions.' : step === 3 ? 'A human analyst remains the decision maker.' : 'All actions are enforced by the simulated exchange and recorded.'}</span></div>

          <div className="briefing-actions">
            {step === 0 && isTerminal && <button className="briefing-primary" disabled={busy} onClick={onStart}><Play size={15}/>Start 10-agent simulation</button>}
            {step === 2 && activeCase && !activeCase.report && <button className="briefing-primary" disabled={busy} onClick={() => onInvestigate(activeCase.id)}><Sparkles size={15}/>Generate investigation</button>}
            {step === 3 && proposal && <div className="briefing-decision"><button className="briefing-approve" disabled={busy} onClick={() => onApprove(proposal.id)}><Check size={14}/>Approve</button><button className="briefing-reject" disabled={busy} onClick={() => onReject(proposal.id)}><X size={14}/>Reject</button></div>}
            {step === 3 && approved && <div className="briefing-approved"><Check size={14}/>Approved by human analyst</div>}
            <div className="briefing-nav-actions">
              <button className="briefing-autoplay" onClick={() => setAutoplay(value => !value)}>{autoplay ? <Pause size={14}/> : <Play size={14}/>} {autoplay ? 'Pause story' : 'Auto-play'}</button>
              {step === steps.length - 1 ? <button className="briefing-next" onClick={() => setStep(0)}><RotateCcw size={14}/>Replay</button> : <button className="briefing-next" onClick={advance}>Next stage <ArrowRight size={14}/></button>}
            </div>
          </div>
          <div className="briefing-footnote">Suspicious activity is a lead for review, not a finding of misconduct.</div>
        </aside>
      </div>
      <footer className="briefing-footer"><span>ARGUS <i>·</i> OBSERVE. INVESTIGATE. GOVERN.</span><span><button onClick={() => setStep(current => Math.max(0, current - 1))} disabled={step === 0} aria-label="Previous stage"><ArrowLeft size={14}/></button><b>0{step + 1}</b><span>/ 05</span><button onClick={() => setStep(current => Math.min(steps.length - 1, current + 1))} disabled={step === steps.length - 1} aria-label="Next stage"><ArrowRight size={14}/></button></span></footer>
    </div>
  </div>;
}

type SceneProps = { data: Snapshot; step: number; focusAgents: number[]; restrictedAgents: number[] };
function Scene({ data, step, focusAgents, restrictedAgents }: SceneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef({ data, step, focusAgents, restrictedAgents });
  const yawRef = useRef(-0.22);
  const zoomRef = useRef(1);
  const dragRef = useRef<{ x: number; pointer: number } | null>(null);
  sceneRef.current = { data, step, focusAgents, restrictedAgents };

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    let frame = 0;
    let width = 0;
    let height = 0;
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const render = (now: number) => {
      const { data: live, step: currentStep, focusAgents: focus, restrictedAgents: restricted } = sceneRef.current;
      const agents = live.agents.length ? live.agents : fallbackAgents.map(agent => ({ ...agent, status: 'active', last_activity: 0 }));
      const ids = agents.map(agent => agent.id);
      const radius = Math.min(width * .31, height * .40, 260) * zoomRef.current;
      const center = { x: width * .51, y: height * .58 };
      const cameraYaw = yawRef.current + Math.sin(now * .00016) * .035;
      const scale = Math.min(width / 1120, height / 650, 1.12);
      const cos = Math.cos(cameraYaw), sin = Math.sin(cameraYaw);
      const project = (x: number, y: number, z: number) => {
        const rx = x * cos - z * sin;
        const rz = x * sin + z * cos;
        const perspective = 830 / (830 + rz + 230);
        return { x: center.x + rx * scale * perspective, y: center.y + (rz * .33 - y) * scale * perspective, perspective };
      };
      const position = (id: number, y = 0) => {
        const index = Math.max(0, ids.indexOf(id));
        const angle = index / Math.max(1, ids.length) * Math.PI * 2 - Math.PI / 2;
        return { x: Math.cos(angle) * radius, y, z: Math.sin(angle) * radius };
      };
      ctx.clearRect(0, 0, width, height);
      const bg = ctx.createLinearGradient(0, 0, 0, height);
      bg.addColorStop(0, '#0b1520'); bg.addColorStop(1, '#080e15');
      ctx.fillStyle = bg; ctx.fillRect(0, 0, width, height);
      const glow = ctx.createRadialGradient(center.x, center.y - 30, 4, center.x, center.y, Math.max(width, height) * .58);
      glow.addColorStop(0, '#17324a55'); glow.addColorStop(1, '#0a111a00');
      ctx.fillStyle = glow; ctx.fillRect(0, 0, width, height);

      // Ambient stars make the stage feel dimensional without obscuring the network.
      for (let i = 0; i < 50; i++) {
        const x = ((i * 173 + 37) % 997) / 997 * width;
        const y = ((i * 89 + 23) % 431) / 431 * height * .72;
        ctx.fillStyle = `rgba(140,185,220,${.08 + ((i * 7) % 10) / 100})`;
        ctx.fillRect(x, y, i % 8 === 0 ? 2 : 1, i % 8 === 0 ? 2 : 1);
      }

      // Circular floor grid rendered as projected 3D rings.
      for (let ring = 1; ring <= 5; ring++) {
        ctx.beginPath();
        for (let i = 0; i <= 100; i++) {
          const angle = i / 100 * Math.PI * 2;
          const p = project(Math.cos(angle) * radius * ring / 5, -3, Math.sin(angle) * radius * ring / 5);
          i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
        }
        ctx.closePath(); ctx.strokeStyle = ring === 5 ? '#34526a66' : '#29415355'; ctx.lineWidth = 1; ctx.stroke();
      }
      for (let line = 0; line < 16; line++) {
        const angle = line / 16 * Math.PI * 2;
        const a = project(0, -3, 0), b = project(Math.cos(angle) * radius, -3, Math.sin(angle) * radius);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.strokeStyle = '#29415344'; ctx.lineWidth = 1; ctx.stroke();
      }

      // Exchange spokes show ordinary market participation.
      agents.forEach((agent, index) => {
        const p = position(agent.id, 0);
        const end = project(p.x, p.y, p.z);
        const start = project(0, 17, 0);
        const highlighted = currentStep > 0 && focus.includes(agent.id);
        ctx.beginPath(); ctx.moveTo(start.x, start.y); ctx.lineTo(end.x, end.y);
        ctx.strokeStyle = highlighted ? '#f0ae5274' : '#55a7e72d';
        ctx.setLineDash(highlighted ? [4, 5] : []); ctx.lineWidth = highlighted ? 1.6 : 1; ctx.stroke(); ctx.setLineDash([]);
        const travel = (now * .00018 + index / agents.length) % 1;
        const px = start.x + (end.x - start.x) * travel;
        const py = start.y + (end.y - start.y) * travel;
        ctx.beginPath(); ctx.arc(px, py, highlighted ? 2.1 : 1.5, 0, Math.PI * 2);
        ctx.fillStyle = highlighted ? '#ffc66a' : '#8bcaff'; ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 9; ctx.fill(); ctx.shadowBlur = 0;
      });

      // Behavior links are intentionally separate from trade flow.
      const behaviorPairs = new Set<string>();
      if (currentStep > 0) {
        const actual = live.graph.edges.filter(edge => edge.kind === 'behavior');
        const pairs = actual.length ? actual.map(edge => [Number(edge.source), Number(edge.target)] as [number, number]) : [[focus[0] ?? 7, focus[1] ?? 8], [focus[1] ?? 8, focus[2] ?? 9], [focus[2] ?? 9, focus[0] ?? 7]] as [number, number][];
        for (const [a, b] of pairs) {
          behaviorPairs.add(`${a}-${b}`); behaviorPairs.add(`${b}-${a}`);
          const pa = position(a, 2), pb = position(b, 2);
          const A = project(pa.x, pa.y, pa.z), B = project(pb.x, pb.y, pb.z);
          ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.quadraticCurveTo(center.x, center.y - 45, B.x, B.y);
          ctx.strokeStyle = currentStep >= 4 && (restricted.includes(a) || restricted.includes(b)) ? '#f07478cc' : '#f2b15ddb';
          ctx.lineWidth = 1.5 + Math.min(1.5, live.graph.edges.find(edge => Number(edge.source) === a && Number(edge.target) === b)?.weight ?? 1);
          ctx.shadowColor = '#f3a943'; ctx.shadowBlur = 12; ctx.stroke(); ctx.shadowBlur = 0;
          const pulse = (now * .00022 + a * .13) % 1;
          const q = 1 - pulse;
          const point = { x: (1-q)*(1-q)*A.x + 2*(1-q)*q*center.x + q*q*B.x, y: (1-q)*(1-q)*A.y + 2*(1-q)*q*(center.y-45) + q*q*B.y };
          ctx.beginPath(); ctx.arc(point.x, point.y, 2.5, 0, Math.PI * 2); ctx.fillStyle = '#ffd17e'; ctx.fill();
        }
      }

      // Low polygon exchange plinth and luminous market core.
      const corners = [[-43, -32], [43, -32], [43, 32], [-43, 32]] as const;
      const base = corners.map(([x, z]) => project(x, 0, z));
      const top = corners.map(([x, z]) => project(x, 34, z));
      for (let i = 0; i < 4; i++) {
        const j = (i + 1) % 4;
        ctx.beginPath(); ctx.moveTo(base[i].x, base[i].y); ctx.lineTo(base[j].x, base[j].y); ctx.lineTo(top[j].x, top[j].y); ctx.lineTo(top[i].x, top[i].y); ctx.closePath();
        ctx.fillStyle = i === 0 ? '#112b3d' : '#0d202f'; ctx.fill(); ctx.strokeStyle = '#4a91bd'; ctx.lineWidth = 1; ctx.stroke();
      }
      ctx.beginPath(); top.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)); ctx.closePath();
      const core = ctx.createLinearGradient(center.x - 30, center.y - 25, center.x + 25, center.y + 12);
      core.addColorStop(0, '#22618a'); core.addColorStop(1, '#173248'); ctx.fillStyle = core; ctx.fill(); ctx.strokeStyle = '#73c9f4'; ctx.lineWidth = 1.3; ctx.shadowColor = '#53b9ef'; ctx.shadowBlur = 18; ctx.stroke(); ctx.shadowBlur = 0;
      ctx.textAlign = 'center'; ctx.font = '700 9px ui-monospace, monospace'; ctx.fillStyle = '#d2efff'; ctx.fillText('ARGUS EXCHANGE', center.x, center.y - 4);
      ctx.font = '7px ui-monospace, monospace'; ctx.fillStyle = '#77b4d4'; ctx.fillText('SYNTHETIC VENUE', center.x, center.y + 9);

      // Sort markers by projected depth so the orbital network reads as a 3D scene.
      const sorted = [...agents].sort((a, b) => position(a.id).z - position(b.id).z);
      for (const agent of sorted) {
        const p = position(agent.id);
        const basePoint = project(p.x, 0, p.z);
        const marker = project(p.x, 18, p.z);
        const isFocus = currentStep > 0 && focus.includes(agent.id);
        const isRestricted = restricted.includes(agent.id) || agent.status === 'restricted';
        ctx.beginPath(); ctx.moveTo(basePoint.x, basePoint.y); ctx.lineTo(marker.x, marker.y); ctx.strokeStyle = isRestricted ? '#e66c74' : isFocus ? '#edb75f' : '#4b8ab7'; ctx.lineWidth = 1; ctx.stroke();
        ctx.beginPath(); ctx.ellipse(basePoint.x, basePoint.y + 2, 17 * marker.perspective, 5 * marker.perspective, 0, 0, Math.PI * 2);
        ctx.fillStyle = isRestricted ? '#e04b5430' : isFocus ? '#e9a7422d' : '#53a5e71c'; ctx.fill();
        const r = Math.max(7, 11 * marker.perspective);
        ctx.beginPath(); ctx.arc(marker.x, marker.y, r + 5, 0, Math.PI * 2); ctx.fillStyle = isRestricted ? '#ef767822' : isFocus ? '#f0b3531c' : '#55b1f31a'; ctx.fill();
        ctx.beginPath(); ctx.arc(marker.x, marker.y, r, 0, Math.PI * 2);
        ctx.fillStyle = isRestricted ? '#702c34' : isFocus ? '#654925' : '#17364c'; ctx.strokeStyle = isRestricted ? '#fa858a' : isFocus ? '#f2bd68' : '#72b9e8'; ctx.lineWidth = 1.2; ctx.shadowColor = ctx.strokeStyle; ctx.shadowBlur = isFocus || isRestricted ? 13 : 5; ctx.fill(); ctx.stroke(); ctx.shadowBlur = 0;
        ctx.beginPath(); ctx.arc(marker.x, marker.y, 2.1 * marker.perspective, 0, Math.PI * 2); ctx.fillStyle = isRestricted ? '#ffb2b2' : isFocus ? '#ffdc9c' : '#b4e2ff'; ctx.fill();
        ctx.textAlign = 'center'; ctx.font = `600 ${Math.max(8, 9 * marker.perspective)}px ui-monospace, monospace`; ctx.fillStyle = isRestricted ? '#ffb0b4' : isFocus ? '#f3cd8c' : '#c7d7e4'; ctx.fillText(String(agent.name ?? `Agent ${agent.id}`).replace('Market maker ', 'MM ').replace('Momentum ', 'MOM ').replace('Flow agent ', 'AGENT ').replace('Noise trader', 'AGENT '), marker.x, marker.y - r - 8);
        ctx.font = '7px ui-monospace, monospace'; ctx.fillStyle = isRestricted ? '#ed8588' : isFocus ? '#c29451' : '#70889b'; ctx.fillText(isRestricted ? 'RESTRICTED' : `A${String(agent.id).padStart(2, '0')}`, marker.x, marker.y + r + 12);
      }
      frame = requestAnimationFrame(render);
    };
    frame = requestAnimationFrame(render);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, []);

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    dragRef.current = { x: event.clientX, pointer: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return;
    yawRef.current += (event.clientX - dragRef.current.x) * .006;
    dragRef.current.x = event.clientX;
  };
  const stopDrag = () => { dragRef.current = null; };
  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    zoomRef.current = Math.max(.7, Math.min(1.35, zoomRef.current - event.deltaY * .0007));
  };
  return <canvas ref={canvasRef} className="briefing-canvas" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={stopDrag} onPointerCancel={stopDrag} onWheel={onWheel} aria-label="3D visualization of the synthetic exchange and ten agents"/>;
}
