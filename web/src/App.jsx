import { useState } from "react";
import { isLoggedIn, logout } from "./api";
import Login from "./components/Login";
import Users from "./components/Users";
import Runs from "./components/Runs";
import "./App.css";

export default function App() {
  const [authed, setAuthed] = useState(isLoggedIn());
  const [tab, setTab] = useState("users");

  if (!authed) {
    return <Login onLogin={() => setAuthed(true)} />;
  }

  return (
    <div className="app">
      <header>
        <h1>Voice Bot Admin</h1>
        <nav>
          <button
            className={tab === "users" ? "active" : ""}
            onClick={() => setTab("users")}
          >
            Users
          </button>
          <button
            className={tab === "runs" ? "active" : ""}
            onClick={() => setTab("runs")}
          >
            Runs
          </button>
        </nav>
        <button
          className="btn-logout"
          onClick={() => {
            logout();
            setAuthed(false);
          }}
        >
          Logout
        </button>
      </header>
      <main>
        {tab === "users" ? <Users /> : <Runs />}
      </main>
    </div>
  );
}
