import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';

export default function Panel({label,title,closeLabel,onClose,children,eyebrow='DemoBank assistant',subtitle,footer,size}) {
  const dialog=useRef(null),[closing,setClosing]=useState(false);
  // Close plays the exit motion (slide out for drawers, fade for modals) before the parent unmounts the panel.
  function close() {
    if(closing)return;
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){onClose();return;}
    // The page becomes usable at once: the panel drops to non-modal while its exit motion plays.
    const node=dialog.current;node.close();node.show();
    setClosing(true);setTimeout(onClose,size==='drawer'?220:160);
  }
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
  return <dialog ref={dialog} className={`panel-dialog ${size||''} ${closing?'closing':''}`} aria-label={label} onKeyDown={containFocus} onCancel={e=>{e.preventDefault();close();}}>
    <section className="support-panel" aria-label={label}>
      <div className="panel-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2>{subtitle&&<p className="panel-subtitle">{subtitle}</p>}</div><button className="icon-button" aria-label={closeLabel} onClick={close}><Icon name="close"/></button></div>
      {children}
      {footer&&<div className="panel-footer">{footer}</div>}
    </section>
  </dialog>;
}
