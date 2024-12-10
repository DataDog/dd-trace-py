import ast
from typing import Any
from typing import Generator
from typing import List
from typing import Tuple
from typing import Type


"""
python -m astpretty --no ../ddtrace/contrib/internal/aws_lambda/patch.py | less
"""


class Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: List[Tuple[int, int]] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if hasattr(node.value, "id") and node.value.id == "os" and node.attr in ("environ", "getenv"):
            self.problems.append((node.lineno, node.col_offset))

        self.generic_visit(node)


class Plugin:
    def __init__(self, tree: ast.AST) -> None:
        self._tree = tree

    def run(self) -> Generator[Tuple[int, int, str, Type[Any]], None, None]:
        visitor = Visitor()
        visitor.visit(self._tree)
        for line, col in visitor.problems:
            yield line, col, "DDC001 Usage of os.environ is prohibited outside of configuration", type(self)
