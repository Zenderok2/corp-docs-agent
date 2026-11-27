import type { Citation, DepartmentComparisonRow } from '../../shared/api'

type Props = {
  rows: DepartmentComparisonRow[]
  citations: Citation[]
  openSource: (id: string) => void
}

export function DepartmentComparison({ rows, citations, openSource }: Props) {
  return <div className="department-comparison" role="region" tabIndex={0} aria-label="Сроки по подразделениям">
    <table>
      <caption>Сроки по подразделениям</caption>
      <thead>
        <tr>
          <th scope="col" className="comparison-department">Подразделение</th>
          <th scope="col" className="comparison-timing">Действие и срок</th>
          <th scope="col">Условие</th>
          <th scope="col" className="comparison-reference">Источник</th>
        </tr>
      </thead>
      <tbody>{rows.map((row) => {
        const citation = citations.find((item) => item.id === row.citation_id)
        const locator = citation?.locator
        const clause = locator && 'clause_number' in locator ? locator.clause_number : undefined
        const sourceLabel = clause ? `п. ${clause}` : 'Цитата'
        return <tr key={row.department}>
          <th scope="row">{row.department}</th>
          <td>
            <div>{row.action}</div>
            <div className="comparison-deadline"><strong>{row.deadline}</strong></div>
            <div>{row.anchor}</div>
          </td>
          <td>{row.condition ?? '—'}</td>
          <td>{citation && <button type="button" className="button text-button comparison-source"
            aria-label={`${sourceLabel} — ${row.department}`} onClick={() => openSource(citation.id)}>
            {sourceLabel}
          </button>}</td>
        </tr>
      })}</tbody>
    </table>
  </div>
}
