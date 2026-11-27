import type { Citation, DepartmentComparisonRow } from '../../shared/api'

type Props = {
  rows: DepartmentComparisonRow[]
  citations: Citation[]
  openSource: (id: string) => void
}

export function DepartmentComparison({ rows, citations, openSource }: Props) {
  return <div className="department-comparison" role="region" tabIndex={0} aria-label="Сроки по подразделениям">
    <table role="table">
      <caption>Сроки по подразделениям</caption>
      <thead role="rowgroup">
        <tr role="row">
          <th role="columnheader" scope="col" className="comparison-department">Подразделение</th>
          <th role="columnheader" scope="col" className="comparison-timing">Действие и срок</th>
          <th role="columnheader" scope="col">Условие</th>
          <th role="columnheader" scope="col" className="comparison-reference">Источник</th>
        </tr>
      </thead>
      <tbody role="rowgroup">{rows.map((row) => {
        const citation = citations.find((item) => item.id === row.citation_id)
        const locator = citation?.locator
        const clause = locator && 'clause_number' in locator ? locator.clause_number : undefined
        const sourceLabel = clause ? `п. ${clause}` : 'Цитата'
        return <tr role="row" key={row.department}>
          <th role="rowheader" scope="row">{row.department}</th>
          <td role="cell" className="comparison-rule">
            <div>{row.action}</div>
            <div className="comparison-deadline"><strong>{row.deadline}</strong></div>
            <div>{row.anchor}</div>
          </td>
          <td role="cell" className="comparison-condition">
            <span className="comparison-condition-label" aria-hidden="true">Условие</span>{row.condition ?? '—'}
          </td>
          <td role="cell" className="comparison-citation">{citation && <button type="button" className="button text-button comparison-source"
            aria-label={`${sourceLabel} — ${row.department}`} onClick={() => openSource(citation.id)}>
            {sourceLabel}
          </button>}</td>
        </tr>
      })}</tbody>
    </table>
  </div>
}
