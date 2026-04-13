import { useState, useEffect } from "react";
import { getRuns } from "../api";

export default function Runs() {
  const [runs, setRuns] = useState([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load(telegramId) {
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

  useEffect(() => { load(); }, []);

  function handleFilter(e) {
    e.preventDefault();
    load(filter);
  }

  function formatDate(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString();
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
              load();
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
              <th>Telegram ID</th>
              <th>Type</th>
              <th>Text</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r, i) => (
              <tr key={i}>
                <td className="nowrap">{formatDate(r.created_at)}</td>
                <td>{r.telegram_id}</td>
                <td>
                  <span className={`badge badge-${r.type}`}>
                    {r.type.toUpperCase()}
                  </span>
                </td>
                <td className="text-cell">{r.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
