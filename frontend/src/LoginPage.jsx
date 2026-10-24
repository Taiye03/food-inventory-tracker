
import { useState } from "react";

// Where the backend lives. Same address you used for the /docs page.
const API_URL = "http://localhost:8000";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // A third remembered value: the message shown under the form.
  const [message, setMessage] = useState("");

  // Runs when the Login button is clicked.
  // Tkinter version: def login(): ... read users.csv and compare passwords.
  // Here the page doesn't check anything itself — it asks the backend.
  async function handleLogin(e) {
    e.preventDefault(); // stop the browser from reloading the page on submit

    if (!username || !password) {
      setMessage("Please enter a username and password.");
      return;
    }

    try {
      // fetch = send a request to the backend and wait for the answer.
      // The login endpoint expects form data, like the docs page sent.
      const response = await fetch(`${API_URL}/auth/login`, {
        method: "POST",
        body: new URLSearchParams({ username, password }),
      });

      if (!response.ok) {
        // 401 = wrong username or password (same as messagebox.showerror)
        setMessage("Invalid username or password.");
        return;
      }

      // 200 = success. The backend sends back { access_token: "eyJ..." }
      const data = await response.json();
      // Save the token in the browser so later pages can prove who's logged in.
      localStorage.setItem("token", data.access_token);
      setMessage(`Logged in as ${username}.`);
    } catch {
      // fetch itself failed: usually the backend isn't running.
      setMessage("Can't reach the server. Is the backend running?");
    }
  }

  return (
    // <form> + onSubmit means clicking the button OR pressing Enter runs handleLogin
    <form onSubmit={handleLogin}>
      <h1>Login</h1>

      <label>Username</label>
      <input
        type="text"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
      />

      <label>Password</label>
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />

      <button type="submit">Login</button>

      {/* Only shows when message isn't empty */}
      {message && <p>{message}</p>}
    </form>
  );
}