from sgt.processing.ticker_extraction import extract_tickers

UNIVERSE = {"AAPL", "GME", "TSLA", "F", "T", "A", "IT", "DD"}


def test_cashtag_in_universe():
    assert extract_tickers("buying $AAPL calls", UNIVERSE) == {"AAPL"}


def test_cashtag_not_in_universe_ignored():
    assert extract_tickers("$ZZZZ to the moon", UNIVERSE) == set()


def test_bare_ticker_in_universe():
    assert extract_tickers("GME is squeezing again", UNIVERSE) == {"GME"}


def test_stopwords_excluded_even_if_in_universe():
    # IT and DD are in the universe here but are blocklisted stopwords.
    assert extract_tickers("great DD on this, IT sector strong", UNIVERSE) == set()


def test_common_words_never_match():
    assert extract_tickers("ALL IN NOW FOR THE WIN", UNIVERSE) == set()


def test_multiple_and_dedup():
    assert extract_tickers("$AAPL and AAPL and TSLA", UNIVERSE) == {"AAPL", "TSLA"}


def test_empty():
    assert extract_tickers("", UNIVERSE) == set()
