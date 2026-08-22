'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, ApiError } from './lib/api';

interface BacklogItem {
  id: number;
  title: string;
  status: 'not_started' | 'in_progress' | 'completed';
  hours_played: number;
  added_at: string;
  cover_url: string | null;
  genres?: string[];
  completion_source: string | null;
  main_story_hours: number | null;
  main_plus_extras_hours: number | null;
  completionist_hours: number | null;
  completion_match_confidence: number | null;
}

const statusLabels: Record<BacklogItem['status'], string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  completed: 'Completed',
};

export default function Home() {
  const router = useRouter();
  const [items, setItems] = useState<BacklogItem[]>([]);
  const [steamId, setSteamId] = useState('');
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadBacklog = useCallback(async () => {
    try {
      setError(null);
      setItems(await api.backlog.list<BacklogItem[]>());
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        api.auth.clearSession();
        router.replace('/login');
        return;
      }
      setError(requestError instanceof Error ? requestError.message : 'Could not load your backlog.');
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!api.auth.hasSession()) {
      router.replace('/login');
      return;
    }
    void loadBacklog();
  }, [loadBacklog, router]);

  async function importLibrary(event: React.FormEvent) {
    event.preventDefault();
    setImporting(true);
    setError(null);
    setMessage(null);

    try {
      const result = await api.integrations.importSteam<{ game_count: number; completion_matches: number }>(steamId.trim());
      setMessage(`Imported ${result.game_count} games from Steam. Found completion times for ${result.completion_matches}.`);
      await loadBacklog();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Steam import failed.');
    } finally {
      setImporting(false);
    }
  }

  function logout() {
    api.auth.clearSession();
    router.push('/login');
  }

  if (loading) {
    return <main className="shell"><p className="muted">Loading your backlog…</p></main>;
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Backlog Beater</p>
          <h1>Your games</h1>
        </div>
        <button className="button secondary" onClick={logout}>Log out</button>
      </header>

      <section className="panel import-panel">
        <div>
          <h2>Import from Steam</h2>
          <p className="muted">Steam provides your library and playtime. Completion estimates and artwork come from HowLongToBeat.</p>
        </div>
        <form className="import-form" onSubmit={importLibrary}>
          <label className="sr-only" htmlFor="steam-id">Steam ID</label>
          <input id="steam-id" value={steamId} onChange={(event) => setSteamId(event.target.value)} placeholder="Steam ID" required />
          <button className="button" disabled={importing} type="submit">{importing ? 'Importing…' : 'Import library'}</button>
        </form>
      </section>

      {error && <p className="notice error" role="alert">{error}</p>}
      {message && <p className="notice success">{message}</p>}

      <section className="section-heading">
        <h2>Backlog</h2>
        <span className="count">{items.length} {items.length === 1 ? 'game' : 'games'}</span>
      </section>

      {items.length === 0 ? (
        <section className="empty-state">
          <h2>No games yet</h2>
          <p className="muted">Import a public Steam library to get started.</p>
        </section>
      ) : (
        <div className="game-list">
          {items.map((item) => (
            <article className="game-row" key={item.id}>
              <div className="cover" aria-hidden="true">
                {/* Metadata artwork is served from provider-controlled domains. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {item.cover_url ? <img src={item.cover_url} alt="" /> : <span>BG</span>}
              </div>
              <div className="game-copy">
                <div className="game-title-row">
                  <h3>{item.title}</h3>
                  <span className={`status ${item.status}`}>{statusLabels[item.status]}</span>
                </div>
                <p className="muted">{item.genres?.length ? item.genres.slice(0, 3).join(' · ') : 'No genre metadata'}</p>
                <div className="facts">
                  <span><strong>{item.hours_played.toFixed(1)}</strong> hours played</span>
                  {item.main_story_hours != null && <span><strong>{item.main_story_hours.toFixed(1)}</strong> main story</span>}
                  {item.main_plus_extras_hours != null && <span><strong>{item.main_plus_extras_hours.toFixed(1)}</strong> main + extras</span>}
                  {item.completionist_hours != null && <span><strong>{item.completionist_hours.toFixed(1)}</strong> completionist</span>}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
