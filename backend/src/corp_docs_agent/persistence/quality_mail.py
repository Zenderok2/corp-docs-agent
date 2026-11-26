from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
import sqlite3
from uuid import uuid4

from corp_docs_agent.persistence.store import now


DOMAIN = "corp-docs.test"
QUALITY = f"quality@{DOMAIN}"
SUBJECT = "Вопрос по корпоративным документам"
REASONS = {
    "no_evidence": "В доступных документах не найдено прямого основания для ответа.",
    "partial": "Документы подтверждают только часть ответа.",
    "conflict": "Применимые источники содержат расходящиеся положения.",
    "clarification_exhausted": "После уточнений область применения или ответ остались неясными.",
    "unknown_publication": "Действие найденной редакции на дату вопроса не подтверждено.",
}


def send_request(conn: sqlite3.Connection, case_id: str, root_id: str, question: str, reason: str) -> None:
    conn.execute(
        """INSERT INTO quality_mail(id, case_id, direction, sender, recipient, cc, subject, body, sent_at)
        VALUES (?, ?, 'request', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(case_id) WHERE direction = 'request' DO NOTHING""",
        (uuid4().hex, case_id, f"agent@{DOMAIN}", QUALITY, f"employee-{root_id[:16]}@{DOMAIN}",
         SUBJECT, f"Вопрос\n{question}\n\nПричина\n{REASONS.get(reason, REASONS['no_evidence'])}", now()),
    )


def recover_pending_mail(conn: sqlite3.Connection) -> None:
    pending = conn.execute(
        """SELECT k.id, k.root_id, q.question, json_extract(q.result, '$.message_code') AS reason
        FROM quality_cases k JOIN questions q ON q.id = k.question_id
        WHERE k.status = 'pending' AND NOT EXISTS (SELECT 1 FROM quality_mail m WHERE m.case_id = k.id)""",
    ).fetchall()
    for row in pending:
        send_request(conn, row["id"], row["root_id"], row["question"], row["reason"])


def mail_thread(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM quality_mail WHERE case_id = ? ORDER BY rowid", (case_id,),
    )]


def send_reply(conn: sqlite3.Connection, thread: list[dict], body: str, sent_at: str) -> str | None:
    if not thread:
        return None
    request = thread[0]
    reply_id = uuid4().hex
    conn.execute(
        """INSERT INTO quality_mail(id, case_id, in_reply_to, direction, sender, recipient, subject, body, sent_at)
        VALUES (?, ?, ?, 'reply', ?, ?, ?, ?, ?)""",
        (reply_id, request["case_id"], thread[-1]["id"], QUALITY, request["cc"], f"Re: {request['subject']}", body, sent_at),
    )
    return reply_id


def as_eml(message: sqlite3.Row, root_message_id: str) -> bytes:
    mail = EmailMessage(policy=SMTP)
    mail["From"] = message["sender"]
    mail["To"] = message["recipient"]
    mail["Subject"] = message["subject"]
    mail["Date"] = datetime.fromisoformat(message["sent_at"])
    mail["Message-ID"] = f"<{message['id']}@{DOMAIN}>"
    if message["cc"]:
        mail["Cc"] = message["cc"]
    if parent := message["in_reply_to"]:
        mail["In-Reply-To"] = f"<{parent}@{DOMAIN}>"
        ancestors = [root_message_id] if parent == root_message_id else [root_message_id, parent]
        mail["References"] = " ".join(f"<{item}@{DOMAIN}>" for item in ancestors)
    mail.set_content(message["body"])
    return mail.as_bytes()
