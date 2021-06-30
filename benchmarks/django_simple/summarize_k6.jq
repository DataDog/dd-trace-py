include "util";
[.[] |
    select(.type=="Point" and .metric == "http_req_duration" and .data.tags.status >= "200")] |
    group_by(.data.tags.sirun_variant) |
    map({"variant": .[0].data.tags.sirun_variant,
         "count": [.[] | .data.value] | length,
         "http_req_duration": [.[] | .data.value] | summary,
         })
