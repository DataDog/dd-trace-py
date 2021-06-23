include "util";
. |
    group_by(.variant) |
    map({"variant": .[0].variant,
         "count": [.[] | .iterations[]] | length,
         "system.time": [.[] | .iterations[] | ."system.time"] | summary,
         "user.time": [.[] | .iterations[] | ."user.time"] | summary,
         "wall.time": [.[] | .iterations[] | ."wall.time"] | summary,
         "cpu.pct.wall.time": [.[] | .iterations[] | ."cpu.pct.wall.time"] | summary,
         "max.res.size": [.[] | .iterations[] | ."max.res.size"] | summary
        })
