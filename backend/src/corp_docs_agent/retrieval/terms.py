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
    должна должны нужно скажите подскажите расскажите после перед между чем можно узнать""".split()
)

# Glossary aliases are search vocabulary. They do not define responsibilities or scope.
GLOSSARY = {
    "рп": ("руководитель проекта", "проджект менеджер", "проектный менеджер", "project manager"),
    "па": ("пресейл архитектор", "presale architect", "presales architect"),
    "ам": ("аккаунт менеджер", "account manager"),
    "ап": ("администратор проекта", "project administrator"),
    "цк": ("центр компетенций",),
    "диб": ("дирекция информационной безопасности",),
    "уиб": ("управление информационной безопасности",),
    "знп": ("заявка на пресейл", "presale request"),
    "рм": ("ресурс менеджер", "ресурсный менеджер", "resource manager"),
    "гип": ("главный инженер проекта",),
    "ртз": ("расчет трудозатрат",),
    "кп": ("коммерческое предложение",),
    "ткп": ("технико коммерческое предложение",),
    "sla": ("service level agreement", "соглашение об уровне обслуживания"),
    "rfi": ("request for information", "запрос информации"),
    "дрп": ("департамент работы с подрядчиками",),
    "опэ": ("окончательная приемка эксплуатация",),
    "пми": ("программа и методика испытаний",),
    "пси": ("приемосдаточные испытания",),
    "зни": ("запрос на изменение",),
}


def words(text: str) -> list[str]:
    return WORD.findall(text.casefold().replace("ё", "е"))


def stems(tokens: list[str]) -> list[str]:
    russian = stemmer("russian")
    return [russian.stemWord(word) if CYRILLIC.fullmatch(word) else word for word in tokens]


def index_text(text: str) -> str:
    return " ".join(stems(words(text)))


ALIASES = {
    tuple(stems(words(phrase))): stems([short])[0]
    for short, phrases in GLOSSARY.items() for phrase in (short, *phrases)
}
ALIAS_LENGTHS = sorted({len(key) for key in ALIASES}, reverse=True)


def concepts(text: str, *, drop_stop_words: bool = False) -> list[str]:
    original = words(text)
    tokens = stems(original)
    result = []
    offset = 0
    while offset < len(tokens):
        for length in ALIAS_LENGTHS:
            phrase = tuple(tokens[offset:offset + length])
            if concept := ALIASES.get(phrase):
                result.append(concept)
                offset += len(phrase)
                break
        else:
            if not drop_stop_words or original[offset] not in STOP_WORDS:
                result.append(tokens[offset])
            offset += 1
    return result


def query_terms(text: str) -> list[str]:
    tokens = dict.fromkeys(
        token for token in concepts(text, drop_stop_words=True) if len(token) > 1 or token.isdigit()
    )
    return list(tokens)[:32]


def canonical_question(text: str) -> str:
    return " ".join(words(text))


def fts_query(terms: list[str]) -> str:
    phrases = [f'"{term}"' for term in terms]
    for phrase, concept in ALIASES.items():
        if len(phrase) > 1 and concept in terms:
            phrases.append('"' + ' '.join(phrase) + '"')
    return " OR ".join(phrases)


def scope_key(text: str) -> str:
    tokens = stems(words(text))
    alias = ALIASES.get(tuple(tokens))
    return alias if alias in {"диб", "уиб"} else " ".join(words(text))


def glossary_hints(text: str) -> list[str]:
    used = set(concepts(text))
    return [" = ".join((key.upper(), *phrases)) for key, phrases in GLOSSARY.items() if stems([key])[0] in used]
