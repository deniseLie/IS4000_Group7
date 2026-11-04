import {Fragment,useState} from 'react';
import Icon from '../Icon';
import {formatDateTime} from '../../lib/format';
import {loadTrace,loadTraces,seconds} from '../../lib/admin';
import {Failed,Loading,Page,Status,useLoad} from './shared';

// The latest client and RM requests as LangSmith recorded them, step by step.
export default function TracesPage() {
  const [list,refresh]=useLoad(loadTraces,'all');
  const [open,setOpen]=useState(null);
  const [spans]=useLoad((auth,signal)=>loadTrace(open,auth,signal),open);
  return <Page id="traces" title="Traces" intro="The latest assistant requests, each with its steps, time and tokens. Text was redacted before upload."
    actions={<button className="bank-chat-logout" onClick={refresh} disabled={list.busy}><Icon name="refresh"/>Refresh</button>}>
    <Failed error={list.error}/>
    {list.busy&&!list.data&&<Loading label="Loading traces"/>}
    {list.data&&!list.data.length&&<p className="admin-empty">No client or RM requests have been traced yet.</p>}
    {list.data?.length>0&&<div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>When</th><th>Mode</th><th>Status</th><th>Time</th><th>Tokens</th></tr></thead>
      <tbody>{list.data.map(item=><Fragment key={item.run_id}>
        <tr className="admin-row" data-testid={`admin-trace-${item.run_id}`} onClick={()=>setOpen(open===item.run_id?null:item.run_id)} aria-expanded={open===item.run_id}>
          <td><button className="link-button">{formatDateTime(item.started_at)}</button></td><td>{item.mode||'—'}</td>
          <td><Status value={item.error?'error':item.status||'—'}/></td><td>{seconds(item.latency_s)}</td><td>{item.tokens??'—'}</td></tr>
        {open===item.run_id&&<tr className="admin-detail"><td colSpan={5}>
          {spans.busy&&<p className="loading">Loading steps…</p>}
          <Failed error={spans.error}/>
          {spans.data&&<ol className="admin-spans">{spans.data.map((span,index)=><li key={index} style={{paddingLeft:`${span.depth*14}px`}}>
            <span className="admin-span-type">{span.run_type}</span> {span.name}{span.agent?` · ${span.agent}`:''}{span.tool&&span.tool!==span.name?` · ${span.tool}`:''}
            <span className="admin-muted"> {seconds(span.latency_s)}{span.tokens?` · ${span.tokens} tokens`:''}</span>
            {span.error&&<span className="form-error"> {span.error}</span>}</li>)}</ol>}
        </td></tr>}
      </Fragment>)}</tbody></table></div>}
  </Page>;
}
