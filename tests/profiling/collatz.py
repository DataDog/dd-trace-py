from ddtrace.profiling import Profiler
from time import monotonic_ns
prof = Profiler(
    env="perf",
    service="collatz_py",
    version="1.8.3rc2",
    use_libdatadog=True,
    use_pyprof=False
)
prof.start()

funs = []
funlen = 1000;

def f000(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f001(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f002(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f003(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f004(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f005(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f006(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f007(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f008(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f009(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f010(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f011(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f012(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f013(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f014(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f015(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f016(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f017(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f018(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f019(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f020(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f021(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f022(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f023(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f024(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f025(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f026(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f027(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f028(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f029(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f030(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f031(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f032(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f033(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f034(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f035(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f036(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f037(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f038(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f039(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f040(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f041(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f042(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f043(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f044(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f045(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f046(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f047(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f048(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f049(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f050(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f051(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f052(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f053(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f054(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f055(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f056(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f057(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f058(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f059(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f060(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f061(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f062(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f063(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f064(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f065(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f066(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f067(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f068(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f069(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f070(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f071(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f072(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f073(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f074(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f075(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f076(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f077(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f078(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f079(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f080(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f081(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f082(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f083(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f084(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f085(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f086(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f087(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f088(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f089(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f090(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f091(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f092(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f093(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f094(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f095(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f096(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f097(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f098(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f099(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f100(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f101(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f102(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f103(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f104(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f105(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f106(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f107(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f108(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f109(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f110(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f111(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f112(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f113(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f114(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f115(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f116(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f117(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f118(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f119(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f120(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f121(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f122(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f123(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f124(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f125(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f126(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f127(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f128(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f129(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f130(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f131(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f132(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f133(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f134(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f135(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f136(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f137(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f138(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f139(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f140(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f141(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f142(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f143(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f144(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f145(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f146(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f147(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f148(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f149(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f150(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f151(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f152(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f153(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f154(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f155(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f156(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f157(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f158(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f159(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f160(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f161(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f162(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f163(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f164(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f165(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f166(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f167(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f168(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f169(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f170(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f171(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f172(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f173(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f174(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f175(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f176(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f177(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f178(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f179(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f180(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f181(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f182(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f183(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f184(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f185(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f186(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f187(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f188(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f189(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f190(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f191(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f192(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f193(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f194(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f195(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f196(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f197(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f198(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f199(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f200(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f201(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f202(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f203(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f204(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f205(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f206(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f207(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f208(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f209(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f210(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f211(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f212(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f213(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f214(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f215(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f216(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f217(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f218(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f219(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f220(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f221(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f222(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f223(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f224(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f225(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f226(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f227(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f228(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f229(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f230(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f231(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f232(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f233(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f234(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f235(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f236(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f237(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f238(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f239(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f240(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f241(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f242(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f243(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f244(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f245(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f246(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f247(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f248(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f249(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f250(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f251(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f252(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f253(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f254(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f255(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f256(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f257(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f258(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f259(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f260(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f261(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f262(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f263(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f264(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f265(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f266(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f267(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f268(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f269(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f270(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f271(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f272(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f273(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f274(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f275(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f276(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f277(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f278(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f279(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f280(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f281(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f282(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f283(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f284(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f285(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f286(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f287(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f288(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f289(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f290(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f291(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f292(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f293(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f294(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f295(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f296(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f297(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f298(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f299(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f300(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f301(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f302(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f303(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f304(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f305(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f306(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f307(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f308(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f309(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f310(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f311(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f312(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f313(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f314(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f315(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f316(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f317(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f318(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f319(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f320(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f321(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f322(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f323(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f324(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f325(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f326(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f327(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f328(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f329(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f330(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f331(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f332(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f333(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f334(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f335(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f336(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f337(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f338(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f339(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f340(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f341(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f342(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f343(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f344(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f345(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f346(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f347(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f348(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f349(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f350(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f351(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f352(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f353(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f354(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f355(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f356(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f357(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f358(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f359(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f360(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f361(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f362(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f363(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f364(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f365(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f366(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f367(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f368(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f369(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f370(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f371(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f372(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f373(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f374(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f375(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f376(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f377(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f378(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f379(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f380(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f381(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f382(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f383(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f384(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f385(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f386(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f387(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f388(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f389(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f390(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f391(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f392(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f393(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f394(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f395(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f396(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f397(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f398(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f399(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f400(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f401(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f402(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f403(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f404(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f405(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f406(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f407(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f408(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f409(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f410(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f411(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f412(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f413(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f414(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f415(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f416(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f417(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f418(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f419(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f420(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f421(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f422(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f423(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f424(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f425(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f426(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f427(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f428(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f429(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f430(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f431(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f432(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f433(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f434(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f435(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f436(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f437(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f438(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f439(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f440(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f441(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f442(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f443(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f444(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f445(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f446(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f447(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f448(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f449(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f450(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f451(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f452(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f453(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f454(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f455(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f456(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f457(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f458(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f459(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f460(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f461(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f462(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f463(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f464(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f465(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f466(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f467(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f468(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f469(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f470(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f471(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f472(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f473(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f474(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f475(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f476(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f477(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f478(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f479(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f480(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f481(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f482(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f483(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f484(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f485(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f486(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f487(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f488(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f489(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f490(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f491(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f492(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f493(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f494(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f495(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f496(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f497(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f498(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f499(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f500(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f501(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f502(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f503(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f504(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f505(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f506(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f507(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f508(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f509(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f510(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f511(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f512(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f513(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f514(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f515(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f516(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f517(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f518(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f519(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f520(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f521(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f522(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f523(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f524(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f525(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f526(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f527(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f528(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f529(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f530(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f531(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f532(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f533(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f534(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f535(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f536(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f537(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f538(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f539(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f540(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f541(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f542(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f543(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f544(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f545(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f546(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f547(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f548(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f549(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f550(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f551(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f552(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f553(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f554(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f555(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f556(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f557(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f558(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f559(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f560(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f561(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f562(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f563(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f564(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f565(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f566(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f567(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f568(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f569(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f570(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f571(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f572(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f573(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f574(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f575(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f576(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f577(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f578(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f579(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f580(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f581(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f582(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f583(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f584(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f585(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f586(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f587(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f588(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f589(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f590(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f591(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f592(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f593(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f594(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f595(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f596(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f597(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f598(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f599(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f600(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f601(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f602(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f603(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f604(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f605(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f606(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f607(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f608(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f609(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f610(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f611(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f612(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f613(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f614(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f615(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f616(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f617(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f618(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f619(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f620(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f621(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f622(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f623(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f624(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f625(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f626(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f627(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f628(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f629(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f630(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f631(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f632(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f633(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f634(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f635(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f636(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f637(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f638(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f639(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f640(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f641(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f642(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f643(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f644(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f645(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f646(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f647(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f648(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f649(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f650(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f651(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f652(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f653(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f654(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f655(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f656(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f657(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f658(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f659(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f660(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f661(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f662(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f663(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f664(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f665(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f666(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f667(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f668(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f669(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f670(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f671(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f672(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f673(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f674(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f675(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f676(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f677(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f678(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f679(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f680(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f681(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f682(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f683(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f684(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f685(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f686(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f687(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f688(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f689(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f690(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f691(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f692(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f693(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f694(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f695(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f696(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f697(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f698(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f699(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f700(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f701(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f702(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f703(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f704(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f705(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f706(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f707(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f708(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f709(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f710(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f711(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f712(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f713(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f714(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f715(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f716(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f717(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f718(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f719(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f720(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f721(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f722(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f723(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f724(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f725(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f726(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f727(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f728(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f729(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f730(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f731(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f732(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f733(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f734(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f735(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f736(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f737(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f738(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f739(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f740(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f741(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f742(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f743(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f744(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f745(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f746(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f747(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f748(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f749(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f750(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f751(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f752(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f753(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f754(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f755(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f756(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f757(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f758(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f759(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f760(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f761(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f762(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f763(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f764(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f765(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f766(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f767(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f768(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f769(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f770(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f771(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f772(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f773(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f774(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f775(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f776(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f777(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f778(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f779(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f780(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f781(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f782(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f783(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f784(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f785(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f786(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f787(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f788(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f789(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f790(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f791(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f792(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f793(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f794(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f795(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f796(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f797(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f798(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f799(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f800(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f801(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f802(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f803(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f804(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f805(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f806(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f807(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f808(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f809(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f810(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f811(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f812(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f813(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f814(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f815(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f816(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f817(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f818(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f819(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f820(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f821(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f822(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f823(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f824(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f825(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f826(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f827(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f828(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f829(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f830(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f831(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f832(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f833(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f834(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f835(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f836(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f837(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f838(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f839(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f840(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f841(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f842(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f843(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f844(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f845(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f846(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f847(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f848(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f849(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f850(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f851(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f852(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f853(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f854(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f855(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f856(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f857(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f858(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f859(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f860(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f861(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f862(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f863(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f864(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f865(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f866(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f867(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f868(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f869(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f870(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f871(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f872(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f873(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f874(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f875(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f876(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f877(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f878(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f879(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f880(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f881(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f882(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f883(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f884(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f885(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f886(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f887(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f888(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f889(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f890(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f891(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f892(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f893(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f894(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f895(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f896(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f897(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f898(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f899(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f900(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f901(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f902(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f903(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f904(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f905(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f906(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f907(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f908(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f909(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f910(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f911(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f912(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f913(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f914(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f915(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f916(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f917(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f918(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f919(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f920(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f921(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f922(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f923(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f924(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f925(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f926(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f927(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f928(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f929(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f930(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f931(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f932(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f933(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f934(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f935(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f936(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f937(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f938(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f939(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f940(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f941(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f942(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f943(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f944(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f945(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f946(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f947(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f948(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f949(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f950(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f951(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f952(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f953(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f954(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f955(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f956(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f957(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f958(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f959(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f960(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f961(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f962(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f963(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f964(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f965(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f966(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f967(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f968(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f969(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f970(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f971(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f972(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f973(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f974(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f975(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f976(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f977(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f978(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f979(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f980(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f981(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f982(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f983(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f984(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f985(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f986(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f987(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f988(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f989(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f990(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f991(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f992(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f993(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f994(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f995(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f996(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f997(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f998(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

def f999(n):
    m = int(n * 3 + 1 if n % 2 == 1 else n / 2)
    return 1 if 1 >= m else funs[m % funlen](m)

funs = [f000, f001, f002, f003, f004, f005, f006, f007, f008, f009, f010, f011, f012, f013, f014, f015, f016, f017, f018, f019, f020, f021, f022, f023, f024, f025, f026, f027, f028, f029, f030, f031, f032, f033, f034, f035, f036, f037, f038, f039, f040, f041, f042, f043, f044, f045, f046, f047, f048, f049, f050, f051, f052, f053, f054, f055, f056, f057, f058, f059, f060, f061, f062, f063, f064, f065, f066, f067, f068, f069, f070, f071, f072, f073, f074, f075, f076, f077, f078, f079, f080, f081, f082, f083, f084, f085, f086, f087, f088, f089, f090, f091, f092, f093, f094, f095, f096, f097, f098, f099, f100, f101, f102, f103, f104, f105, f106, f107, f108, f109, f110, f111, f112, f113, f114, f115, f116, f117, f118, f119, f120, f121, f122, f123, f124, f125, f126, f127, f128, f129, f130, f131, f132, f133, f134, f135, f136, f137, f138, f139, f140, f141, f142, f143, f144, f145, f146, f147, f148, f149, f150, f151, f152, f153, f154, f155, f156, f157, f158, f159, f160, f161, f162, f163, f164, f165, f166, f167, f168, f169, f170, f171, f172, f173, f174, f175, f176, f177, f178, f179, f180, f181, f182, f183, f184, f185, f186, f187, f188, f189, f190, f191, f192, f193, f194, f195, f196, f197, f198, f199, f200, f201, f202, f203, f204, f205, f206, f207, f208, f209, f210, f211, f212, f213, f214, f215, f216, f217, f218, f219, f220, f221, f222, f223, f224, f225, f226, f227, f228, f229, f230, f231, f232, f233, f234, f235, f236, f237, f238, f239, f240, f241, f242, f243, f244, f245, f246, f247, f248, f249, f250, f251, f252, f253, f254, f255, f256, f257, f258, f259, f260, f261, f262, f263, f264, f265, f266, f267, f268, f269, f270, f271, f272, f273, f274, f275, f276, f277, f278, f279, f280, f281, f282, f283, f284, f285, f286, f287, f288, f289, f290, f291, f292, f293, f294, f295, f296, f297, f298, f299, f300, f301, f302, f303, f304, f305, f306, f307, f308, f309, f310, f311, f312, f313, f314, f315, f316, f317, f318, f319, f320, f321, f322, f323, f324, f325, f326, f327, f328, f329, f330, f331, f332, f333, f334, f335, f336, f337, f338, f339, f340, f341, f342, f343, f344, f345, f346, f347, f348, f349, f350, f351, f352, f353, f354, f355, f356, f357, f358, f359, f360, f361, f362, f363, f364, f365, f366, f367, f368, f369, f370, f371, f372, f373, f374, f375, f376, f377, f378, f379, f380, f381, f382, f383, f384, f385, f386, f387, f388, f389, f390, f391, f392, f393, f394, f395, f396, f397, f398, f399, f400, f401, f402, f403, f404, f405, f406, f407, f408, f409, f410, f411, f412, f413, f414, f415, f416, f417, f418, f419, f420, f421, f422, f423, f424, f425, f426, f427, f428, f429, f430, f431, f432, f433, f434, f435, f436, f437, f438, f439, f440, f441, f442, f443, f444, f445, f446, f447, f448, f449, f450, f451, f452, f453, f454, f455, f456, f457, f458, f459, f460, f461, f462, f463, f464, f465, f466, f467, f468, f469, f470, f471, f472, f473, f474, f475, f476, f477, f478, f479, f480, f481, f482, f483, f484, f485, f486, f487, f488, f489, f490, f491, f492, f493, f494, f495, f496, f497, f498, f499, f500, f501, f502, f503, f504, f505, f506, f507, f508, f509, f510, f511, f512, f513, f514, f515, f516, f517, f518, f519, f520, f521, f522, f523, f524, f525, f526, f527, f528, f529, f530, f531, f532, f533, f534, f535, f536, f537, f538, f539, f540, f541, f542, f543, f544, f545, f546, f547, f548, f549, f550, f551, f552, f553, f554, f555, f556, f557, f558, f559, f560, f561, f562, f563, f564, f565, f566, f567, f568, f569, f570, f571, f572, f573, f574, f575, f576, f577, f578, f579, f580, f581, f582, f583, f584, f585, f586, f587, f588, f589, f590, f591, f592, f593, f594, f595, f596, f597, f598, f599, f600, f601, f602, f603, f604, f605, f606, f607, f608, f609, f610, f611, f612, f613, f614, f615, f616, f617, f618, f619, f620, f621, f622, f623, f624, f625, f626, f627, f628, f629, f630, f631, f632, f633, f634, f635, f636, f637, f638, f639, f640, f641, f642, f643, f644, f645, f646, f647, f648, f649, f650, f651, f652, f653, f654, f655, f656, f657, f658, f659, f660, f661, f662, f663, f664, f665, f666, f667, f668, f669, f670, f671, f672, f673, f674, f675, f676, f677, f678, f679, f680, f681, f682, f683, f684, f685, f686, f687, f688, f689, f690, f691, f692, f693, f694, f695, f696, f697, f698, f699, f700, f701, f702, f703, f704, f705, f706, f707, f708, f709, f710, f711, f712, f713, f714, f715, f716, f717, f718, f719, f720, f721, f722, f723, f724, f725, f726, f727, f728, f729, f730, f731, f732, f733, f734, f735, f736, f737, f738, f739, f740, f741, f742, f743, f744, f745, f746, f747, f748, f749, f750, f751, f752, f753, f754, f755, f756, f757, f758, f759, f760, f761, f762, f763, f764, f765, f766, f767, f768, f769, f770, f771, f772, f773, f774, f775, f776, f777, f778, f779, f780, f781, f782, f783, f784, f785, f786, f787, f788, f789, f790, f791, f792, f793, f794, f795, f796, f797, f798, f799, f800, f801, f802, f803, f804, f805, f806, f807, f808, f809, f810, f811, f812, f813, f814, f815, f816, f817, f818, f819, f820, f821, f822, f823, f824, f825, f826, f827, f828, f829, f830, f831, f832, f833, f834, f835, f836, f837, f838, f839, f840, f841, f842, f843, f844, f845, f846, f847, f848, f849, f850, f851, f852, f853, f854, f855, f856, f857, f858, f859, f860, f861, f862, f863, f864, f865, f866, f867, f868, f869, f870, f871, f872, f873, f874, f875, f876, f877, f878, f879, f880, f881, f882, f883, f884, f885, f886, f887, f888, f889, f890, f891, f892, f893, f894, f895, f896, f897, f898, f899, f900, f901, f902, f903, f904, f905, f906, f907, f908, f909, f910, f911, f912, f913, f914, f915, f916, f917, f918, f919, f920, f921, f922, f923, f924, f925, f926, f927, f928, f929, f930, f931, f932, f933, f934, f935, f936, f937, f938, f939, f940, f941, f942, f943, f944, f945, f946, f947, f948, f949, f950, f951, f952, f953, f954, f955, f956, f957, f958, f959, f960, f961, f962, f963, f964, f965, f966, f967, f968, f969, f970, f971, f972, f973, f974, f975, f976, f977, f978, f979, f980, f981, f982, f983, f984, f985, f986, f987, f988, f989, f990, f991, f992, f993, f994, f995, f996, f997, f998, f999]

# The number with the highest collatz delay less than 512 is 626331
# http://www.ericr.nl/wondrous/delrecs.html
i = 0
j = 0
last_time = monotonic_ns()
print(f"iter, walltime")
while True:
  f000(i)
  i += 1
  if i > 626331:
    now = monotonic_ns()
    wall_time = now - last_time
    last_time = now
    i = 1
    j += 1
    print(f"{j}, {wall_time}")
