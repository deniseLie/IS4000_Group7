
import { useState } from "react";
import { supabase } from "./lib/supabase";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleLogin(e) {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) setError(error.message);
    } catch {
      setError("Unable to sign in. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-container">
        <section className="login-brand">
          <div className="brand-logo">
            <span className="brand-symbol">◆</span> DemoBank
          </div>

          <div className="brand-visual" aria-hidden="true">
            <div className="visual-glow"></div>
            <div className="visual-card visual-card-back"></div>
            <div className="visual-card visual-card-front">
              <div className="card-top">
                <span className="card-chip"></span>
                <span className="card-waves">)))</span>
              </div>
              <div className="card-bottom">
                <span>•••• &nbsp; •••• &nbsp; •••• &nbsp; 4829</span>
                <strong>DemoBank</strong>
              </div>
            </div>
            <div className="visual-bubble visual-bubble-one">✓</div>
            <div className="visual-bubble visual-bubble-two">✦</div>
          </div>

          <div className="brand-bottom">
            <span className="brand-line"></span>
            <p>Your banking assistant</p>
          </div>
        </section>

        <section className="login-panel">
          <form onSubmit={handleLogin}>
            <div className="login-heading">
              <span className="login-eyebrow">CUSTOMER PORTAL</span>
              <h1>Welcome back</h1>
              <p>Sign in to continue to your banking assistant.</p>
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

            {error && <p className="login-error" role="alert">{error}</p>}

            <button className="login-submit" type="submit" disabled={loading}>
              {loading ? "Signing in..." : "Sign In →"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
