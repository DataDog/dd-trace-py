"""
Module with various code patterns that generate code without line numbers.

Python 3.12+ may generate None line numbers for:
- Comprehensions
- Lambda functions
- Nested function definitions
- Generator expressions
"""


def test_comprehensions(items):
    """Test list/dict/set comprehensions which can generate synthetic opcodes."""
    # List comprehension
    doubled = [x * 2 for x in items]

    # Dict comprehension
    squared_dict = {x: x**2 for x in items}  # noqa: F841

    # Set comprehension
    unique_doubled = {x * 2 for x in items}  # noqa: F841

    # Nested comprehension
    nested = [[y * 2 for y in range(x)] for x in items[:3]]  # noqa: F841

    return doubled


def test_lambda(value):
    """Test lambda functions which may have synthetic opcodes."""
    # Simple lambda
    square = lambda x: x * x  # noqa: E731

    # Lambda with conditional
    safe_divide = lambda x, y: x / y if y != 0 else 0  # noqa: E731, F841

    # Lambda with complex expression
    complex_calc = lambda x: (x + 1) * (x + 2) * (x + 3) if x > 0 else 0  # noqa: E731, F841

    return square(value)


def test_nested_functions(value):
    """Test nested function definitions."""

    def outer(x):
        def inner(y):
            def innermost(z):
                return z * 3

            return innermost(y) + x

        return inner(x)

    result = outer(value)
    return result


def test_generator_expression():
    """Test generator expressions which may have synthetic opcodes."""
    # Simple generator
    gen = (x * 2 for x in range(10))

    # Generator with filter
    filtered_gen = (x * 2 for x in range(10) if x % 2 == 0)  # noqa: F841

    # Nested generator
    nested_gen = ((x, y) for x in range(3) for y in range(3))  # noqa: F841

    return list(gen)


def test_walrus_operator(items):
    """Test walrus operator (Python 3.8+) which may generate synthetic opcodes."""
    results = []

    # Walrus in comprehension
    filtered = [y for x in items if (y := x * 2) > 5]

    # Walrus in while loop
    while (n := len(results)) < 5:
        results.append(n)

    return filtered


class TestClass:
    """Class with various methods that may generate synthetic opcodes."""

    def __init__(self, value):
        self.value = value
        # Comprehension in __init__
        self.doubled_range = [x * 2 for x in range(value)]

    def method_with_lambda(self):
        """Method that uses lambda."""
        transform = lambda x: x * self.value  # noqa: E731, F841
        return [i * self.value for i in range(5)]

    @property
    def computed_property(self):
        """Property with comprehension."""
        return sum(x * 2 for x in range(self.value))


# Module-level comprehension (executed at import time)
MODULE_LEVEL_COMP = [x * 3 for x in range(5)]

# Module-level lambda
MODULE_LEVEL_LAMBDA = lambda x: x * 2  # noqa: E731

# Module-level generator
MODULE_LEVEL_GEN = (x for x in range(10))
