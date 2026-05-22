import { useState, useEffect } from "react";
import { getRuns, getUsers, resendAudio } from "../api";

export default function Runs() {
  const [runs, setRuns] = useState([]);
  const [userMap, setUserMap] = useState({});
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(null);

  async function loadUsers() {
    try {
      const users = await getUsers();
      const map = {};
      for (const u of users) {
        map[u.telegram_id] = u.name || String(u.telegram_id);
      }
      setUserMap(map);
    } catch {}
  }

  async function loadRuns(telegramId) {
    setLoading(true);
    setError("");
    try {
      setRuns(await getRuns(telegramId || null));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadUsers();
    loadRuns();
  }, []);

  function handleFilter(e) {
    e.preventDefault();
    loadRuns(filter);
  }

  function formatDate(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString();
  }

  function userName(telegramId) {
    return userMap[telegramId] || String(telegramId);
  }

  async function handleResend(runId) {
    setSending(runId);
    try {
      await resendAudio(runId);
      alert("Sent to your Telegram!");
    } catch (e) {
      alert("Failed: " + e.message);
    } finally {
      setSending(null);
    }
  }

  return (
    <div>
      <form onSubmit={handleFilter} className="filter-form">
        <input
          type="number"
          placeholder="Filter by Telegram ID"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button type="submit">Filter</button>
        {filter && (
          <button
            type="button"
            onClick={() => {
              setFilter("");
              loadRuns();
            }}
          >
            Clear
          </button>
        )}
      </form>

      {error && <p className="error">{error}</p>}

      {loading ? (
        <p className="loading">Loading...</p>
      ) : runs.length === 0 ? (
        <p className="empty">No runs yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>User</th>
              <th>Type</th>
              <th>Model</th>
              <th>Voice</th>
              <th>Text</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r, i) => (
              <tr key={i}>
                <td className="nowrap">{formatDate(r.created_at)}</td>
                <td>{userName(r.telegram_id)}</td>
                <td>
                  <span className={`badge badge-${r.type}`}>
                    {r.type.toUpperCase()}
                  </span>
                </td>
                <td className="nowrap model-cell">{r.model || "—"}</td>
                <td>{r.voice_name || "—"}</td>
                <td className="text-cell">{r.text}</td>
                <td>
                  {r.has_audio && (
                    <button
                      onClick={() => handleResend(r.run_id)}
                      disabled={sending === r.run_id}
                      style={{ fontSize: "0.8rem", padding: "0.3rem 0.6rem" }}
                    >
                      {sending === r.run_id ? "..." : "Send to TG"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
