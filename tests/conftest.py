import pytest

from pe_metrics.db import connect
from pe_metrics.definitions import load_definitions, load_questions
from pe_metrics.generate import generate_portfolio


@pytest.fixture(scope="session")
def definitions():
    return load_definitions()


@pytest.fixture(scope="session")
def questions(definitions):
    return load_questions(definitions=definitions)


@pytest.fixture(scope="session")
def portfolio(tmp_path_factory):
    return generate_portfolio(tmp_path_factory.mktemp("ledgers"))


@pytest.fixture(scope="session")
def connection(portfolio):
    opened = connect(portfolio)
    yield opened
    opened.close()
