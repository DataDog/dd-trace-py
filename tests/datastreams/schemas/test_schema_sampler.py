from ddtrace.internal.datastreams.schemas.schema_sampler import SchemaSampler


def test_schema_sampler_samples_with_correct_weights():
    currentTimeMillis = 100000
    sampler = SchemaSampler()

    can_sample1 = sampler.can_sample(currentTimeMillis)
    weight1 = sampler.try_sample(currentTimeMillis)

    can_sample2 = sampler.can_sample(currentTimeMillis + 1000)
    weight2 = sampler.try_sample(currentTimeMillis + 1000)

    can_sample3 = sampler.can_sample(currentTimeMillis + 2000)
    weight3 = sampler.try_sample(currentTimeMillis + 2000)

    can_sample4 = sampler.can_sample(currentTimeMillis + 30000)
    weight4 = sampler.try_sample(currentTimeMillis + 30000)

    can_sample5 = sampler.can_sample(currentTimeMillis + 30001)
    weight5 = sampler.try_sample(currentTimeMillis + 30001)

    assert can_sample1
    assert weight1 == 1
    assert not can_sample2
    assert weight2 == 0
    assert not can_sample3
    assert weight3 == 0
    assert can_sample4
    assert weight4 == 3
    assert not can_sample5
    assert weight5 == 0
