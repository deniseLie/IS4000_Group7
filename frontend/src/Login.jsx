
import { useState } from "react";
import { supabase } from "./lib/supabase";
import Icon from "./components/Icon";

export default function Login({onBack}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [forgot, setForgot] = useState(false);

  async function handleLogin(e) {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      if (!supabase) throw new Error('Sign-in is not configured.');
      // A new sign-in always opens a fresh assistant page; saved chats stay under Recent chats (a refresh still reopens).
      try { sessionStorage.setItem('demobank.fresh-sign-in', '1'); } catch { /* storage blocked: the last chat reopens */ }
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      // CR1: one message for every sign-in failure, so it never reveals whether an account exists.
      if (error) setError("Incorrect email or password. Please try again.");
    } catch {
      setError("Unable to sign in. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <button className="bank-chat-logout login-back" onClick={onBack}>← Back to general help</button>
      <div className="login-container">
        <section className="login-brand">
          <span className="brand-name">UBS Connect<span className="brand-dot">.</span></span>
          <div><span className="small-rule"/><h2>A clearer view.<br/><span>A helping hand.</span></h2><p>Sign in to see your accounts, with guidance from your banking assistant.</p></div>
        </section>

        <section className="login-panel">
          <form onSubmit={handleLogin}>
            <div className="login-heading">
              <span className="eyebrow">Sign in</span>
              <h1>Welcome back</h1>
              <p>Sign in to see your accounts, meetings and requests. General banking help is available without signing in.</p>
            </div>

            <label htmlFor="email">Email address</label>
            <input
              id="email"
              type="email"
              placeholder="Enter your email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              autoComplete="username"
              required
            />

            <label htmlFor="password">Password</label>
            <div className="password-field">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                placeholder="Enter your password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button type="button" onClick={() => setShowPassword(!showPassword)}>
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>

            {error && <p className="login-error" role="alert" data-testid="login-error">{error}</p>}
            {/* CR1: password recovery guidance; the assistant never asks for a password. */}
            <button type="button" className="source-link" data-testid="forgot-password" aria-expanded={forgot} onClick={() => setForgot(!forgot)}>Forgot password?</button>
            {forgot && <p className="rm-small" data-testid="forgot-password-help">To reset your password, use “Forgot password” in the official UBS Connect app or website, or call client services. UBS Connect staff and this assistant will never ask for your password, PIN or one-time code.</p>}

            <button data-testid="submit-login" className="login-submit primary-button" type="submit" disabled={loading}>
              {loading ? "Signing in..." : <>Sign in<Icon name="arrow"/></>}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
