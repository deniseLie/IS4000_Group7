
import { useEffect, useRef, useState } from "react";
import { supabase } from "./lib/supabase";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:5001";

export default function Chat({ session }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const requestRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => () => requestRef.current?.abort(), []);

  async function sendMessage(e) {
    e.preventDefault();
    const question = input.trim();
    if (!question || loading) return;

    const history = [
      ...messages.filter(m => !m.error).map(({ role, content }) => ({ role, content })),
      { role: "user", content: question }
    ];

    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setLoading(true);

    const controller = new AbortController();
    requestRef.current = controller;

    try {
      const { data: { session: currentSession } } = await supabase.auth.getSession();
      if (!currentSession) throw new Error("Session expired. Please sign in again.");

      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${currentSession.access_token}`
        },
        body: JSON.stringify({ messages: history }),
        signal: controller.signal
      });

      if (!response.ok) {
        throw new Error(
          response.status === 401
            ? "Session expired. Please sign in again."
            : "Unable to contact the banking assistant."
        );
      }

      if (!response.body) throw new Error("No response received.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let answer = "";
      let completed = false;

      const handleEvent = (eventText) => {
        const lines = eventText.split("\n");
        const type = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
        const data = lines
          .filter(line => line.startsWith("data:"))
          .map(line => line.slice(5).replace(/^ /, ""))
          .join("\n");

        if (type === "error") throw new Error(data || "AI service unavailable.");
        if (type === "done") {
          completed = true;
          return;
        }

        answer += data;
        setMessages([...history, { role: "assistant", content: answer }]);
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        events.forEach(handleEvent);
      }

      if (!completed) throw new Error("The response was interrupted.");
    } catch (err) {
      if (err.name !== "AbortError") {
        setMessages([
          ...history,
          { role: "assistant", content: err.message, error: true }
        ]);
      }
    } finally {
      requestRef.current = null;
      setLoading(false);
    }
  }

  async function signOut() {
    requestRef.current?.abort();
    await supabase.auth.signOut();
  }

  return (
    <div className="bank-chat-page">
      <div className="bank-chat-layout">
        <header className="bank-chat-header">
          <div className="bank-chat-brand">
            <div className="bank-chat-icon">◆</div>
            <div>
              <h1>DemoBank</h1>
              <span>Personal Banking Assistant</span>
            </div>
          </div>
          <button className="bank-chat-logout" onClick={signOut}>
            Sign out
          </button>
        </header>

        <section className="bank-chat-window">
          <div className="bank-chat-messages">
            {messages.length === 0 && (
              <div className="bank-chat-welcome">
                <div className="bank-chat-welcome-icon">✦</div>
                <h2>How can I help you today?</h2>
                <p>Ask me about your accounts, credit cards, loans or transactions.</p>
                <div className="bank-chat-suggestions">
                  {[
                    "What's my account balance?",
                    "When is my credit card payment due?",
                    "Show my recent transactions"
                  ].map(question => (
                    <button key={question} onClick={() => setInput(question)}>
                      {question} <span>↗</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`bank-chat-message ${msg.role}`}>
                {msg.role === "assistant" && (
                  <div className="bank-chat-avatar">✦</div>
                )}
                <div className={`bank-chat-bubble ${msg.error ? "error" : ""}`}>
                  {msg.content || (loading && i === messages.length - 1 ? (
                    <span className="bank-chat-typing">Thinking...</span>
                  ) : "")}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>

          <form className="bank-chat-composer" onSubmit={sendMessage}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Ask a banking question..."
              aria-label="Your message"
              disabled={loading}
              maxLength={4000}
            />
            <button type="submit" disabled={!input.trim() || loading}>
              {loading ? "..." : "Send →"}
            </button>
          </form>
          <p className="bank-chat-disclaimer">
            Demo banking information only. AI responses may contain errors.
          </p>
        </section>
        <footer className="bank-chat-footer">
          Signed in as {session?.user?.email}
        </footer>
      </div>
    </div>
  );
}
