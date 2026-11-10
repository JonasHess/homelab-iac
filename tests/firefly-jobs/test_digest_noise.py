import ast

from conftest import SCRIPTS

# digest.py runs its whole report at module level (environment, API calls, mail), so it
# cannot be imported here. Only the filter constant is read from the source.
_tree = ast.parse((SCRIPTS / "digest.py").read_text(encoding="utf-8"))
NOT_A_FAILURE = next(ast.literal_eval(n.value) for n in _tree.body
                     if isinstance(n, ast.Assign)
                     and any(getattr(t, "id", None) == "NOT_A_FAILURE" for t in n.targets))


def is_failure(line):
    """The digest's own test for an importer log line, as applied in digest.py."""
    return "production.ERROR" in line and not any(x in line for x in NOT_A_FAILURE)


class TestImporterNoise:
    def test_content_duplicate_submission_error_is_noise(self):
        # A CSV row without external_id that is offered again is rejected by content.
        # The importer logs it twice: the [a117] line and this one, which names no code.
        line = ('[2026-11-10 04:00:02] production.ERROR: Submission error: 0 '
                '["Duplicate of transaction #9918."] ')
        assert not is_failure(line)

    def test_a117_companion_line_is_noise(self):
        line = ('[2026-11-10 04:00:02] production.ERROR: [2.3.4]: [a117]: transactions.0.'
                'description: Duplicate of transaction #9918. (original value: "SPAREN 3")  ')
        assert not is_failure(line)

    def test_other_submission_error_still_reported(self):
        line = ('[2026-11-10 04:00:02] production.ERROR: Submission error: 0 '
                '["This value is invalid for this field."] ')
        assert is_failure(line)
