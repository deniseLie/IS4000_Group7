
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
          <span className="brand-name">DemoBank<span className="brand-dot">.</span></span>
          <div><span className="small-rule"/><h2>A clearer view.<br/><span>A helping hand.</span></h2><p>Sign in to explore your synthetic demo records, with guidance from your banking assistant.</p></div>
          <small>IS4000 · Coursework demo · Not a real bank</small>
        </section>

        <section className="login-panel">
          <form onSubmit={handleLogin}>
            <div className="login-heading">
              <span className="eyebrow">Demo account access</span>
              <h1>Welcome back</h1>
              <p>Use your demo account to view synthetic records. General banking help is available without signing in.</p>
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
            {forgot && <p className="rm-small" data-testid="forgot-password-help">To reset your password, use “Forgot password” in the official DemoBank app or website, or call client services. DemoBank staff and this assistant will never ask for your password, PIN or one-time code. In this demo, use the persona password your team was given.</p>}

            <button data-testid="submit-login" className="login-submit primary-button" type="submit" disabled={loading}>
              {loading ? "Signing in..." : <>Sign in<Icon name="arrow"/></>}
            </button>
          </form>
        </section>
      </div>
      <footer className="login-footer">Synthetic data only</footer>
    </div>
  );
}
