# libdatadog Version Check

Determine whether the libdatadog dependency in this repo is out of date.

Steps:

1. Read `src/native/Cargo.toml` and find the libdatadog rev currently pinned
   (the `rev = "..."` on the github.com/DataDog/libdatadog deps); it may be a
   release tag like v35.0.0 or a 40-character commit SHA.
2. Run
   `curl -s https://api.github.com/repos/DataDog/libdatadog/compare/<rev>...main`,
   substituting the pinned rev for `<rev>`. In the JSON, `ahead_by` is how
   many commits main is ahead of the current pin (i.e. how many commits behind
   the pin is), and `base_commit.sha` is the commit the pin resolves to.
3. Report in one short paragraph: the current pinned rev, the commit SHA it
   resolves to, and how many commits behind main it is.
4. If the pin is MORE THAN 5 commits behind main, end with the exact line:
   "UPDATE NEEDED: libdatadog is N commits behind main." (substitute N).
   Otherwise end with: "OK: libdatadog is within 5 commits of main."
