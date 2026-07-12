"""AuthContext:貫穿 application 層的請求身分(PHASE_1 §6、MASTER_PLAN_v1 §F.8)。

frozen model;`trace_id` 型別一律 `str`(§R R4)。ownership 過濾在 repository 層以
`user_id` 實作,NEVER 在 service 事後過濾。
"""
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    role: str
    trace_id: str
