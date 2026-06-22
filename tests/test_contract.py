from datetime import date

from hr_assistant.contract import EmployeeInfo, generate_contract_draft


def test_generate_contract_draft():
    employee = EmployeeInfo(
        name="홍길동",
        position="사원",
        department="인사팀",
        start_date=date(2026, 7, 1),
        salary=36_000_000,
    )
    draft = generate_contract_draft(employee)

    assert "홍길동" in draft
    assert "인사팀" in draft
    assert "36,000,000" in draft
    assert "2026-07-01" in draft
    assert "09:00~18:00" in draft


def test_custom_work_hours():
    employee = EmployeeInfo(
        name="김철수",
        position="대리",
        department="개발팀",
        start_date=date(2026, 8, 1),
        salary=42_000_000,
        work_hours="10:00~19:00",
    )
    draft = generate_contract_draft(employee)

    assert "10:00~19:00" in draft
