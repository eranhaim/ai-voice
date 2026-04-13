import { useState, useEffect } from "react";
import { getUsers, addUser, deleteUser } from "../api";

export default function Users() {
  const [users, setUsers] = useState([]);
  const [telegramId, setTelegramId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      setUsers(await getUsers());
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
    if (!telegramId) return;
    try {
      await addUser(telegramId, name);
      setTelegramId("");
      setName("");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleDelete(id) {
    if (!confirm(`Remove user ${id}?`)) return;
    try {
      await deleteUser(id);
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
      <form onSubmit={handleAdd} className="add-form">
        <input
          type="number"
          placeholder="Telegram ID"
          value={telegramId}
          onChange={(e) => setTelegramId(e.target.value)}
          required
        />
        <input
          type="text"
          placeholder="Name (optional)"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit">Add User</button>
      </form>

      {error && <p className="error">{error}</p>}

      {users.length === 0 ? (
        <p className="empty">No authorized users yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Telegram ID</th>
              <th>Name</th>
              <th>Added</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.telegram_id}>
                <td>{u.telegram_id}</td>
                <td>{u.name}</td>
                <td>{formatDate(u.created_at)}</td>
                <td>
                  <button
                    className="btn-delete"
                    onClick={() => handleDelete(u.telegram_id)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
