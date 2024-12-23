from tasks import fn_a
from tasks import fn_b


(fn_a.si() | fn_b.si()).delay()
