import subprocess

import yaml


def get_changed_files():
    """Get the list of files changed in the latest commit."""
    result = subprocess.run(["git", "diff", "--name-only", "HEAD~1"], stdout=subprocess.PIPE)
    return result.stdout.decode("utf-8").splitlines()


def load_all_jobs(job_file):
    """Load all job definitions from a single YAML file."""
    with open(job_file, "r") as f:
        return yaml.safe_load(f)


def generate_pipeline():
    changed_files = get_changed_files()

    # Load all job definitions from the jobs.yml file once
    all_jobs = load_all_jobs("jobs.yml")
    pipeline_jobs = {}

    # Check which files have changed and select corresponding jobs
    if any(file.startswith("path/to/file1") for file in changed_files):
        print("file1 changed, adding job1")
        pipeline_jobs["job1"] = all_jobs["job1"]  # Add job1 to pipeline

    if any(file.startswith("path/to/file2") for file in changed_files):
        print("file2 changed, adding job2")
        pipeline_jobs["job2"] = all_jobs["job2"]  # Add job2 to pipeline

    if any(file.startswith("path/to/file3") for file in changed_files):
        print("file3 changed, adding job3")
        pipeline_jobs["job3"] = all_jobs["job3"]  # Add job3 to pipeline

    # Write the combined job definitions to child-pipeline.yml
    with open("child-pipeline.yml", "w") as f:
        yaml.dump(pipeline_jobs, f)


if __name__ == "__main__":
    generate_pipeline()
