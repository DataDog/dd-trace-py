def mean: reduce .[] as $n (0; . + $n) / length;
def pow2: . * .;
def variance: . | mean as $mean | map_values(. - $mean | pow2) | mean;
def stdev: . | variance | sqrt;
def percentile(p):
    sort
    | . as $N
    | ((length - 1) * p) as $k
    | ($k | floor) as $f
    | ($k | ceil) as $c
    | if ($f == $c) then $N[$k] else (($N[$f] * ($c-$k)) + ($N[$f] * ($k-$f))) end;
def summary:
    {"mean": mean,
     "stdev": stdev,
     "p50": percentile(0.50),
     "p90": percentile(0.95),
     "p99": percentile(0.99)};
