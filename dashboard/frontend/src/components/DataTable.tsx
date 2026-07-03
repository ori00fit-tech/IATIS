import type { ReactNode } from 'react'

export interface Column<T> {
  header: string
  render: (row: T) => ReactNode
  align?: 'left' | 'right'
}

export function DataTable<T>({ columns, rows, rowKey }: { columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string }) {
  return (
    <div className="overflow-x-auto">
    <table className="w-full border-collapse text-[0.82em]">
      <thead>
        <tr>
          {columns.map((col) => (
            <th
              key={col.header}
              className={`px-3 py-2 text-muted text-[0.75em] uppercase tracking-[0.8px] bg-surface font-semibold ${
                col.align === 'right' ? 'text-right' : 'text-left'
              }`}
            >
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)} className="hover:bg-accent/[0.03] [&:last-child>td]:border-b-0">
            {columns.map((col) => (
              <td
                key={col.header}
                className={`px-3 py-2.5 border-b border-border ${col.align === 'right' ? 'text-right' : ''}`}
              >
                {col.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  )
}
