"""Token 估算(PHASE_2 §7;**全專案唯一實作**)。

`estimate_tokens(text) = cjk字元數 + ceil(非cjk字元數 / 4)`。
確定性、零外部相依;**NEVER 引入 tiktoken**(§F.5、§G.1):估算只用於切塊大小與
預算控制,不需要與任一 tokenizer 位元級一致,引入外部詞表反而綁死模型。
"""
import math
import re

# CJK 標點、假名、漢字(含擴充 A / 相容區)、全形符號、諺文——一字約一 token。
_CJK = re.compile(
    r"[　-〿぀-ヿ㐀-䶿一-鿿"
    r"가-힯豈-﫿＀-￯]"
)


def estimate_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 4)
