// ============================================================
//  Products page (read-only for now)
// ============================================================
//  Replaces the Treeview + refresh_table() + notify_expiry() parts of
//  productspage.py. Save / Update / Delete come in the next step.
// ============================================================

import { useEffect, useState } from "react";

const API_URL = "http://localhost:8000";

export default function ProductsPage({ token, onLogout }) {
  const [products, setProducts] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState("");

  // Every request after login sends the token, like showing a wristband.
  const authHeaders = { Authorization: `Bearer ${token}` };

  // Replaces refresh_table() + notify_expiry().
  async function loadProducts() {
    try {
      const [productsRes, alertsRes] = await Promise.all([
        fetch(`${API_URL}/products`, { headers: authHeaders }),
        fetch(`${API_URL}/products/alerts`, { headers: authHeaders }),
      ]);

      if (productsRes.status === 401) {
        // Token expired or invalid: send the user back to the login page.
        onLogout();
        return;
      }

      setProducts(await productsRes.json());
      setAlerts(await alertsRes.json());
      setError("");
    } catch {
      setError("Can't reach the server. Is the backend running?");
    }
  }

  // useEffect with [] runs once when the page first appears —
  // like calling refresh_table() at the bottom of productspage.py.
  useEffect(() => {
    loadProducts();
  }, []);

  return (
    <div>
      <h1>Products</h1>
      <button onClick={loadProducts}>Refresh</button>
      <button onClick={onLogout}>Log out</button>

      {error && <p>{error}</p>}

      {/* Expiry alerts (was a messagebox popup) */}
      {alerts.map((alert) => (
        <p key={alert.batch_id}>⚠️ {alert.message}</p>
      ))}

      {/* The table (was ttk.Treeview) */}
      <table>
        <thead>
          <tr>
            <th>Batch ID</th>
            <th>Product Name</th>
            <th>Price</th>
            <th>Quantity</th>
            <th>Date Added</th>
            <th>Expiry Date</th>
          </tr>
        </thead>
        <tbody>
          {/* .map() turns each product into a table row —
              like the for loop that did tree.insert() */}
          {products.map((p) => (
            <tr key={p.batch_id}>
              <td>{p.batch_id}</td>
              <td>{p.product_name}</td>
              <td>{p.price}</td>
              <td>{p.quantity}</td>
              <td>{p.date_added}</td>
              <td>{p.expiry_date}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {products.length === 0 && !error && <p>No products yet.</p>}
    </div>
  );
}
