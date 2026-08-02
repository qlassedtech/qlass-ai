def tokenize_words(text: str) -> list[str]:
    """
    Lowercase alphabetic runs, in order — e.g. "Don't know?" -> ["don", "t",
    "know"]. Deliberately not regex: a plain character scan is just as
    correct for this (split on anything that isn't a-z) and easier to
    follow than a pattern.
    """
    words: list[str] = []
    current: list[str] = []
    for ch in text.lower():
        if "a" <= ch <= "z":
            current.append(ch)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words
