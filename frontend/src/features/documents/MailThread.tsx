import type { QualityMail } from '../../shared/api'
import { formatTimestamp } from '../../shared/api'

export function MailThread({ mail, expanded = false }: { mail: QualityMail[]; expanded?: boolean }) {
  if (!mail.length) return null
  return <details className="mail-thread" open={expanded}>
    <summary>Тестовая почта <span className="soft">· {mail.length}</span></summary>
    {mail.map((message) => <section className="mail-message" key={message.id}>
      <h4>{message.subject}</h4>
      <dl>
        <dt>Отправлено</dt><dd><time dateTime={message.sent_at}>{formatTimestamp(message.sent_at)} МСК</time></dd>
        <dt>От</dt><dd>{message.sender}</dd>
        <dt>Кому</dt><dd>{message.recipient}</dd>
        {message.cc && <><dt>Копия</dt><dd>{message.cc}</dd></>}
      </dl>
      <p>{message.body}</p>
      <a href={'/api/v1/quality/mail/' + message.id + '/eml'}>Скачать письмо (.eml)</a>
    </section>)}
  </details>
}
