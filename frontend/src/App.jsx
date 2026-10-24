// App is the top-level component — like the Tk() root window.
// It decides which page to show, replacing the old subprocess/os.execv
// trick that opened a whole new window for every page.
import { useEffect, useState } from "react";
import LoginPage from "./LoginPage.jsx";
import ProductsPage from "./ProductsPage.jsx";
import OrdersPage from "./OrdersPage.jsx";
import UsersPage from "./UsersPage.jsx";
import ReportsPage from "./ReportsPage.jsx";

const API_URL = "http://localhost:8000";

export default function App() {
  // If a token was saved by an earlier login, start logged in.
  const [token, setToken] = useState(localStorage.getItem("token"));
  // Which page is showing (was: which .py file was running).
  const [page, setPage] = useState("products");
  // Who is logged in, e.g. { username: "admin", role: "Admin", ... }
  const [me, setMe] = useState(null);

  function handleLogout() {
    localStorage.removeItem("token");
    setToken(null);
    setMe(null);
    setPage("products");
  }

  // Whenever the token changes, ask the backend who we are.
  useEffect(() => {
    if (!token) return;
    fetch(`${API_URL}/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => (res.ok ? res.json() : Promise.reject(res)))
      .then(setMe)
      .catch(handleLogout); // bad or expired token: back to login
  }, [token]);

  if (!token) {
    // onLogin is a "prop": a function App hands to LoginPage to call later.
    return <LoginPage onLogin={(newToken) => setToken(newToken)} />;
  }

  const isAdmin = me?.role === "Admin";

  return (
    <div className="app">
      {/* Menu (was the sidebar buttons in every Tkinter page) */}
      <nav>
        <button className={page === "products" ? "active" : ""} onClick={() => setPage("products")}>Products</button>
        <button className={page === "orders" ? "active" : ""} onClick={() => setPage("orders")}>Orders</button>
        {/* Only admins see the Users button */}
        {isAdmin && <button className={page === "reports" ? "active" : ""} onClick={() => setPage("reports")}>Reports</button>}
        {isAdmin && <button className={page === "users" ? "active" : ""} onClick={() => setPage("users")}>Users</button>}
        {me && <span className="who">Logged in as {me.username} ({me.role})</span>}
        <button className="logout" onClick={handleLogout}>Log out</button>
      </nav>

      <main>
      {page === "users" && isAdmin ? (
        <UsersPage token={token} onLogout={handleLogout} />
      ) : page === "reports" && isAdmin ? (
        <ReportsPage token={token} onLogout={handleLogout} />
      ) : page === "orders" ? (
        <OrdersPage token={token} onLogout={handleLogout} isAdmin={isAdmin} />
      ) : (
        <ProductsPage token={token} onLogout={handleLogout} />
      )}
      </main>
    </div>
  );
}
