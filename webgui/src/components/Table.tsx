// A table in the design system's markup (design-system/patterns/declared-observed.html):
// PatternFly's own table classes, compact, collapsing to a grid under 768 px so it
// holds inside a Showroom tab. Small on purpose — sorting and paging arrive with the
// first screen that needs them.

import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
}

/** A value from the snapshot: strings as they are, objects as JSON, nothing as a dash. */
export const cell = (v: unknown): string => {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
};

export function Table<T>({
  ariaLabel,
  columns,
  rows,
  rowKey,
}: {
  ariaLabel: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
}) {
  return (
    <table className="pf-v6-c-table pf-m-compact pf-m-grid-md" role="grid" aria-label={ariaLabel}>
      <thead className="pf-v6-c-table__thead">
        <tr className="pf-v6-c-table__tr" role="row">
          {columns.map((c) => (
            <th key={c.key} className="pf-v6-c-table__th" role="columnheader" scope="col">
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="pf-v6-c-table__tbody" role="rowgroup">
        {rows.map((row, i) => (
          <tr key={rowKey(row, i)} className="pf-v6-c-table__tr" role="row">
            {columns.map((c) => (
              <td key={c.key} className="pf-v6-c-table__td" role="cell" data-label={c.label}>
                {c.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
