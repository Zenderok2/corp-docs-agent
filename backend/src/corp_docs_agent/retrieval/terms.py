import re

from snowballstemmer import stemmer


WORD = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
CYRILLIC = re.compile(r"^[а-я]+$")
STOP_WORDS = frozenset(
    """а без близко был была были было быть в вам вас весь вместе во вот все всегда всего всех
    где да даже для до его ее если есть еще же за и из или им их к как какой какая какие каким
    какую какое когда кто куда ли либо мне мы на над надо нам наш не него нет ни но об о один
    она они оно от по под пожалуйста пусть при про с сам свой се сего со так также такой там
    те тем то только тот у уже чего чем что чтобы эта эти это этом я мне меня можно должен
    должна должны нужно скажите подскажите расскажите после перед между чем можно""".split()
)


def words(text: str) -> list[str]:
    return WORD.findall(text.casefold().replace("ё", "е"))


def stems(tokens: list[str]) -> list[str]:
    russian = stemmer("russian")
    return [russian.stemWord(word) if CYRILLIC.fullmatch(word) else word for word in tokens]


def index_text(text: str) -> str:
    return " ".join(stems(words(text)))


def query_terms(text: str) -> list[str]:
    tokens = [token for token in words(text) if token not in STOP_WORDS and (len(token) > 1 or token.isdigit())]
    return list(dict.fromkeys(stems(tokens)))[:32]


def canonical_question(text: str) -> str:
    return " ".join(words(text))


def fts_query(terms: list[str]) -> str:
    return " OR ".join(f'"{term}"' for term in terms)
