// App is the top-level component — like the Tk() root window.
// It decides which page to show, replacing the old subprocess/os.execv
// trick that opened a whole new window for every page.
import { useState } from "react";
import LoginPage from "./LoginPage.jsx";
import ProductsPage from "./ProductsPage.jsx";

export default function App() {
  // If a token was saved by an earlier login, start on the Products page.
  const [token, setToken] = useState(localStorage.getItem("token"));

  function handleLogout() {
    localStorage.removeItem("token");
    setToken(null);
  }

  if (!token) {
    // onLogin is a "prop": a function App hands to LoginPage to call later.
    return <LoginPage onLogin={(newToken) => setToken(newToken)} />;
  }
  return <ProductsPage token={token} onLogout={handleLogout} />;
}