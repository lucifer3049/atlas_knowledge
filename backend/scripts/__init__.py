"""後端維運 / 評測腳本(非 app 執行期程式碼,不隨套件安裝)。

有 `__init__.py` 是為了讓 `tests/` 能以 `scripts.rag_eval` 匯入,
且 mypy 對同一檔案只解析出單一模組名(否則報 "found twice under different module names")。
"""
