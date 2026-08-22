'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '../lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);

    try {
      await api.auth.authenticate(mode, email, password);
      router.push('/');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Authentication failed.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <p className="eyebrow">Backlog Beater</p>
        <h1>{mode === 'login' ? 'Welcome back' : 'Create an account'}</h1>
        <p className="muted">Turn an untouched game library into a short list you can finish.</p>

        <form className="auth-form" onSubmit={submit}>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
          <label htmlFor="password">Password</label>
          <input id="password" type="password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required />
          {error && <p className="notice error" role="alert">{error}</p>}
          <button className="button" disabled={loading} type="submit">{loading ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Create account'}</button>
        </form>

        <button className="text-button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null); }}>
          {mode === 'login' ? 'Need an account? Register' : 'Already registered? Log in'}
        </button>
      </section>
    </main>
  );
}
