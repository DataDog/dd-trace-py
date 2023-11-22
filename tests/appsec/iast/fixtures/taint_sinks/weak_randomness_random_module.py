"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import random as random_2


def random_random():
    # label weak_randomness_random
    result = random_2.random()
    return result


def random_randint():
    # label weak_randomness_randint
    result = random_2.randint(1, 10)
    return result


def random_randrange():
    # label weak_randomness_randrange
    result = random_2.randrange(1, 10)
    return result


def random_choice():
    # label weak_randomness_choice
    result = random_2.choice([1, 10])
    return result


def random_shuffle():
    # label weak_randomness_shuffle
    result = random_2.shuffle([1, 10])
    return result


def random_betavariate():
    # label weak_randomness_betavariate
    result = random_2.betavariate(10.00, beta=1)
    return result


def random_gammavariate():
    # label weak_randomness_gammavariate
    result = random_2.gammavariate(10.00, beta=1)
    return result


def random_expovariate():
    # label weak_randomness_expovariate
    result = random_2.expovariate(10.00)
    return result


def random_choices():
    # label weak_randomness_choices
    result = random_2.choices([1, 10])
    return result


def random_gauss():
    # label weak_randomness_gauss
    result = random_2.gauss(10.00, sigma=1)
    return result


def random_uniform():
    # label weak_randomness_uniform
    result = random_2.uniform(10.00, b=1)
    return result


def random_lognormvariate():
    # label weak_randomness_lognormvariate
    result = random_2.lognormvariate(10.00, sigma=10)
    return result


def random_normalvariate():
    # label weak_randomness_normalvariate
    result = random_2.normalvariate(10.00, sigma=10)
    return result


def random_paretovariate():
    # label weak_randomness_paretovariate
    result = random_2.paretovariate(10.00)
    return result


def random_sample():
    # label weak_randomness_sample
    result = random_2.sample([1, 2, 3], 1)
    return result


def random_triangular():
    # label weak_randomness_triangular
    result = random_2.triangular(1, 1)
    return result


def random_vonmisesvariate():
    # label weak_randomness_vonmisesvariate
    result = random_2.vonmisesvariate(1, 1)
    return result


def random_weibullvariate():
    # label weak_randomness_weibullvariate
    result = random_2.weibullvariate(1, 1)
    return result


def random_randbytes():
    # label weak_randomness_randbytes
    result = random_2.randbytes(44)
    return result
