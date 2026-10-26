from apps.common.codes import GYM_CODE, allocate_code


def test_code_comes_from_the_label() -> None:
    assert allocate_code([], "E2E Cycle", prefix="CLASS") == "E2E_CYCLE"
    assert allocate_code([], "E2E PT pack", prefix="PACKAGE") == "E2E_PT_PACK"
    assert allocate_code([], "E2E One Day", prefix="PLAN") == "E2E_ONE_DAY"
    assert allocate_code([], "Ten sessions", prefix="PACKAGE") == "TEN_SESSIONS"


def test_code_adds_a_suffix_when_the_label_is_taken() -> None:
    assert allocate_code(["E2E_CYCLE"], "E2E Cycle", prefix="CLASS") == "E2E_CYCLE_2"
    assert allocate_code(["RENT"], "Rent", prefix="EXPENSE", reserved=["RENT"]) == "RENT_2"


def test_code_prefixes_a_label_that_does_not_start_with_a_letter() -> None:
    assert allocate_code([], "10 sessions", prefix="PACKAGE") == "PACKAGE_10_SESSIONS"


def test_gym_code_uses_hyphens() -> None:
    code = allocate_code([], "Second Gym", prefix="GYM", separator="-", pattern=GYM_CODE)
    assert code == "SECOND-GYM"
