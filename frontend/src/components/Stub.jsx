// Marks Figma content that has no recorded data or feature behind it yet.
export default function Stub({title,inline,children}) {
  if(inline)return <span className="stub-inline" data-stub="true">{children}<span className="stub-badge">Design stub</span></span>;
  return <div className="stub-block" data-stub="true">
    <div className="stub-heading">{title&&<strong>{title}</strong>}<span className="stub-badge">Design stub · not connected to data</span></div>
    {children}
  </div>;
}
