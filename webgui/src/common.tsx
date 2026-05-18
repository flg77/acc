// Small shared presentational helpers for the acc-webgui screens.
import type { ReactNode } from "react";

export function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

export function KV({ data }: { data: Record<string, unknown> }) {
  return (
    <table className="kv">
      <tbody>
        {Object.entries(data).map(([k, v]) => (
          <tr key={k}>
            <th>{k}</th>
            <td>{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
}) {
  return (
    <table className="data">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr>
            <td colSpan={columns.length} className="empty">
              no data
            </td>
          </tr>
        )}
        {rows.map((row, i) => (
          <tr key={i}>
            {columns.map((c) => (
              <td key={c}>
                {typeof row[c] === "object"
                  ? JSON.stringify(row[c])
                  : String(row[c] ?? "")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Empty({ what }: { what: string }) {
  return <p className="empty">No {what} yet — waiting for collective signals.</p>;
}
