import pytest

from cos.kuzu import KuzuClient, ReadOnlyViolation

# The Gmail graph is a source system rebuilt nightly from the maildir. Nothing
# here may write to it, so the guard is tested rather than assumed.


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE (p:Person {id: 'x'})",
        "MATCH (p:Person) DELETE p",
        "MATCH (p:Person) SET p.name = 'x'",
        "MERGE (p:Person {id: 'x'})",
        "DROP TABLE Person",
        "COPY Person FROM 'x.csv'",
        "MATCH (p:Person) detach delete p",
        "ALTER TABLE Person ADD x STRING",
    ],
)
def test_mutations_are_refused(statement):
    with pytest.raises(ReadOnlyViolation):
        KuzuClient._assert_read_only(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (p:Person) RETURN p.id",
        "MATCH (e:Email) RETURN count(e)",
        "MATCH (p:Person)-[:SENT]->(e:Email) RETURN p.id, max(e.timestamp)",
    ],
)
def test_reads_are_allowed(statement):
    KuzuClient._assert_read_only(statement)


def test_keyword_inside_a_string_literal_is_not_a_mutation():
    """A subject line containing 'delete' must not trip the guard."""
    KuzuClient._assert_read_only(
        "MATCH (e:Email) WHERE e.subject = 'please delete my account' RETURN e.id"
    )


def test_keyword_as_a_substring_is_not_a_mutation():
    KuzuClient._assert_read_only("MATCH (e:Email) RETURN e.created_at")
