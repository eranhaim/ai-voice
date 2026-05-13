import { useState, useEffect } from "react";
import { getSystemVoices, addSystemVoice, deleteSystemVoice, getVoices } from "../api";

export default function Voices() {
  const [systemVoices, setSystemVoices] = useState([]);
  const [customVoices, setCustomVoices] = useState([]);
  const [voiceId, setVoiceId] = useState("");
  const [voiceName, setVoiceName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [sv, cv] = await Promise.all([getSystemVoices(), getVoices()]);
      setSystemVoices(sv);
      setCustomVoices(cv);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleAdd(e) {
    e.preventDefault();
    setError("");
    if (!voiceId || !voiceName) return;
    try {
      await addSystemVoice(voiceName, voiceId);
      setVoiceId("");
      setVoiceName("");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDelete(id, name) {
    if (!confirm(`Delete system voice "${name}"?`)) return;
    try {
      await deleteSystemVoice(id);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  function formatDate(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString();
  }

  if (loading) return <p className="loading">Loading...</p>;

  return (
    <div>
      <h3 style={{ marginBottom: "1rem", color: "#f0f3f6" }}>System Voices</h3>

      <form onSubmit={handleAdd} className="add-form">
        <input
          type="text"
          placeholder="ElevenLabs Voice ID"
          value={voiceId}
          onChange={(e) => setVoiceId(e.target.value)}
          required
        />
        <input
          type="text"
          placeholder="Voice Name"
          value={voiceName}
          onChange={(e) => setVoiceName(e.target.value)}
          required
        />
        <button type="submit">Add Voice</button>
      </form>

      {error && <p className="error">{error}</p>}

      {systemVoices.length === 0 ? (
        <p className="empty">No system voices.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>ElevenLabs Voice ID</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {systemVoices.map((v) => (
              <tr key={v.id}>
                <td>{v.name}</td>
                <td className="text-cell">{v.elevenlabs_voice_id}</td>
                <td>
                  <button
                    className="btn-delete"
                    onClick={() => handleDelete(v.id, v.name)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ margin: "2rem 0 1rem", color: "#f0f3f6" }}>Custom Voices (user-created)</h3>

      {customVoices.length === 0 ? (
        <p className="empty">No custom voices yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Tier</th>
              <th>Status</th>
              <th>Telegram ID</th>
              <th>ElevenLabs Voice ID</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {customVoices.map((v, i) => (
              <tr key={i}>
                <td>{v.name}</td>
                <td><KindBadge kind={v.kind} /></td>
                <td><StatusBadge status={v.training_status} /></td>
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

function KindBadge({ kind }) {
  const label = kind === "pvc" ? "PVC" : "IVC";
  const cls = kind === "pvc" ? "badge badge-pvc" : "badge badge-ivc";
  return <span className={cls}>{label}</span>;
}

function StatusBadge({ status }) {
  const map = {
    ready: { label: "Ready", cls: "badge badge-ready" },
    uploading: { label: "Uploading", cls: "badge badge-progress" },
    verifying: { label: "Verifying", cls: "badge badge-progress" },
    training: { label: "Training", cls: "badge badge-progress" },
    failed: { label: "Failed", cls: "badge badge-failed" },
  };
  const entry = map[status] || { label: status || "ready", cls: "badge badge-ready" };
  return <span className={entry.cls}>{entry.label}</span>;
}
