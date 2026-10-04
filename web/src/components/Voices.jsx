import { useState, useEffect, useRef } from "react";
import { getSystemVoices, addSystemVoice, deleteSystemVoice, getVoices, cloneVoice, confirmVoiceConsent } from "../api";

export default function Voices() {
  const [systemVoices, setSystemVoices] = useState([]);
  const [customVoices, setCustomVoices] = useState([]);
  const [voiceId, setVoiceId] = useState("");
  const [voiceName, setVoiceName] = useState("");
  const [systemConsentReference, setSystemConsentReference] = useState("");
  const [systemConsentConfirmed, setSystemConsentConfirmed] = useState(false);
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
    if (!voiceId || !voiceName || !systemConsentConfirmed || !systemConsentReference) return;
    try {
      await addSystemVoice(voiceName, voiceId, systemConsentReference);
      setVoiceId("");
      setVoiceName("");
      setSystemConsentReference("");
      setSystemConsentConfirmed(false);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  const [cloneName, setCloneName] = useState("");
  const [cloneFiles, setCloneFiles] = useState(null);
  const [cloneConsentReference, setCloneConsentReference] = useState("");
  const [cloneConsentConfirmed, setCloneConsentConfirmed] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [cloneStatus, setCloneStatus] = useState("");
  const fileInputRef = useRef(null);

  async function handleClone(e) {
    e.preventDefault();
    if (!cloneName || !cloneFiles || cloneFiles.length === 0 || !cloneConsentConfirmed || !cloneConsentReference) return;
    setCloning(true);
    setCloneStatus("Uploading & cloning... this may take a minute");
    setError("");
    try {
      const result = await cloneVoice(cloneName, cloneFiles, cloneConsentReference);
      setCloneStatus(`Voice "${result.name}" created (${result.elevenlabs_voice_id})`);
      setCloneName("");
      setCloneFiles(null);
      setCloneConsentReference("");
      setCloneConsentConfirmed(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
      load();
    } catch (e) {
      setError(e.message);
      setCloneStatus("");
    } finally {
      setCloning(false);
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

  async function handleConfirmConsent(voiceId, voiceName) {
    const reference = window.prompt(`Enter the consent record reference for "${voiceName}" (agreement, ticket, or recorded approval ID):`);
    if (!reference?.trim()) return;
    try {
      await confirmVoiceConsent(voiceId, reference.trim());
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
        <input
          type="text"
          placeholder="Consent record reference"
          value={systemConsentReference}
          onChange={(e) => setSystemConsentReference(e.target.value)}
          required
        />
        <label className="consent-field">
          <input
            type="checkbox"
            checked={systemConsentConfirmed}
            onChange={(e) => setSystemConsentConfirmed(e.target.checked)}
            required
          />
          I confirm this creator authorized use of this voice.
        </label>
        <button type="submit">Add Voice</button>
      </form>

      <h3 style={{ margin: "2rem 0 1rem", color: "#f0f3f6" }}>Clone Voice from Audio</h3>
      <form onSubmit={handleClone} className="add-form clone-form">
        <input
          type="text"
          placeholder="Voice Name"
          value={cloneName}
          onChange={(e) => setCloneName(e.target.value)}
          required
          disabled={cloning}
        />
        <input
          ref={fileInputRef}
          type="file"
          accept="audio/*,.mp3,.m4a,.wav,.ogg,.opus,.aac,.flac"
          multiple
          onChange={(e) => setCloneFiles(e.target.files)}
          required
          disabled={cloning}
        />
        <input
          type="text"
          placeholder="Consent record reference"
          value={cloneConsentReference}
          onChange={(e) => setCloneConsentReference(e.target.value)}
          required
          disabled={cloning}
        />
        <label className="consent-field">
          <input
            type="checkbox"
            checked={cloneConsentConfirmed}
            onChange={(e) => setCloneConsentConfirmed(e.target.checked)}
            required
            disabled={cloning}
          />
          I confirm this creator authorized voice cloning.
        </label>
        <button type="submit" disabled={cloning || !cloneName || !cloneFiles?.length || !cloneConsentConfirmed || !cloneConsentReference}>
          {cloning ? "Cloning..." : "Clone Voice"}
        </button>
      </form>
      {cloneStatus && <p className="clone-status">{cloneStatus}</p>}
      <p style={{ color: "#8b949e", fontSize: "0.8rem", margin: "0.3rem 0 1rem" }}>
        Upload clean audio of one authorized speaker. References are validated and sent to ElevenLabs,
        but this application does not retain new source files.
      </p>

      {error && <p className="error">{error}</p>}

      {systemVoices.length === 0 ? (
        <p className="empty">No system voices.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>ElevenLabs Voice ID</th>
              <th>Consent</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {systemVoices.map((v) => (
              <tr key={v.id}>
                <td>{v.name}</td>
                <td className="text-cell">{v.elevenlabs_voice_id}</td>
                <td><ConsentCell voice={v} onConfirm={handleConfirmConsent} /></td>
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
              <th>Consent</th>
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
                <td><ConsentCell voice={v} onConfirm={handleConfirmConsent} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ConsentCell({ voice, onConfirm }) {
  if (voice.consent_status === "confirmed") {
    return <span className="badge badge-ready">Confirmed</span>;
  }
  return (
    <button className="btn-consent" onClick={() => onConfirm(voice.id, voice.name)}>
      Record consent
    </button>
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
