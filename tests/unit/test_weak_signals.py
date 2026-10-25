def test_a_few_topics_on_a_quiet_forum_are_not_a_buzz():
    """25/10/2026: two topics in three days on a forum with none usually read « z 3.1 » with the Wikipedia
    dispersion floor — four values had exactly that z."""
    from app.domain.weak_signals import RadarParams, robust_z, social_z

    p = RadarParams()
    quiet = [0.0] * 60
    assert round(robust_z(quiet + [1, 1, 0], p), 1) == 3.1  # the old reading
    assert social_z(quiet + [1, 1, 0], p) is None  # 2 topics < 5: no anomaly read
    z = social_z(quiet + [3, 2, 2], p)
    assert z is not None and z > p.social_z  # 7 new topics in 3 days where there are none: a real burst
    busy = [4.0, 6.0, 5.0, 3.0] * 15
    assert social_z(busy + [5, 4, 6], p) < 1  # an active forum at its usual pace
