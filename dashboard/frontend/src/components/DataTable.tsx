import { useMemo, useState, type ReactNode } from 'react'
import { useReactTable, getCoreRowModel, getSortedRowModel, flexRender, type ColumnDef, type SortingState } from '@tanstack/react-table'

export interface Column<T> {
  header: string
  render: (row: T) => ReactNode
  align?: 'left' | 'right'
  /** Opt-in: enables a clickable sort-toggle header for this column, sorted
   * by this accessor's returned value. Columns without this render exactly
   * as before TanStack Table adoption — no sort affordance, no behavior
   * change (this is why every one of the other DataTable consumers is
   * unaffected by this component's internals changing). */
  accessorFn?: (row: T) => string | number
  /** Numeric columns should pass 'basic' — the default 'alphanumeric'
   * sorter is for text and misorders floats. */
  sortingFn?: 'alphanumeric' | 'basic'
}

export function DataTable<T>({ columns, rows, rowKey }: { columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string }) {
  const tableColumns = useMemo<ColumnDef<T>[]>(
    () =>
      columns.map((col, i) =>
        col.accessorFn
          ? {
              id: `col-${i}`,
              accessorFn: col.accessorFn,
              header: col.header,
              cell: (ctx) => col.render(ctx.row.original),
              sortingFn: col.sortingFn ?? 'alphanumeric',
            }
          : {
              id: `col-${i}`,
              header: col.header,
              cell: (ctx) => col.render(ctx.row.original),
              enableSorting: false,
            }
      ),
    [columns]
  )

  const [sorting, setSorting] = useState<SortingState>([])
  const table = useReactTable({
    data: rows,
    columns: tableColumns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[0.82em]">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h, i) => {
                const col = columns[i]
                const sortable = h.column.getCanSort()
                const sorted = h.column.getIsSorted()
                return (
                  <th
                    key={h.id}
                    onClick={sortable ? h.column.getToggleSortingHandler() : undefined}
                    className={`px-3 py-2 text-muted text-[0.75em] uppercase tracking-[0.8px] bg-surface font-semibold ${
                      col.align === 'right' ? 'text-right' : 'text-left'
                    } ${sortable ? 'cursor-pointer select-none hover:text-text' : ''}`}
                  >
                    {col.header}
                    {sortable && <span className="ml-1 text-[0.9em] opacity-70">{sorted === 'asc' ? '▲' : sorted === 'desc' ? '▼' : ''}</span>}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={rowKey(row.original)} className="hover:bg-accent/[0.03] [&:last-child>td]:border-b-0">
              {row.getVisibleCells().map((cell, i) => (
                <td key={cell.id} className={`px-3 py-2.5 border-b border-border ${columns[i].align === 'right' ? 'text-right' : ''}`}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
