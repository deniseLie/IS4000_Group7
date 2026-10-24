import {useEffect,useRef} from 'react';
import Icon from './Icon';

export default function Panel({label,title,closeLabel,onClose,children,eyebrow='DemoBank assistant',subtitle,footer,size}) {
  const dialog=useRef(null);
  useEffect(()=>{
    const node=dialog.current;const trigger=document.activeElement;
    node.showModal();
    return ()=>{node.close();if(trigger?.isConnected)trigger.focus();};
  },[]);
  function containFocus(e) {
    if(e.key!=='Tab')return;
    const controls=[...dialog.current.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled)')];
    const first=controls[0],last=controls.at(-1);
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
  }
  return <dialog ref={dialog} className={`panel-dialog ${size||''}`} aria-label={label} onKeyDown={containFocus} onCancel={e=>{e.preventDefault();onClose();}}>
    <section className="support-panel" aria-label={label}>
      <div className="panel-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2>{subtitle&&<p className="panel-subtitle">{subtitle}</p>}</div><button className="icon-button" aria-label={closeLabel} onClick={onClose}><Icon name="close"/></button></div>
      {children}
      {footer&&<div className="panel-footer">{footer}</div>}
    </section>
  </dialog>;
}
