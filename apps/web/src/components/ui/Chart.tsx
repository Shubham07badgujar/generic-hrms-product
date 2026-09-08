/**
 * Accessible wrapper for a chart.
 *
 * A `<ResponsiveContainer>` renders an SVG of unlabelled paths. To a screen
 * reader that is nothing at all — not a poor experience, an absent one. Recharts
 * cannot fix this for us because only the caller knows what the series means.
 *
 * So every chart in this app goes through here, which gives it two things:
 *
 *   1. `role="img"` with a one-line summary, so a screen reader announces what
 *      the chart shows instead of skipping it or reading stray path data.
 *   2. A visually-hidden data table carrying the ACTUAL numbers, so the
 *      information in the chart is reachable and not merely described.
 *
 * The table is the part that matters. A summary alone tells someone a chart
 * exists; the table lets them read it.
 */

import type { ReactNode } from 'react'

export interface ChartDatum {
  name: string
  value: number
}

export function ChartFrame({
  title,
  data,
  formatValue,
  valueLabel = 'Value',
  height = 224,
  children,
}: {
  /** What the chart is of — used in the accessible summary. */
  title: string
  data: ChartDatum[]
  formatValue: (value: number) => string
  valueLabel?: string
  height?: number
  children: ReactNode
}) {
  const summary = buildSummary(title, data, formatValue)
  const tableId = `chart-data-${slug(title)}`

  return (
    <figure className="m-0">
      <div
        role="img"
        aria-label={summary}
        aria-describedby={tableId}
        style={{ height }}
        className="w-full"
      >
        {children}
      </div>

      {/* The numbers themselves, for anyone who cannot see the shape. */}
      <table id={tableId} className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">Label</th>
            <th scope="col">{valueLabel}</th>
          </tr>
        </thead>
        <tbody>
          {data.map((point) => (
            <tr key={point.name}>
              <th scope="row">{point.name}</th>
              <td>{formatValue(point.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}

/**
 * A one-line description of the chart.
 *
 * Names the range and the extremes rather than reciting every point — the table
 * carries the detail, and an aria-label that reads out forty values is worse
 * than one that orients the listener before they reach it.
 */
function buildSummary(
  title: string,
  data: ChartDatum[],
  formatValue: (value: number) => string,
): string {
  if (data.length === 0) return `${title}: no data.`
  if (data.length === 1) {
    return `${title}: ${data[0]!.name}, ${formatValue(data[0]!.value)}.`
  }

  const highest = data.reduce((a, b) => (b.value > a.value ? b : a))
  const lowest = data.reduce((a, b) => (b.value < a.value ? b : a))

  return (
    `${title}: ${data.length} points from ${data[0]!.name} to ${data[data.length - 1]!.name}. ` +
    `Highest ${highest.name} at ${formatValue(highest.value)}; ` +
    `lowest ${lowest.name} at ${formatValue(lowest.value)}. ` +
    `Full values follow in a table.`
  )
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}
