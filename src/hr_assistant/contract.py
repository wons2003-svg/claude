"""근로계약서 초안 생성 모듈"""

from dataclasses import dataclass
from datetime import date


@dataclass
class EmployeeInfo:
    name: str
    position: str
    department: str
    start_date: date
    salary: int
    work_hours: str = "09:00~18:00"


def generate_contract_draft(employee: EmployeeInfo) -> str:
    return (
        f"근로계약서\n"
        f"{'='*40}\n"
        f"성명: {employee.name}\n"
        f"직위: {employee.position}\n"
        f"부서: {employee.department}\n"
        f"입사일: {employee.start_date.isoformat()}\n"
        f"급여: {employee.salary:,}원\n"
        f"근무시간: {employee.work_hours}\n"
        f"{'='*40}\n"
    )
