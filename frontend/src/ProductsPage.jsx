// ============================================================
//  Products page
// ============================================================
//  Replaces productspage.py:
//    Treeview + refresh_table()  → the table + loadProducts()
//    notify_expiry()             → the ⚠️ alerts
//    Save / Update / Delete      → saveProduct / updateProduct / deleteProduct
//    select_product (dbl-click)  → click a table row
//    Export Excel                → exportCsv (downloads a real file)
// ============================================================

import { useEffect, useState } from "react";

const API_URL = "http://localhost:8000";

const today = () => new Date().toISOString().slice(0, 10); // "YYYY-MM-DD"

// What an empty form looks like (Date Added defaults to today, like the old app).
const emptyForm = {
  product_name: "",
  price: "",
  quantity: "",
  date_added: today(),
  expiry_date: "",
};

// Turn the backend's error into one readable sentence.
async function errorMessage(response) {
  if (response.status === 403) return "Only admins can change products.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    // 422 validation errors come back as a list
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => d.msg.replace("Value error, ", "")).join(" ");
    }
  } catch {
    /* response had no JSON body */
  }
  return `Something went wrong (code ${response.status}).`;
}

export default function ProductsPage({ token, onLogout }) {
  const [products, setProducts] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [message, setMessage] = useState("");

  // One state object holds every form field (like the six Entry widgets).
  const [form, setForm] = useState(emptyForm);
  // Which row is selected. null = nothing selected (so Save makes a new one).
  const [selectedBatch, setSelectedBatch] = useState(null);

  const authHeaders = { Authorization: `Bearer ${token}` };

  // ---------- Load (refresh_table + notify_expiry) ----------
  async function loadProducts() {
    try {
      const [productsRes, alertsRes] = await Promise.all([
        fetch(`${API_URL}/products`, { headers: authHeaders }),
        fetch(`${API_URL}/products/alerts`, { headers: authHeaders }),
      ]);
      if (productsRes.status === 401) {
        onLogout(); // token expired: back to login
        return;
      }
      setProducts(await productsRes.json());
      setAlerts(await alertsRes.json());
    } catch {
      setMessage("Can't reach the server. Is the backend running?");
    }
  }

  useEffect(() => {
    loadProducts();
  }, []);

  // ---------- Form helpers ----------
  // One onChange for every input: the input's `name` says which field to update.
  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value });
  }

  function clearForm() {
    setForm({ ...emptyForm, date_added: today() });
    setSelectedBatch(null);
  }

  // Clicking a row fills the form (was select_product on double-click).
  function selectRow(p) {
    setSelectedBatch(p.batch_id);
    setForm({
      product_name: p.product_name,
      price: p.price,
      quantity: p.quantity,
      date_added: p.date_added,
      expiry_date: p.expiry_date,
    });
    setMessage(`Selected ${p.batch_id}`);
  }

  // The form keeps everything as text; the backend wants numbers for these two.
  function formAsJson() {
    return JSON.stringify({
      ...form,
      price: Number(form.price),
      quantity: Number(form.quantity),
    });
  }

  // Sends one request, then shows a message and reloads the table.
  async function send(url, method, body, successText) {
    try {
      const response = await fetch(url, {
        method,
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body,
      });
      if (!response.ok) {
        setMessage(await errorMessage(response));
        return;
      }
      const data = response.status === 204 ? null : await response.json();
      setMessage(successText(data));
      clearForm();
      loadProducts();
    } catch {
      setMessage("Can't reach the server. Is the backend running?");
    }
  }

  // ---------- Save / Update / Delete ----------
  function saveProduct(e) {
    e.preventDefault();
    send(`${API_URL}/products`, "POST", formAsJson(),
      (data) => `Product saved with Batch ID: ${data.batch_id}`);
  }

  function updateProduct() {
    if (!selectedBatch) return setMessage("Click a row in the table first.");
    send(`${API_URL}/products/${selectedBatch}`, "PUT", formAsJson(),
      () => `Updated ${selectedBatch}`);
  }

  function deleteProduct() {
    if (!selectedBatch) return setMessage("Click a row in the table first.");
    if (!window.confirm(`Delete ${selectedBatch}?`)) return;
    send(`${API_URL}/products/${selectedBatch}`, "DELETE", undefined,
      () => `Deleted ${selectedBatch}`);
  }

  // ---------- Export (was export_to_excel) ----------
  async function exportCsv() {
    const response = await fetch(`${API_URL}/products/export`, { headers: authHeaders });
    if (!response.ok) return setMessage(await errorMessage(response));
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "products_export.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  // ---------- The page ----------
  return (
    <div>
      <h1>Products</h1>
      <button onClick={loadProducts}>Refresh</button>
      <button onClick={exportCsv}>Export CSV</button>

      {alerts.map((alert) => (
        <p key={alert.batch_id}>⚠️ {alert.message}</p>
      ))}

      {/* The form (was the six Entry widgets + buttons) */}
      <form onSubmit={saveProduct}>
        <h2>{selectedBatch ? `Editing ${selectedBatch}` : "New product"}</h2>

        <label>Product Name</label>
        <input name="product_name" value={form.product_name} onChange={handleChange} required />

        <label>Price</label>
        <input name="price" type="number" min="0" step="0.01" value={form.price} onChange={handleChange} required />

        <label>Quantity</label>
        <input name="quantity" type="number" min="0" step="1" value={form.quantity} onChange={handleChange} required />

        <label>Date Added</label>
        <input name="date_added" type="date" value={form.date_added} onChange={handleChange} required />

        <label>Expiry Date</label>
        <input name="expiry_date" type="date" value={form.expiry_date} onChange={handleChange} required />

        <div>
          <button type="submit" disabled={selectedBatch !== null}>Save</button>
          <button type="button" onClick={updateProduct} disabled={!selectedBatch}>Update</button>
          <button type="button" onClick={deleteProduct} disabled={!selectedBatch}>Delete</button>
          <button type="button" onClick={clearForm}>Clear</button>
        </div>
      </form>

      {message && <p>{message}</p>}

      {/* The table. Click a row to edit it. */}
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
          {products.map((p) => (
            <tr
              key={p.batch_id}
              onClick={() => selectRow(p)}
              style={{ cursor: "pointer", fontWeight: p.batch_id === selectedBatch ? "bold" : "normal" }}
            >
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

      {products.length === 0 && <p>No products yet.</p>}
    </div>
  );
}
