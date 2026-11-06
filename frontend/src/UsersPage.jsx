// ============================================================
//  Users page (admins only)
// ============================================================
//  Replaces users.py:
//    Treeview + refresh_users_table() → the table + loadUsers()
//    Add User popup + save_new_user() → the form + addUser()
//  Differences from the old app:
//    - passwords are hashed by the backend and never sent back
//    - the backend checks the email format and duplicate usernames
//    - only Admin accounts can open this page
// ============================================================

import { useEffect, useState } from "react";

const API_URL = "http://localhost:8000";

const emptyForm = { username: "", fullname: "", role: "User", email: "", password: "" };

async function errorMessage(response) {
  if (response.status === 403) return "Only admins can manage users.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      // e.g. a bad email or a password that's too short
      return body.detail
        .map((d) => `${d.loc[d.loc.length - 1]}: ${d.msg.replace("Value error, ", "")}`)
        .join(" · ");
    }
  } catch {
    /* no JSON body */
  }
  return `Something went wrong (code ${response.status}).`;
}

export default function UsersPage({ token, onLogout }) {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [message, setMessage] = useState("");

  const authHeaders = { Authorization: `Bearer ${token}` };

  async function loadUsers() {
    try {
      const response = await fetch(`${API_URL}/users`, { headers: authHeaders });
      if (response.status === 401) return onLogout();
      if (!response.ok) return setMessage(await errorMessage(response));
      setUsers(await response.json());
    } catch {
      setMessage("Can't reach the server. Is the backend running?");
    }
  }

  useEffect(() => {
    loadUsers();
  }, []);

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value });
  }

  async function addUser(e) {
    e.preventDefault();
    try {
      const response = await fetch(`${API_URL}/users`, {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!response.ok) return setMessage(await errorMessage(response));
      const user = await response.json();
      setMessage(`User ${user.username} added successfully!`);
      setForm(emptyForm);
      loadUsers();
    } catch {
      setMessage("Can't reach the server. Is the backend running?");
    }
  }

  return (
    <div>
      <h1>Users</h1>

      {/* Add User form (was the popup window) */}
      <form onSubmit={addUser}>
        <h2>Add User</h2>

        <label>
          Username
          <input name="username" value={form.username} onChange={handleChange} required />
        </label>

        <label>
          Full Name
          <input name="fullname" value={form.fullname} onChange={handleChange} required />
        </label>

        <label>
          Role
          {/* <select> is the web version of ttk.Combobox */}
          <select name="role" value={form.role} onChange={handleChange}>
            <option value="User">User</option>
            <option value="Admin">Admin</option>
          </select>
        </label>

        <label>
          Email
          <input name="email" type="email" value={form.email} onChange={handleChange} required />
        </label>

        <label>
          Password
          <input name="password" type="password" minLength={12} value={form.password} onChange={handleChange} required />
        </label>

        <button type="submit">Add User</button>
      </form>

      {message && <p className="message">{message}</p>}

      {/* No password column — the API never sends passwords back. */}
      <table>
        <thead>
          <tr>
            <th>Username</th>
            <th>Full Name</th>
            <th>Role</th>
            <th>Email</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.username}</td>
              <td>{u.fullname}</td>
              <td>{u.role}</td>
              <td>{u.email}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
