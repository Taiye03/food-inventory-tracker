// ============================================================
//  Orders page (new — the Tkinter app had no orders)
// ============================================================
//  The website version of the client's handwritten sales ledger:
//  date, customer, product, cartons, amount, amount paid.
//  The backend works out each order's balance and status
//  (Paid / Part paid / Owing).
//
//  Anyone logged in can record a sale. Only admins can edit or delete.
//  Orders don't change product stock yet; that can be linked later.
// ============================================================

import { useEffect, useState } from "react";

const API_URL = "http://localhost:8000";

const today = () => new Date().toISOString().slice(0, 10);

const emptyForm = {
  order_date: today(),
  customer_name: "",
  product_name: "",
  cartons: "",
  amount: "",
  amount_paid: "",
  notes: "",
};

// Shows money as "₦22,500"
const naira = (n) => "₦" + Number(n).toLocaleString("en-NG", { maximumFractionDigits: 2 });

async function errorMessage(response) {
  if (response.status === 403) return "Only admins can edit or delete orders.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => d.msg.replace("Value error, ", "")).join(" ");
    }
  } catch {
    /* no JSON body */
  }
  return `Something went wrong (code ${response.status}).`;
}

export default function OrdersPage({ token, onLogout, isAdmin }) {
  const [orders, setOrders] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [selectedId, setSelectedId] = useState(null);
  const [message, setMessage] = useState("");
  // Filters for the table
  const [search, setSearch] = useState("");
  const [unpaidOnly, setUnpaidOnly] = useState(false);

  const authHeaders = { Authorization: `Bearer ${token}` };

  async function loadOrders() {
    try {
      const response = await fetch(`${API_URL}/orders`, { headers: authHeaders });
      if (response.status === 401) return onLogout();
      setOrders(await response.json());
    } catch {
      setMessage("Can't reach the server. Is the backend running?");
    }
  }

  useEffect(() => {
    loadOrders();
  }, []);

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value });
  }

  function clearForm() {
    setForm({ ...emptyForm, order_date: today() });
    setSelectedId(null);
  }

  function selectRow(o) {
    if (!isAdmin) return; // staff can't edit, so don't load the row into the form
    setSelectedId(o.id);
    setForm({
      order_date: o.order_date,
      customer_name: o.customer_name,
      product_name: o.product_name,
      cartons: o.cartons,
      amount: o.amount,
      amount_paid: o.amount_paid,
      notes: o.notes ?? "",
    });
    setMessage(`Editing order #${o.id}`);
  }

  function formAsJson() {
    return JSON.stringify({
      ...form,
      cartons: Number(form.cartons),
      amount: Number(form.amount),
      amount_paid: Number(form.amount_paid || 0), // blank = nothing paid yet
      notes: form.notes || null,
    });
  }

  async function send(url, method, body, successText) {
    try {
      const response = await fetch(url, {
        method,
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body,
      });
      if (!response.ok) return setMessage(await errorMessage(response));
      setMessage(successText);
      clearForm();
      loadOrders();
    } catch {
      setMessage("Can't reach the server. Is the backend running?");
    }
  }

  function saveOrder(e) {
    e.preventDefault();
    send(`${API_URL}/orders`, "POST", formAsJson(), "Order recorded.");
  }

  function updateOrder() {
    send(`${API_URL}/orders/${selectedId}`, "PUT", formAsJson(), `Order #${selectedId} updated.`);
  }

  function deleteOrder() {
    if (!window.confirm(`Delete order #${selectedId}?`)) return;
    send(`${API_URL}/orders/${selectedId}`, "DELETE", undefined, `Order #${selectedId} deleted.`);
  }

  // Filtering happens right here in the browser — no extra request needed.
  const shown = orders.filter(
    (o) =>
      o.customer_name.toLowerCase().includes(search.toLowerCase()) &&
      (!unpaidOnly || o.balance > 0)
  );

  // Totals for whatever is currently shown
  const totalCartons = shown.reduce((sum, o) => sum + o.cartons, 0);
  const totalAmount = shown.reduce((sum, o) => sum + o.amount, 0);
  const totalOwed = shown.reduce((sum, o) => sum + o.balance, 0);

  return (
    <div>
      <h1>Orders</h1>

      {/* Summary cards */}
      <div className="stats">
        <div><span>Orders</span><strong>{shown.length}</strong></div>
        <div><span>Cartons sold</span><strong>{totalCartons}</strong></div>
        <div><span>Sales</span><strong>{naira(totalAmount)}</strong></div>
        <div><span>Still owed</span><strong>{naira(totalOwed)}</strong></div>
      </div>

      <form onSubmit={saveOrder}>
        <h2>{selectedId ? `Editing order #${selectedId}` : "Record a sale"}</h2>

        <label>
          Date
          <input name="order_date" type="date" value={form.order_date} onChange={handleChange} required />
        </label>
        <label>
          Customer
          <input name="customer_name" value={form.customer_name} onChange={handleChange} required />
        </label>
        <label>
          Product
          <input name="product_name" value={form.product_name} onChange={handleChange} required />
        </label>
        <label>
          Cartons
          <input name="cartons" type="number" min="1" step="1" value={form.cartons} onChange={handleChange} required />
        </label>
        <label>
          Amount (₦)
          <input name="amount" type="number" min="0" step="0.01" value={form.amount} onChange={handleChange} required />
        </label>
        <label>
          Amount paid (₦)
          <input name="amount_paid" type="number" min="0" step="0.01" value={form.amount_paid} onChange={handleChange} placeholder="0" />
        </label>
        <label>
          Notes
          <input name="notes" value={form.notes} onChange={handleChange} placeholder="optional" />
        </label>

        <div>
          <button type="submit" disabled={selectedId !== null}>Save</button>
          {isAdmin && (
            <>
              <button type="button" onClick={updateOrder} disabled={!selectedId}>Update</button>
              <button type="button" className="danger" onClick={deleteOrder} disabled={!selectedId}>Delete</button>
            </>
          )}
          <button type="button" onClick={clearForm}>Clear</button>
        </div>
      </form>

      {message && <p className="message">{message}</p>}

      {/* Filters */}
      <div className="toolbar">
        <input
          className="search"
          placeholder="Search customer…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="check">
          <input type="checkbox" checked={unpaidOnly} onChange={(e) => setUnpaidOnly(e.target.checked)} />
          Only unpaid
        </label>
      </div>

      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Date</th>
            <th>Customer</th>
            <th>Product</th>
            <th>Cartons</th>
            <th>Amount</th>
            <th>Paid</th>
            <th>Balance</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((o) => (
            <tr
              key={o.id}
              onClick={() => selectRow(o)}
              className={o.id === selectedId ? "selected" : ""}
              style={{ cursor: isAdmin ? "pointer" : "default" }}
              title={o.notes || ""}
            >
              <td>{o.id}</td>
              <td>{o.order_date}</td>
              <td>{o.customer_name}</td>
              <td>{o.product_name}</td>
              <td>{o.cartons}</td>
              <td>{naira(o.amount)}</td>
              <td>{naira(o.amount_paid)}</td>
              <td>{naira(o.balance)}</td>
              <td>
                {/* "Part paid" → class "part-paid" */}
                <span className={"badge " + o.status.toLowerCase().replace(" ", "-")}>{o.status}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {shown.length === 0 && <p className="empty">No orders to show.</p>}
    </div>
  );
}
