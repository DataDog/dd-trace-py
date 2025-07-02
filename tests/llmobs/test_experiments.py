

def test_dataset_pull(llmobs):
    dataset = llmobs.pull_dataset(name="kyle-test")
    assert dataset._id == "929531d1-3cd2-473d-ab4e-2423b40c5db5"
