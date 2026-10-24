// ============================================================
//  Reports page (admins only)
// ============================================================
//  Two summaries the backend works out with SQL GROUP BY:
//    - sales by month (with a simple bar chart)
//    - sales by customer (biggest buyer first, and what each still owes)
//  The bar chart is plain HTML + CSS: each bar is a <div> whose height
//  is a percentage of the biggest month. No chart library needed.
// ============================================================

import { useEffect, useState } from "react";

const API_URL = "http://localhost:8000";

const naira = (n) => "₦" + Number(n).toLocaleString("en-NG", { maximumFractionDigits: 0 });
// Short version for chart labels: ₦5.3M, ₦850K
const nairaShort = (n) =>
  n >= 1_000_000 ? `₦${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `₦${Math.round(n / 1_000)}K` : `₦${n}`;
// "2026-02" -> "Feb 2026"
const monthName = (ym) =>
  new Date(`${ym}-01T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "numeric" });

// Fill in months with no orders (e.g. Nov 2025) so gaps show up in the chart
// instead of silently disappearing.
function fillGaps(rows) {
  if (rows.length === 0) return rows;
  const byMonth = Object.fromEntries(rows.map((r) => [r.month, r]));
  const out = [];
  let [y, m] = rows[0].month.split("-").map(Number);
  const last = rows[rows.length - 1].month;
  while (true) {
    const key = `${y}-${String(m).padStart(2, "0")}`;
    out.push(byMonth[key] ?? { month: key, orders: 0, cartons: 0, sales: 0, owed: 0 });
    if (key === last) break;
    m += 1;
    if (m > 12) { m = 1; y += 1; }
  }
  return out;
}

export default function ReportsPage({ token, onLogout }) {
  const [months, setMonths] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const headers = { Authorization: `Bearer ${token}` };
    Promise.all([
      fetch(`${API_URL}/reports/monthly`, { headers }),
      fetch(`${API_URL}/reports/customers`, { headers }),
    ])
      .then(async ([m, c]) => {
        if (m.status === 401) return onLogout();
        if (!m.ok) return setMessage("Only admins can see reports.");
        setMonths(fillGaps(await m.json()));
        setCustomers(await c.json());
      })
      .catch(() => setMessage("Can't reach the server. Is the backend running?"));
  }, []);

  const biggestMonth = Math.max(...months.map((m) => m.sales), 1);
  const totalSales = customers.reduce((sum, c) => sum + c.sales, 0);
  const totalOwed = customers.reduce((sum, c) => sum + c.owed, 0);
  const topDebtor = [...customers].sort((a, b) => b.owed - a.owed)[0];

  return (
    <div>
      <h1>Reports</h1>
      {message && <p className="message">{message}</p>}

      <div className="stats">
        <div><span>Total sales</span><strong>{naira(totalSales)}</strong></div>
        <div><span>Collected</span><strong>{naira(totalSales - totalOwed)}</strong></div>
        <div><span>Still owed</span><strong>{naira(totalOwed)}</strong></div>
        <div>
          <span>Owes the most</span>
          <strong>{topDebtor ? topDebtor.customer_name : "—"}</strong>
        </div>
      </div>

      {/* ---------- Sales by month ---------- */}
      <section className="card">
        <h2>Sales by month</h2>
        <div className="legend">
          <span><i className="swatch paid" /> Collected</span>
          <span><i className="swatch owed" /> Still owed</span>
        </div>
        <div className="chart">
          {months.map((m) => {
            const height = (m.sales / biggestMonth) * 100; // % of the tallest bar
            const owedShare = m.sales ? (m.owed / m.sales) * 100 : 0;
            return (
              <div className="bar-col" key={m.month} title={`${monthName(m.month)}: ${naira(m.sales)} sales, ${naira(m.owed)} owed`}>
                <span className="bar-value">{nairaShort(m.sales)}</span>
                <div className="bar-track">
                  <div className="bar" style={{ height: `${height}%` }}>
                    {/* the pink top part is what's still owed */}
                    <div className="bar-owed" style={{ height: `${owedShare}%` }} />
                  </div>
                </div>
                <span className="bar-label">{monthName(m.month)}</span>
              </div>
            );
          })}
        </div>

        <table>
          <thead>
            <tr>
              <th>Month</th>
              <th>Orders</th>
              <th>Cartons</th>
              <th>Sales</th>
              <th>Still owed</th>
            </tr>
          </thead>
          <tbody>
            {months.map((m) => (
              <tr key={m.month}>
                <td>{monthName(m.month)}</td>
                <td>{m.orders}</td>
                <td>{m.cartons || "—"}</td>
                <td>{naira(m.sales)}</td>
                <td>{naira(m.owed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* ---------- Sales by customer ---------- */}
      <section className="card">
        <h2>Customers</h2>
        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Orders</th>
              <th>Cartons</th>
              <th>Sales</th>
              <th>Share of sales</th>
              <th>Still owed</th>
              <th>Last order</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((c) => {
              const share = totalSales ? (c.sales / totalSales) * 100 : 0;
              return (
                <tr key={c.customer_name}>
                  <td>{c.customer_name}</td>
                  <td>{c.orders}</td>
                  <td>{c.cartons || "—"}</td>
                  <td>{naira(c.sales)}</td>
                  <td>
                    <div className="share">
                      <div style={{ width: `${share}%` }} />
                    </div>
                    {share.toFixed(0)}%
                  </td>
                  <td className={c.owed > 0 ? "owed" : ""}>{naira(c.owed)}</td>
                  <td>{c.last_order}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
