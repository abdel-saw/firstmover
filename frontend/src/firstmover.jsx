import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ArrowUpRight, ArrowRight, ArrowLeft, Check, CheckCircle2, Circle, Clock3, Plus, Sparkles, ShieldCheck, RefreshCw, X, Users, Wallet, Layers, ExternalLink, AlertTriangle, Settings2, Wand2, LockKeyhole, Play, Download, ChevronRight} from 'lucide-react';
import './firstmover.css';

const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format((n||0)/100);
const preset={title:'Sunday printmaking club',description:'A hands-on printmaking workshop for a small group. We run the session only when three people commit. The first two participants receive a $1 organizer-funded bonus if the group does not fill.',threshold:3,price_cents:1000,early_slots:2,bonus_cents:100,duration_minutes:15};
const terminal=['funded','released','recovered'];
const names=['Alex','Jordan','Taylor','Sam','Robin','Casey'];
const stateName={open:'Gathering the group',settling:'Settlement in progress',review:'Needs reconciliation',recovering:'Recovery in progress',funded:'Group funded',released:'Holds released · bonuses paid',recovered:'Payments recovered'};
async function api(path,options={}){
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  const value=await response.json().catch(()=>({detail:'The server returned an unreadable response.'}));
  if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));
  return value;
}
function PayPalButton({clientId,campaign,name,onComplete,onError}){
  const ref=useRef(null);const [accepted,setAccepted]=useState(false);const [loading,setLoading]=useState(false);
  useEffect(()=>{
    if(!accepted||!clientId)return;
    let disposed=false,buttons;
    async function mount(){
      setLoading(true);
      try{
        if(!window.paypal){
          await new Promise((resolve,reject)=>{
            const existing=document.getElementById('firstmover-paypal-sdk');
            if(existing){existing.addEventListener('load',resolve,{once:true});existing.addEventListener('error',reject,{once:true});return;}
            const script=document.createElement('script');script.id='firstmover-paypal-sdk';
            script.src='https://www.paypal.com/sdk/js?'+new URLSearchParams({'client-id':clientId,currency:'USD',intent:'authorize',components:'buttons'});
            script.onload=resolve;script.onerror=()=>{script.remove();reject(new Error('Could not load PayPal checkout.'));};document.head.appendChild(script);
          });
        }
        if(disposed)return;
        let pledgeId;
        buttons=window.paypal.Buttons({
          fundingSource:window.paypal.FUNDING.PAYPAL,
          style:{layout:'vertical',shape:'rect',label:'paypal',height:45},
          createOrder:async()=>{const result=await api('/firstmover/campaigns/'+campaign.id+'/join',{method:'POST',body:JSON.stringify({name:name.trim()||'Participant',terms_digest:campaign.terms_digest,accepted:true})});pledgeId=result.commitment_id;return result.order_id;},
          onApprove:async()=>{try{const result=await api('/firstmover/pledges/'+pledgeId+'/authorize',{method:'POST'});onComplete(result);}catch(e){onError(e.message);}},
          onCancel:()=>onError('PayPal approval was cancelled. No authorization is recorded. Reconcile any unfinished checkout before closing.'),
          onError:e=>onError(e.message||'PayPal checkout could not complete. Refresh and reconcile before trying again.')
        });
        await buttons.render(ref.current);
      }catch(e){onError(e.message);}finally{if(!disposed)setLoading(false);}
    }
    mount();return()=>{disposed=true;buttons?.close().catch(()=>{});};
  },[accepted,clientId,campaign.id,name]);
  return <><label className="fm-check"><input type="checkbox" checked={accepted} onChange={e=>setAccepted(e.target.checked)}/><span>I accept these exact terms and approve a temporary {money(campaign.terms.price_cents)} sandbox hold. This is not a completed payment.</span></label>{loading&&<p>Opening PayPal…</p>}<div ref={ref}/></>;
}
function App({onSignOut}){
  const [config,setConfig]=useState({}),[campaigns,setCampaigns]=useState([]),[campaign,setCampaign]=useState(null);
  const [view,setView]=useState('campaign'),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [terms,setTerms]=useState(preset),[brief,setBrief]=useState('I teach printmaking. I need three participants paying $10 each. I can offer a $1 bonus to the first two if the group does not fill. Give people 15 minutes to decide.');
  const [proposal,setProposal]=useState(null),[approved,setApproved]=useState(false),[mode,setMode]=useState('demo');
  const [recipient,setRecipient]=useState(''),[probeApproved,setProbeApproved]=useState(false),[probe,setProbe]=useState(null),[now,setNow]=useState(Date.now());
  async function run(label,fn){setBusy(label);setError('');setNotice('');try{await fn();}catch(e){setError(e.message);}finally{setBusy('');}}
  async function refresh(id=campaign?.id){
    const [items,conf]=await Promise.all([api('/firstmover/campaigns'),api('/firstmover/config')]);setCampaigns(items);setConfig(conf);
    if(id){const row=await api('/firstmover/campaigns/'+id);setCampaign(row);return row;}
    if(items.length)setCampaign(items[items.length-1]);
  }
  useEffect(()=>{run('Opening workspace',async()=>{await refresh();});},[]);
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  const t=campaign?.terms||terms;
  const active=campaign?.authorized_count||0;
  const left=campaign?Math.max(0,Math.ceil(campaign.deadline-now/1000)):0;
  const remaining=`${Math.floor(left/60)}:${String(left%60).padStart(2,'0')}`;
  function change(key,value){setTerms(v=>({...v,[key]:value}));setApproved(false);setProposal(null);}
  async function select(id){await run('Loading campaign',async()=>{setCampaign(await api('/firstmover/campaigns/'+id));setView('campaign');});}
  async function joinDemo(){await run('Recording demo commitment',async()=>{const row=await api('/firstmover/campaigns/'+campaign.id+'/join',{method:'POST',body:JSON.stringify({name:names[campaign.pledges.length%names.length],accepted:true,terms_digest:campaign.terms_digest})});setCampaign(row);await refresh(row.id);});}
  async function settle(){await run('Reconciling settlement',async()=>{
    let row=campaign;
    if(row.state==='open')row=await api('/firstmover/campaigns/'+row.id+'/close',{method:'POST'});
    setCampaign(row);
    // Each server step reserves one operation and verifies it. Stop on pending provider states.
    for(let i=0;i<15&&!terminal.includes(row.state);i++){
      row=await api('/firstmover/campaigns/'+row.id+'/step',{method:'POST'});setCampaign(row);
      if(row.operations.some(op=>op.state==='pending'||op.state==='review'))break;
    }
    await refresh(row.id);
  });}
  function download(){const blob=new Blob([JSON.stringify(campaign,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='firstmover-'+campaign.id+'.json';a.click();URL.revokeObjectURL(a.href);}
  return <div className="fm-app">
    <header className="fm-header"><a className="fm-logo" href="/" aria-label="FirstMover home"><span className="fm-mark"><i/><i/><i/></span>firstmover<span className="fm-logo-dot">.</span></a><nav aria-label="Workspace"><button className={view==='campaign'?'active':''} onClick={()=>setView('campaign')}>Campaign desk</button><button className={view==='studio'?'active':''} onClick={()=>setView('studio')}>Terms studio <Sparkles size={13}/></button></nav><button className="fm-connection" onClick={()=>setView('connections')}><span className={config.payout_verified?'fm-live-dot':'fm-neutral-dot'}/>{config.payout_verified?'Payout verified':'Sandbox setup'}<Settings2 size={15}/></button></header>
    <main className="fm-main">
      <div className="fm-context"><span>PAYPAL + AI / EXPERIMENTAL COMMERCE</span><span><ShieldCheck size={13}/> Sandbox only · virtual funds</span></div>
      {error&&<div className="fm-alert error" role="alert"><AlertTriangle size={18}/><div>{error}</div><button aria-label="Dismiss error" onClick={()=>setError('')}><X size={16}/></button></div>}
      {notice&&<div className="fm-alert" role="status"><CheckCircle2 size={18}/>{notice}</div>}
      {busy&&<div className="fm-busy" role="status"><RefreshCw size={14}/>{busy}…</div>}
      {view==='campaign'&&<>
        <section className="fm-hero"><div><div className="fm-eyebrow"><span/> A REASON TO GO FIRST</div><h1>Someone has to<br/>make the <em>first move.</em></h1><p>Commit to a shared experience. If the group fills, you're in.<br className="fm-wide"/> If it doesn't, early participants get an organizer-funded bonus.</p><button className="fm-btn primary" disabled={!!busy} onClick={()=>{setView('studio');setApproved(false);}}>Create a campaign <ArrowUpRight size={17}/></button></div><div className="fm-mechanism" aria-label="Two possible settlement outcomes"><div className="fm-mechanism-top"><span>THE FIRSTMOVER MECHANISM</span><span>01 / 02</span></div><div className="fm-avatar-row"><span>A</span><span>J</span><span className="outline">?</span><div><strong>Go together.</strong><small>Three commitments. One possibility.</small></div></div><div className="fm-path"><CheckCircle2 size={21}/><div><strong>The group fills</strong><span>Authorized commitments become payments.</span></div><span className="fm-tag green">CAPTURE</span></div><div className="fm-path"><ArrowLeft size={21}/><div><strong>The group doesn't fill</strong><span>Release holds. Pay eligible early bonuses.</span></div><span className="fm-tag coral">VOID + PAYOUT</span></div><div className="fm-mechanism-foot"><LockKeyhole size={13}/> Your approval first. Provider receipts afterward.</div></div></section>
        <div className="fm-section-heading"><div><span className="fm-eyebrow">YOUR EXPERIMENT</span><h2>From “maybe” to a committed group.</h2></div><button className="fm-text-button" disabled={!!busy} onClick={()=>run('Refreshing records',()=>refresh())}><RefreshCw size={14}/> Refresh</button></div>
        {!campaign?<section className="fm-empty"><Layers size={32}/><h3>Try the two outcomes.</h3><p>Create a local demo campaign, add participants, and watch the settlement ledger change. Switch to sandbox after verifying Payouts.</p><button className="fm-btn primary" onClick={()=>setView('studio')}>Open terms studio <ArrowRight size={16}/></button></section>:<>
          <div className="fm-campaign-bar"><div className="fm-select-wrap"><label htmlFor="campaign-choice">Current campaign</label><select id="campaign-choice" value={campaigns.some(x=>x.id===campaign.id)?campaign.id:''} onChange={e=>select(e.target.value)}><option value="" disabled>Shared campaign</option>{campaigns.map(c=><option key={c.id} value={c.id}>{c.terms.title}</option>)}</select></div><span className={'fm-mode '+(campaign.mode==='demo'?'demo':'sandbox')}>{campaign.mode==='demo'?'LOCAL SIMULATION':'PAYPAL SANDBOX'}</span>{campaign.mode==='sandbox'&&<button className="fm-text-button" onClick={()=>{navigator.clipboard.writeText(location.origin+'/campaign/'+campaign.id).then(()=>setNotice('Campaign link copied. Participants need their own sandbox buyer account.')).catch(()=>setNotice('Share this link: '+location.origin+'/campaign/'+campaign.id));}}><ExternalLink size={14}/> Share</button>}</div>
          {campaign.mode==='demo'&&<div className="fm-demo-note"><Play size={13}/> Simulated commitments and receipts. No PayPal money moves in this mode.</div>}
          <section className="fm-workbench"><article className="fm-campaign-card"><div className="fm-card-top"><span className="fm-tag neutral">{stateName[campaign.state]||campaign.state}</span><span><Clock3 size={13}/>{campaign.state==='open'?remaining+' remaining':'Terms frozen'}</span></div><h2>{t.title}</h2><p className="fm-description">{t.description}</p><div className="fm-price"><strong>{money(t.price_cents)}</strong><span>per person · authorized first</span></div><div className="fm-progress-label"><strong>{active} / {t.threshold} committed</strong><span>{active>=t.threshold?'The group is complete':`${Math.max(0,t.threshold-active)} more to make it happen`}</span></div><div className="fm-progress"><i style={{width:Math.min(100,active/t.threshold*100)+'%'}}/></div><div className="fm-seat-row">{Array.from({length:t.threshold},(_,i)=><div key={i} className={i<active?'filled':''}><span>{i<active?names[i]?.[0]:<Plus size={17}/>}</span><small>{i<t.early_slots?'Early place':'Group place'}</small></div>)}</div><div className="fm-bonus"><span className="fm-bonus-icon"><Sparkles size={20}/></span><div><strong>{money(t.bonus_cents)} for going first</strong><p>First {t.early_slots} confirmed participants, only if the group misses its target. Paid by the organizer.</p></div></div><div className="fm-actions">{campaign.state==='open'&&<>
            {campaign.mode==='demo'?<button className="fm-btn primary" disabled={!!busy||active>=t.threshold||left===0} onClick={joinDemo}><Plus size={16}/> Add demo participant</button>:<a className="fm-btn primary" href={'/campaign/'+campaign.id}>Open participant page <ArrowUpRight size={16}/></a>}
            {campaign.is_owner&&<button className="fm-btn secondary" disabled={!!busy||(campaign.mode==='sandbox'&&(active<t.threshold&&left>0))} onClick={settle}>{active>=t.threshold?'Approve & fund group':campaign.mode==='demo'?'Advance deadline & settle':'Settle unfilled group'}</button>}
          </>}{campaign.is_owner&&['settling','review','recovering'].includes(campaign.state)&&<><button className="fm-btn primary" disabled={!!busy} onClick={settle}><RefreshCw size={15}/> Resume / reconcile</button>{campaign.state!=='recovering'&&<button className="fm-btn secondary" disabled={!!busy} onClick={()=>run('Approving recovery',async()=>{setCampaign(await api('/firstmover/campaigns/'+campaign.id+'/recover',{method:'POST'}));})}>Cancel & recover</button>}</>}{terminal.includes(campaign.state)&&<div className="fm-final"><CheckCircle2 size={19}/>{stateName[campaign.state]}</div>}</div></article>
          <aside className="fm-ledger"><div className="fm-ledger-head"><div><Wallet size={18}/><h3>Settlement ledger</h3></div><button className="fm-text-button" aria-label="Export campaign record" onClick={download}><Download size={15}/></button></div><div className="fm-ledger-totals"><div><span>Group target</span><strong>{money(t.threshold*t.price_cents)}</strong></div><div><span>Maximum bonuses*</span><strong>{money(t.early_slots*t.bonus_cents)}</strong></div></div><p className="fm-ledger-note">*Organizer liability, excluding fees. This is not a funded or segregated reserve.</p><div className="fm-ledger-label"><span>PARTICIPANT</span><span>STATE</span></div>{campaign.pledges.length?campaign.pledges.map((p,i)=><div className="fm-person" key={p.id}><span className={'fm-person-avatar color'+i%3}>{p.name[0]?.toUpperCase()}</span><div><strong>{p.name}</strong><small>{(campaign.eligible_ids?campaign.eligible_ids.includes(p.id):p.early)?'Early participant':'Participant'}</small>{p.order_id&&<code>{p.order_id}</code>}</div><span className={'fm-pledge-state '+p.state}>{p.state.replaceAll('_',' ')}</span>{['review','creating','authorizing','awaiting_approval'].includes(p.state)&&(p.is_mine||campaign.is_owner)&&<button className="fm-text-button" disabled={!!busy} onClick={()=>run('Reading checkout state',async()=>{await api('/firstmover/pledges/'+p.id+'/reconcile',{method:'POST'});await refresh(campaign.id);})}>Reconcile</button>}</div>):<div className="fm-ledger-empty"><Users size={24}/><p>No commitments yet.<br/>Every group starts with one person.</p></div>}
          <div className="fm-ledger-label"><span>PROVIDER OPERATIONS</span><span>{campaign.mode==='demo'?'SIMULATED':'READ BACK'}</span></div>{campaign.operations.length?campaign.operations.map(op=><div className="fm-operation" key={op.id}>{op.state==='completed'?<CheckCircle2 size={15}/>:<Clock3 size={15}/>}<div><strong>{op.label}</strong><small>{op.status||op.state}</small>{op.provider_id&&<code>{op.provider_id}</code>}{op.message&&<small>{op.message}</small>}</div></div>):<p className="fm-no-ops">Settlement receipts appear here after an operation runs.</p>}<div className="fm-ledger-bottom"><ShieldCheck size={14}/> Unknown outcomes stay unresolved.</div></aside></section>
          {campaign.audit&&<details className="fm-audit"><summary>Campaign audit trail <span>{campaign.audit.length} events</span></summary>{campaign.audit.map((a,i)=><div key={i}><time>{new Date(a.at*1000).toLocaleTimeString()}</time><strong>{a.action.replaceAll('_',' ')}</strong><p>{a.detail}</p></div>)}</details>}
        </>}
      </>}
      {view==='studio'&&<section className="fm-studio"><div className="fm-studio-intro"><span className="fm-eyebrow">TERMS STUDIO</span><h1>Make the promise<br/><em>explicit.</em></h1><p>AI drafts a proposal. You decide the numbers, review the exposure, and approve the exact terms.</p><label htmlFor="brief">Describe the group experience</label><textarea id="brief" value={brief} maxLength={4000} onChange={e=>setBrief(e.target.value)} rows={6}/><button className="fm-btn dark full" disabled={!!busy||brief.length<15||!config.ai_configured} onClick={()=>run('Drafting campaign terms with AI',async()=>{const result=await api('/firstmover/plan',{method:'POST',body:JSON.stringify({brief,retry:true})});const {questions,rationale,...fields}=result.proposal;setTerms(fields);setProposal(result);setApproved(false);})}><Wand2 size={16}/> Draft terms with AI</button><small>{config.ai_configured?'Requires the updated Astropods worker. Proposals cannot launch campaigns.':'AI is not configured. You can edit and launch the preset manually.'}</small>{proposal?<div className="fm-ai-result"><span className="fm-tag green">AI proposal · {proposal.model}</span><p>{proposal.proposal.rationale}</p>{proposal.proposal.questions.length>0&&<><strong>Clarify before launch</strong><ul>{proposal.proposal.questions.map((q,i)=><li key={i}>{q}</li>)}</ul></>}</div>:<div className="fm-studio-hint"><Sparkles size={18}/><p>The starting values are an editable preset, not an AI result.</p></div>}</div><form className="fm-terms-form" onSubmit={e=>{e.preventDefault();run('Launching campaign',async()=>{const row=await api('/firstmover/campaigns',{method:'POST',body:JSON.stringify({terms,mode,approved})});await refresh(row.id);setView('campaign');});}}><div className="fm-form-head"><h2>Review the terms</h2><span className="fm-tag neutral">USD ONLY · MVP</span></div><label>Campaign title<input required minLength={3} maxLength={80} value={terms.title} onChange={e=>change('title',e.target.value)}/></label><label>What participants agree to<textarea required rows={3} minLength={10} maxLength={600} value={terms.description} onChange={e=>change('description',e.target.value)}/></label><div className="fm-form-grid"><label>Group size<input required type="number" min={2} max={6} value={terms.threshold} onChange={e=>change('threshold',Number(e.target.value))}/></label><label>Price per person (USD)<input required type="number" min={1} max={20} step="0.01" value={terms.price_cents/100} onChange={e=>change('price_cents',Math.round(Number(e.target.value)*100))}/></label><label>Early places<input required type="number" min={1} max={Math.min(3,terms.threshold)} value={terms.early_slots} onChange={e=>change('early_slots',Number(e.target.value))}/></label><label>Bonus per early person (USD)<input required type="number" min={.01} max={2} step="0.01" value={terms.bonus_cents/100} onChange={e=>change('bonus_cents',Math.round(Number(e.target.value)*100))}/></label><label>Decision window (minutes)<input required type="number" min={1} max={120} value={terms.duration_minutes} onChange={e=>change('duration_minutes',Number(e.target.value))}/></label><label>Execution mode<select value={mode} onChange={e=>{setMode(e.target.value);setApproved(false);}}><option value="demo">Local simulation</option><option value="sandbox">PayPal sandbox</option></select></label></div><div className="fm-exposure"><div><span>Group target</span><strong>{money(terms.threshold*terms.price_cents)}</strong></div><div><span>Maximum bonus liability</span><strong>{money(terms.early_slots*terms.bonus_cents)}</strong></div></div><p className="fm-help">Bonuses are paid only if the group misses the target. Technical cancellation has a separate recovery flow. Fees are additional. Settlement requires organizer action.</p>{mode==='sandbox'&&!config.payout_verified&&<div className="fm-alert"><AlertTriangle size={16}/><span>Verify the sandbox payout in <button type="button" className="fm-inline" onClick={()=>setView('connections')}>Sandbox setup</button> before launching.</span></div>}<label className="fm-check"><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/><span>I reviewed the exact terms and bonus liability. This campaign uses {mode==='demo'?'simulated operations':'PayPal sandbox virtual funds'}.</span></label><button className="fm-btn primary full" disabled={!!busy||!approved||(mode==='sandbox'&&!config.payout_verified)}>Approve & launch <ArrowUpRight size={17}/></button></form></section>}
      {view==='connections'&&<section className="fm-setup"><span className="fm-eyebrow">SANDBOX SETUP</span><h1>Verify before<br/><em>you promise.</em></h1><p>The bonus mechanism needs Payouts access. Authentication or Disputes access alone does not prove that a bonus can be paid.</p><div className="fm-setup-grid"><article><h2><ShieldCheck size={22}/> Operator access</h2><p>Signed in as organizer.</p><button className="fm-text-button" onClick={onSignOut}>Sign out</button><div className="fm-setup-status"><span>PayPal server credentials</span><strong>{config.paypal_configured?'Configured':'Missing'}</strong></div><div className="fm-setup-status"><span>AI proposal service</span><strong>{config.ai_configured?'Configured':'Missing'}</strong></div><div className="fm-setup-status"><span>Sandbox Payouts receipt</span><strong>{config.payout_verified?'Verified SUCCESS':'Not yet verified'}</strong></div></article><article><h2><Wallet size={22}/> Test a participation bonus</h2><p>Send USD 0.01 in virtual funds to a sandbox buyer account. Repeating a pending test reconciles its original payout; a confirmed rejection can be retried after permissions are fixed.</p><label>Sandbox buyer email<input type="email" value={recipient} onChange={e=>{setRecipient(e.target.value);setProbeApproved(false);}} placeholder="buyer@example.test"/></label><label className="fm-check"><input type="checkbox" checked={probeApproved} onChange={e=>setProbeApproved(e.target.checked)}/><span>I approve a USD 0.01 sandbox payout to this test account.</span></label><button className="fm-btn primary full" disabled={!!busy||!recipient||!probeApproved||!config.paypal_configured} onClick={()=>run('Testing sandbox Payouts',async()=>{setProbe(await api('/firstmover/payout-check',{method:'POST',body:JSON.stringify({recipient,approved:true})}));await refresh();})}>Send / reconcile test payout <ArrowUpRight size={16}/></button>{probe&&<div className="fm-probe" role="status"><strong>{probe.status}</strong><p>{probe.message}</p><code>{probe.payout_batch_id}</code></div>}</article></div><div className="fm-setup-foot"><strong>Prototype boundaries</strong><p>One organizer merchant, USD, short campaign windows, explicit human approvals. No pooled customer wallet, escrow service, segregated bonus reserve, or guaranteed availability of funds. Before using real money, the mechanism needs PayPal policy and legal review.</p></div></section>}
      <footer className="fm-footer"><div><span className="fm-mini-mark">↗</span> FIRSTMOVER <span>An experiment in going together.</span></div><a href="https://github.com/abdel-saw/firstmover" target="_blank" rel="noreferrer">Open source <ArrowUpRight size={13}/></a><a href="/">Browse campaigns</a><span>Inspired by dominant assurance contracts.</span></footer>
    </main>
  </div>;
}

function Checkout({campaign, clientId, onClose, onComplete, onError}){
  const [name,setName]=useState('');
  return <div className="fm-modal-shade"><section className="fm-modal" role="dialog" aria-modal="true" aria-labelledby="join-title"><button className="fm-close" aria-label="Close checkout" onClick={onClose}><X/></button><span className="fm-eyebrow">PAYPAL SANDBOX COMMITMENT</span><h2 id="join-title">Be part of the group.</h2><p>{campaign.terms.title}</p><div className="fm-join-terms"><strong>{money(campaign.terms.price_cents)} temporary authorization</strong><p>Target: {campaign.terms.threshold} distinct sandbox payer accounts. First {campaign.terms.early_slots} confirmed participants may receive {money(campaign.terms.bonus_cents)} each if the target is missed by {new Date(campaign.deadline*1000).toLocaleString()}. The organizer settles the outcome. Technical recovery does not earn a bonus.</p><small>PayPal approval and server-verified authorization are separate steps. No service delivery is guaranteed by this prototype.</small></div><label>Display name<input maxLength={40} value={name} onChange={e=>setName(e.target.value)} placeholder="Your first name"/></label><PayPalButton clientId={clientId} campaign={campaign} name={name} onComplete={onComplete} onError={onError}/></section></div>;
}

function SiteHeader(){
  return <header className="fm-header">
    <a className="fm-logo" href="/" aria-label="FirstMover home"><span className="fm-mark"><i/><i/><i/></span>firstmover<span className="fm-logo-dot">.</span></a>
    <a className="fm-text-button" href="/organizer"><LockKeyhole size={14}/> Organizer sign-in</a>
  </header>;
}

function Organizer(){
  const [session,setSession]=useState(null),[password,setPassword]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const endpoint='/firstmover/organizer/session';
  useEffect(()=>{api(endpoint).then(setSession).catch(e=>setError(e.message));},[]);
  async function login(event){
    event.preventDefault();
    setBusy(true);setError('');
    try{
      const result=await api(endpoint,{method:'POST',body:JSON.stringify({password})});
      setSession(result);
    }catch(e){setError(e.message);}
    finally{setPassword('');setBusy(false);}
  }
  if(session?.signed_in)return <App onSignOut={async()=>{
    try{await api(endpoint,{method:'DELETE'});setSession({...session,signed_in:false});}
    catch(e){window.alert(e.message);}
  }}/>;
  return <div className="fm-app"><SiteHeader/><main className="fm-main">
    <form className="fm-terms-form fm-login" onSubmit={login}>
      <h1>Organizer sign-in</h1>
      {error&&<p className="fm-alert error" role="alert">{error}</p>}
      {session?.configured===false?<p>Organizer sign-in is unavailable on this deployment.</p>:<>
        <label>Password<input type="password" autoComplete="current-password" maxLength={256} required value={password} onChange={e=>setPassword(e.target.value)}/></label>
        <button className="fm-btn primary" disabled={!session||busy}>{busy?'Signing in…':'Sign in'} <ArrowRight size={16}/></button>
      </>}
      <a className="fm-text-button" href="/"><ArrowLeft size={14}/> Browse campaigns</a>
    </form>
  </main></div>;
}

function PublicCampaigns(){
  const id=location.pathname.startsWith('/campaign/')?location.pathname.split('/')[2]:new URLSearchParams(location.hash.slice(1)).get('campaign');
  const [items,setItems]=useState(null),[campaign,setCampaign]=useState(null),[config,setConfig]=useState({});
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[joining,setJoining]=useState(false),[now,setNow]=useState(Date.now());
  const detail='/firstmover/public-campaigns/'+encodeURIComponent(id||'');
  useEffect(()=>{
    if(id){
      Promise.all([api(detail),api('/firstmover/config')])
        .then(([row,settings])=>{setCampaign(row);setConfig(settings);}).catch(e=>setError(e.message));
    }else api('/firstmover/public-campaigns').then(setItems).catch(e=>setError(e.message));
  },[id,detail]);
  useEffect(()=>{
    if(!id)return;
    const timer=setInterval(()=>setNow(Date.now()),1000);
    return()=>clearInterval(timer);
  },[id]);
  const terms=campaign?.terms;
  const canJoin=campaign?.state==='open'&&campaign.deadline>now/1000&&campaign.authorized_count<terms.threshold;
  return <div className="fm-app"><SiteHeader/><main className="fm-main fm-public">
    <div className="fm-context"><span><ShieldCheck size={13}/> PayPal Sandbox · virtual funds</span></div>
    {error&&<p className="fm-alert error" role="alert">{error}</p>}
    {notice&&<p className="fm-alert" role="status">{notice}</p>}
    {!id&&<>
      <section className="fm-hero"><div><h1>Make the <em>first move.</em></h1><p>Join a group experience with a temporary PayPal hold. If the group falls short, the organizer releases the holds and pays a bonus to eligible early participants.</p></div></section>
      <h2>Open campaigns</h2>
      {items===null&&!error&&<p role="status">Loading campaigns…</p>}
      {items?.length===0&&<p>There are no open sandbox campaigns right now.</p>}
      <div className="fm-public-grid">{items?.map(row=><article className="fm-campaign-card" key={row.id}>
        <h2>{row.terms.title}</h2><p>{row.terms.description}</p>
        <div className="fm-progress-label"><strong>{money(row.terms.price_cents)} per person</strong><span>{row.authorized_count} / {row.terms.threshold} committed</span></div>
        <a className="fm-btn primary" href={'/campaign/'+row.id}>View campaign <ArrowUpRight size={16}/></a>
      </article>)}</div>
    </>}
    {id&&<a className="fm-text-button" href="/"><ArrowLeft size={14}/> All campaigns</a>}
    {id&&!campaign&&!error&&<p role="status">Loading campaign…</p>}
    {campaign&&<article className="fm-campaign-card fm-public-detail">
      <div className="fm-card-top"><span className="fm-tag neutral">{stateName[campaign.state]||campaign.state}</span><button className="fm-text-button" onClick={()=>api(detail).then(setCampaign).catch(e=>setError(e.message))}><RefreshCw size={14}/> Refresh</button></div>
      <h1>{terms.title}</h1><p>{terms.description}</p>
      <div className="fm-price"><strong>{money(terms.price_cents)}</strong><span>temporary authorization per person</span></div>
      <div className="fm-progress-label"><strong>{campaign.authorized_count} / {terms.threshold} committed</strong><span>Deadline {new Date(campaign.deadline*1000).toLocaleString()}</span></div>
      <div className="fm-progress"><i style={{width:Math.min(100,campaign.authorized_count/terms.threshold*100)+'%'}}/></div>
      <div className="fm-bonus"><Sparkles size={20}/><div><strong>{money(terms.bonus_cents)} early bonus</strong><p>The organizer pays the first {terms.early_slots} confirmed participants if the group misses its target.</p></div></div>
      {canJoin&&config.paypal_client_id&&<button className="fm-btn primary" onClick={()=>{setError('');api('/session',{method:'POST'}).then(()=>setJoining(true)).catch(e=>setError(e.message));}}>Join with PayPal <ArrowUpRight size={16}/></button>}
      {campaign.pledges.length>0&&<div className="fm-public-receipts"><h2>Your commitments</h2>
        {campaign.pledges.map(p=><div className="fm-person" key={p.id}><strong>{p.name}</strong><span className={'fm-pledge-state '+p.state}>{p.state.replaceAll('_',' ')}</span>
          {campaign.state==='open'&&['review','creating','authorizing','awaiting_approval'].includes(p.state)&&<button className="fm-text-button" onClick={()=>api('/firstmover/pledges/'+p.id+'/reconcile',{method:'POST'}).then(setCampaign).catch(e=>setError(e.message))}>Reconcile</button>}
        </div>)}
      </div>}
    </article>}
  </main>{joining&&<Checkout campaign={campaign} clientId={config.paypal_client_id} onClose={()=>setJoining(false)} onComplete={row=>{
    setCampaign(row);setJoining(false);setNotice('PayPal authorization verified. Your commitment is recorded.');
  }} onError={message=>{setError(message);setJoining(false);api(detail).then(setCampaign).catch(()=>{});}}/>}</div>;
}

createRoot(document.getElementById('root')).render(location.pathname.replace(/\/$/,'')==='/organizer'?<Organizer/>:<PublicCampaigns/>);
