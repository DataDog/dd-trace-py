import json
import os


def main():
    artifacts_dir = "../../../artifacts"
    # find the subdir in artifacts_dir with the latest timestamp
    subdirs = [d for d in os.listdir(artifacts_dir) if os.path.isdir(os.path.join(artifacts_dir, d))]
    latest_subdir = max(subdirs, key=lambda d: os.path.getmtime(os.path.join(artifacts_dir, d)))
    latest_subdir_path = os.path.join(artifacts_dir, latest_subdir)
    print(latest_subdir_path)
    aspects_subdir = os.path.join(latest_subdir_path, "appsec_iast_aspects")
    versions = [d for d in os.listdir(aspects_subdir) if os.path.isdir(os.path.join(aspects_subdir, d))]
    print(versions)

    data_dict = {}

    for version in versions:
        version_dir = os.path.join(aspects_subdir, version)
        with open(os.path.join(version_dir, "results.json")) as f:
            data = json.load(f)
            for item in data["benchmarks"]:
                name = item["metadata"]["name"]

                sum_time = 0.0
                count = 0

                for run in item["runs"]:
                    for value in run["values"]:
                        sum_time += float(value)
                        count += 1

                avg_time = sum_time / count
                if name not in data_dict:
                    data_dict[name] = {}

                # time is in seconds, convert to nanoseconds
                data_dict[name][version] = avg_time * 1e9

    for aspect in data_dict:
        if "noaspect" in aspect:
            continue

        try:
            print(
                "{}: \n\t{}: {}\n\t{}: {}".format(
                    aspect, versions[0], data_dict[aspect][versions[0]], versions[1], data_dict[aspect][versions[1]]
                )
            )
        except KeyError:
            pass


if __name__ == "__main__":
    main()
