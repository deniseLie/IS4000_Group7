import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';

// Menu items keep their original test ids; choosing one returns focus to the trigger so dialogs restore it on close.
export default function AccountMenu({label,items,placement='down',className=''}) {
  const [open,setOpen]=useState(false);
  const root=useRef(null);
  const trigger=useRef(null);
  useEffect(()=>{
    if(!open)return;
    visible()[0]?.focus();
    const close=event=>{if(!root.current?.contains(event.target))setOpen(false);};
    document.addEventListener('pointerdown',close);return ()=>document.removeEventListener('pointerdown',close);
  },[open]);
  function visible() {return [...root.current.querySelectorAll('[role="menuitem"]')].filter(entry=>entry.offsetParent!==null);}
  function choose(item) {setOpen(false);trigger.current?.focus();item.onSelect();}
  function move(event) {
    const entries=visible(),index=entries.indexOf(document.activeElement);
    if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();setOpen(false);trigger.current?.focus();}
    if(event.key==='ArrowDown'&&entries.length){event.preventDefault();entries[(index+1)%entries.length].focus();}
    if(event.key==='ArrowUp'&&entries.length){event.preventDefault();entries[(index-1+entries.length)%entries.length].focus();}
    if(event.key==='Tab'&&open)setOpen(false);
  }
  return <div className={`account-menu ${placement==="up"?"opens-up":""} ${className}`} ref={root} onKeyDown={move}>
    <button ref={trigger} className="account-trigger" data-testid="open-account-menu" aria-haspopup="menu" aria-label="Account" aria-expanded={open} onClick={()=>setOpen(!open)}><span>{label}</span><Icon name="chevron"/></button>
    {open&&<div className="account-menu-list" role="menu" aria-label="Account">{items.map(item=><button key={item.testid} type="button" role="menuitem" data-testid={item.testid} aria-label={item.ariaLabel} className={[item.danger&&'danger',item.mobile&&'mobile-only'].filter(Boolean).join(' ')} onClick={()=>choose(item)}><Icon name={item.icon}/>{item.label}</button>)}</div>}
  </div>;
}
