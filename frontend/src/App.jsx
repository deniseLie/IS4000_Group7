import { useEffect, useState } from "react";
import { supabase } from "./lib/supabase";
import Login from "./Login";
import Chat from "./Chat";

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showLogin, setShowLogin] = useState(false);

  useEffect(() => {
    if (!supabase) { setLoading(false); return; }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    }).catch(() => setLoading(false));

    const { data: { subscription } } =
      supabase.auth.onAuthStateChange((_event, session) => {
        setSession(session);
        setLoading(false);
        setShowLogin(false);
      });

    return () => subscription.unsubscribe();
  }, []);

  if (loading) return <div>Loading...</div>;

  if (showLogin && !session) return <><button className="bank-chat-logout" onClick={() => setShowLogin(false)}>Back to general help</button><Login /></>;
  return <Chat key={session?.user.id || 'guest'} session={session} onSignIn={() => setShowLogin(true)} />;
}

