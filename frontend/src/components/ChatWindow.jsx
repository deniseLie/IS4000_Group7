import { useEffect, useRef, useState } from "react";
import { streamChat } from "../api/chat.js";

export default function ChatWindow() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;

    const history = [...messages, { role: "user", content: text }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);

    try {
      await streamChat(history, (token) =>
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, content: last.content + token };
          return next;
        })
      );
    } catch (err) {
      setMessages((prev) => [...prev.slice(0, -1), { role: "assistant", content: `Error: ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <div className="messages">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>{m.content}</div>
        ))}
        <div ref={endRef} />
      </div>
      <form onSubmit={send} className="composer">
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Type a message…" />
        <button disabled={busy}>Send</button>
      </form>
    </div>
  );
}
