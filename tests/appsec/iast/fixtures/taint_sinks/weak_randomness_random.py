"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import random as random_module
from random import betavariate
from random import choice
from random import choices
from random import expovariate
from random import gammavariate
from random import gauss
from random import lognormvariate
from random import normalvariate
from random import paretovariate
from random import randbytes
from random import randint
from random import random
from random import randrange
from random import sample
from random import shuffle
from random import triangular
from random import uniform
from random import vonmisesvariate
from random import weibullvariate


def random_random():
    # label weak_randomness_random
    result = random()
    return result


def random_randint():
    # label weak_randomness_randint
    result = randint(1, 10)
    return result


def random_randrange():
    # label weak_randomness_randrange
    result = randrange(1, 10)
    return result


def random_choice():
    # label weak_randomness_choice
    result = choice([1, 10])
    return result


def random_shuffle():
    # label weak_randomness_shuffle
    result = shuffle([1, 10])
    return result


def random_betavariate():
    # label weak_randomness_betavariate
    result = betavariate(10.00, beta=1)
    return result


def random_gammavariate():
    # label weak_randomness_gammavariate
    result = gammavariate(10.00, beta=1)
    return result


def random_expovariate():
    # label weak_randomness_expovariate
    result = expovariate(10.00)
    return result


def random_choices():
    # label weak_randomness_choices
    result = choices([1, 10])
    return result


def random_gauss():
    # label weak_randomness_gauss
    result = gauss(10.00, sigma=1)
    return result


def random_uniform():
    # label weak_randomness_uniform
    result = uniform(10.00, b=1)
    return result


def random_lognormvariate():
    # label weak_randomness_lognormvariate
    result = lognormvariate(10.00, sigma=10)
    return result


def random_normalvariate():
    # label weak_randomness_normalvariate
    result = normalvariate(10.00, sigma=10)
    return result


def random_paretovariate():
    # label weak_randomness_paretovariate
    result = paretovariate(10.00)
    return result


def random_sample():
    # label weak_randomness_sample
    result = sample([1, 2, 3], 1)
    return result


def random_triangular():
    # label weak_randomness_triangular
    result = triangular(1, 1)
    return result


def random_vonmisesvariate():
    # label weak_randomness_vonmisesvariate
    result = vonmisesvariate(1, 1)
    return result


def random_weibullvariate():
    # label weak_randomness_weibullvariate
    result = weibullvariate(1, 1)
    return result


def random_randbytes():
    # label weak_randomness_randbytes
    result = randbytes(44)
    return result


def random_dynamic_import():
    # label weak_randomness_dynamic_import
    result = getattr(random_module, "random")()  # noqa: B009
    return result
