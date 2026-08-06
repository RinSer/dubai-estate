import { lazy, Suspense, useEffect, useState } from "react";
import { CopilotDock } from "./copilot/CopilotDock";
import { login, restoreSession } from "./core/client";
import { attachHistoryListener, useStore } from "./core/store";
import { VIEWS, type ViewName } from "./core/viewstate";
import { ErrorNote, Field, Spinner } from "./ui/components";

// Split per view. MapLibre and Observable Plot are the two heaviest things in
// the bundle, and someone who only opens a table should not pay to download a
// WebGL mapping engine. Each view arrives on first use instead.
const ListingView = lazy(() =>
  import("./views/listing/ListingView").then((m) => ({ default: m.ListingView })),
);
const DashboardView = lazy(() =>
  import("./views/dashboard/DashboardView").then((m) => ({ default: m.DashboardView })),
);
const MapView = lazy(() =>
  import("./views/map/MapView").then((m) => ({ default: m.MapView })),
);

const VIEW_LABELS: Record<ViewName, string> = {
  listing: "Listing",
  dashboard: "Dashboard",
  map: "Map",
};

function LoginForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-box">
      <form className="panel" style={{ width: 320 }} onSubmit={submit}>
        <div className="panel-head">
          <span className="panel-title">Sign in</span>
        </div>
        <div className="panel-body stack">
          <Field label="Username">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </Field>
          {error ? <ErrorNote error={error} /> : null}
          <button className="btn primary" type="submit" disabled={busy || !username || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function App() {
  const view = useStore((s) => s.state.view);
  const dispatch = useStore((s) => s.dispatch);
  const undo = useStore((s) => s.undo);
  const redo = useStore((s) => s.redo);
  const past = useStore((s) => s.past.length);
  const future = useStore((s) => s.future.length);

  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => attachHistoryListener(), []);

  useEffect(() => {
    // The access token died with the last tab; trade the stored refresh token
    // for a new one before deciding whether to show the login form.
    restoreSession()
      .then(setAuthed)
      .catch(() => setAuthed(false));
  }, []);

  if (authed === null) {
    return (
      <div className="center-box">
        <Spinner label="Restoring session…" />
      </div>
    );
  }

  if (!authed) return <LoginForm onDone={() => setAuthed(true)} />;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          Dubai estate <span>analytics</span>
        </span>
        <nav className="tabs">
          {VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              className="tab"
              aria-current={v === view ? "page" : undefined}
              onClick={() => dispatch({ type: "setView", view: v })}
            >
              {VIEW_LABELS[v]}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <button
          className="btn subtle sm"
          onClick={undo}
          disabled={!past}
          title="Undo — works the same for your changes and the copilot's"
        >
          Undo
        </button>
        <button className="btn subtle sm" onClick={redo} disabled={!future}>
          Redo
        </button>
      </header>

      <main className={`viewport${view === "map" ? " flush" : ""}`}>
        <Suspense
          fallback={
            <div className="center-box">
              <Spinner label="Loading view…" />
            </div>
          }
        >
          {view === "listing" ? <ListingView /> : null}
          {view === "dashboard" ? <DashboardView /> : null}
          {view === "map" ? <MapView /> : null}
        </Suspense>
      </main>

      <CopilotDock />
    </div>
  );
}
