import { useState, useEffect } from "react";
import { getVoices } from "../api";

export default function Voices() {
  const [voices, setVoices] = useState([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load(telegramId) {
    setLoading(true);
    setError("");
    try {
      setVoices(await getVoices(telegramId || null));
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
      ) : voices.length === 0 ? (
        <p className="empty">No cloned voices yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Telegram ID</th>
              <th>ElevenLabs Voice ID</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {voices.map((v, i) => (
              <tr key={i}>
                <td>{v.name}</td>
                <td>{v.telegram_id}</td>
                <td className="text-cell">{v.elevenlabs_voice_id}</td>
                <td className="nowrap">{formatDate(v.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
