#include "exporter.hpp"

#include <vector>
#include <iostream>
#include <string>
#include <string_view>

int main() {
  Datadog::Profile profile{};
  Datadog::Uploader uploader{};

  profile.start_sample();
  profile.push_cputime(1000, 1);
  profile.push_walltime(10000, 1);
  profile.push_threadinfo(1, 1024, "main thread");

  profile.push_frame("foo", "foo.py", 0, 1);
  profile.push_frame("bar", "foo.py", 0, 1);
  profile.push_frame("baz", "foo.py", 0, 1);

  profile.flush_sample();

  uploader.upload(&profile);
  return 0;
}
